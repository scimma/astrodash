"""Record usage events per request and write them after the response.

A view calls :func:`record` at the end of a success path. The event is held in
a buffer belonging to the current request and written to InfluxDB in one call
when Django sends ``request_finished``, which a WSGI server does after the
response has been delivered. So a slow or absent InfluxDB never delays or
fails the visitor's request: the write is bounded, never retried, and a
failure only drops that request's events and logs how many.

The buffer is thread-local, which under the clusters' synchronous gunicorn
workers is one buffer per request in flight. It is emptied on
``request_started`` so that events stranded by a request whose flush never ran
cannot ride along with the next one. Nothing here touches the ORM or starts a
thread.
"""

import functools
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from astrodash.core.usage.config import UsageConfig, recording_state
from astrodash.infrastructure.usage import USAGE_LOGGER_NAME
from astrodash.infrastructure.usage.influx_client import InfluxClient
from astrodash.infrastructure.usage.line_protocol import (
    Point,
    TimestampSource,
    encode_points,
)

logger = logging.getLogger(USAGE_LOGGER_NAME)

EVENT_CLASSIFICATION = "classification"
EVENT_TWINS_SELECTION = "twins_selection"
EVENT_TWINS_SEARCH = "twins_search"

SURFACE_UI = "ui"
SURFACE_API = "api"

SOURCE_BY_NAME = "by_name"
SOURCE_UPLOAD = "upload"

_buffer = threading.local()


@dataclass
class _Recorder:
    """The process-wide pieces recording needs once it is on."""

    config: UsageConfig
    client: InfluxClient
    timestamps: TimestampSource


@functools.lru_cache(maxsize=1)
def _recorder() -> Optional[_Recorder]:
    """Build the recorder once per process, or ``None`` when recording is off."""
    state = recording_state()
    if not state.enabled:
        return None
    config = state.config
    client = InfluxClient(
        url=config.url,
        database=config.database,
        username=config.username,
        password=config.password,
        retention_policy=config.retention_policy,
        cert_pair=config.cert_pair,
        connect_timeout=config.connect_timeout,
        read_timeout=config.read_timeout,
        pause_seconds=config.pause_seconds,
    )
    return _Recorder(config=config, client=client, timestamps=TimestampSource())


def record(request: Any, event: str, surface: str, fields: Mapping[str, Any]) -> None:
    """Add one usage event to the current request's buffer.

    Does nothing when recording is off.

    Args:
        request: The request that produced the event.
        event: One of the ``EVENT_*`` kinds.
        surface: :data:`SURFACE_UI` or :data:`SURFACE_API`.
        fields: Field values, named as in the line-protocol field table.
    """
    recorder = _recorder()
    if recorder is None:
        return
    tags = {
        "environment": recorder.config.environment,
        "event": event,
        "surface": surface,
        "identified": "no",
    }
    point = Point(
        tags=tags,
        fields={**fields, "count": 1},
        timestamp_ns=recorder.timestamps.next_ns(),
    )
    points = getattr(_buffer, "points", None)
    if points is None:
        points = _buffer.points = []
    points.append(point)


def start_request(**kwargs: Any) -> None:
    """Empty this thread's buffer; connected to ``request_started``."""
    _buffer.points = None


def flush_request(**kwargs: Any) -> None:
    """Write this thread's buffered events; connected to ``request_finished``.

    Never raises: a failure drops the events and logs how many.
    """
    points = getattr(_buffer, "points", None)
    _buffer.points = None
    if not points:
        return
    try:
        _recorder().client.write(encode_points(points))
    except Exception as exc:  # a receiver must not fail the response
        logger.warning(
            "Dropped %d usage events: %s while writing",
            len(points),
            type(exc).__name__,
        )


# REST ``params`` keys and the event fields they are recorded under. The whole
# object is also kept as one JSON string, so an unrecognized key is not lost.
_API_PARAM_FIELDS = {
    "objectName": "object_name",
    "smoothing": "smoothing",
    "minWave": "min_wave",
    "maxWave": "max_wave",
    "knownZ": "known_z",
    "zValue": "redshift",
    "calculateRlap": "calculate_rlap",
}


def api_parameter_fields(params: Mapping[str, Any]) -> dict:
    """Turn a REST call's ``params`` object into event fields.

    Args:
        params: The parsed ``params`` JSON the caller submitted.

    Returns:
        Each recognized parameter under its field name, plus the whole object
        as one JSON string.
    """
    fields = {
        field: params[key] for key, field in _API_PARAM_FIELDS.items() if key in params
    }
    fields["params_json"] = json.dumps(params, sort_keys=True, default=str)
    return fields


def record_classification(
    request: Any,
    *,
    surface: str,
    model_type: str,
    model_id: Optional[str],
    source: str,
    batch: bool,
    submitted: Mapping[str, Any],
    designation: Optional[str] = None,
    filename: Optional[str] = None,
) -> None:
    """Record one successful classification.

    Args:
        request: The request that ran it.
        surface: :data:`SURFACE_UI` or :data:`SURFACE_API`.
        model_type: The model type that produced the result, as the factory
            names it (``"user_uploaded"`` for an uploaded model).
        model_id: The uploaded model's ID for an uploaded model; otherwise
            ignored, and the built-in model's ID is the model type.
        source: :data:`SOURCE_BY_NAME` or :data:`SOURCE_UPLOAD`.
        batch: Whether the spectrum came as part of a batch.
        submitted: The parameters as submitted, under their field names.
            Never the uploaded file's contents.
        designation: The designation a by-name lookup resolved to.
        filename: The uploaded file's name.
    """
    fields = dict(submitted)
    fields.update(
        model_type=model_type,
        model_id=model_id if model_type == "user_uploaded" and model_id else model_type,
        source=source,
        batch=batch,
        designation=designation,
        filename=filename,
    )
    record(request, EVENT_CLASSIFICATION, surface, fields)


def record_batch_classifications(
    request: Any,
    results: Mapping[str, Any],
    *,
    surface: str,
    model_type: str,
    model_id: Optional[str],
    submitted: Mapping[str, Any],
) -> None:
    """Record one event per spectrum of a batch that classified.

    Args:
        request: The request that ran the batch.
        results: The batch service's results, keyed by filename; an entry
            that is not a mapping or that carries an ``error`` is skipped.
        surface: :data:`SURFACE_UI` or :data:`SURFACE_API`.
        model_type: The model type the batch ran.
        model_id: The uploaded model's ID, for an uploaded model.
        submitted: The batch's submitted parameters, under their field names.
    """
    for filename, result in results.items():
        if not isinstance(result, Mapping) or result.get("error"):
            continue
        record_classification(
            request,
            surface=surface,
            model_type=model_type,
            model_id=model_id,
            source=SOURCE_UPLOAD,
            batch=True,
            submitted=submitted,
            filename=filename,
        )


def record_twins_search(request: Any, designation: Optional[str]) -> None:
    """Record one FIND TWINS search on a classified spectrum.

    Args:
        request: The request that ran the search.
        designation: The designation of a spectrum classified by name, or
            ``None`` for an uploaded spectrum, which is flagged instead.
    """
    fields = {"designation": designation} if designation else {"uploaded": True}
    record(request, EVENT_TWINS_SEARCH, SURFACE_UI, fields)


def record_twins_selection(request: Any, dataset: str, designation: str) -> None:
    """Record one object a visitor selected in the public twins explorer.

    Args:
        request: The report request from the explorer page.
        dataset: The explorer dataset the object was selected in.
        designation: The selected object's name as the explorer shows it.
    """
    record(
        request,
        EVENT_TWINS_SELECTION,
        SURFACE_UI,
        {"dataset": dataset, "designation": designation},
    )
