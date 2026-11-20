"""Encode usage events as InfluxDB 1.x line protocol.

Every usage event is one point in one measurement. Tags carry the small set of
values the report groups and deletes by; everything else is a field.

InfluxDB fixes a field's type the first time the field is written to a shard,
and rejects a later point that sends another type for it. So the type of each
field comes from :data:`FIELD_TYPES`, never from the value: a value is coerced
to its declared type, and one that does not fit is left out rather than
allowed to change the field's type or fail the whole write.

Points that share a measurement, tag set and timestamp are merged by
InfluxDB, so :class:`TimestampSource` hands out strictly increasing
nanosecond timestamps within a process.
"""

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Mapping, Optional

MEASUREMENT = "astrodash_usage"

# The declared type of every field any usage event may carry. Numeric form
# parameters are floats even where the form takes an integer, because the REST
# API accepts either and a field cannot hold both.
FIELD_TYPES = {
    # Every event carries count=1, so that a sum over any field set works.
    "count": int,
    # Classification
    "model_type": str,
    "model_id": str,
    "source": str,
    "batch": bool,
    "object_name": str,
    "designation": str,
    "filename": str,
    "smoothing": float,
    "min_wave": float,
    "max_wave": float,
    "known_z": bool,
    "redshift": float,
    "redshift_text": str,
    "calculate_rlap": bool,
    "source_spectrum_id": str,
    "params_json": str,
    # Twins
    "dataset": str,
    "uploaded": bool,
    # Identity, on identified events only
    "country": str,
    "network_owner": str,
}

_TRUE_STRINGS = frozenset({"true", "on", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "off", "no", "0"})


@dataclass(frozen=True)
class Point:
    """One usage event ready to encode.

    Attributes:
        tags: Tag keys to string values. Empty values are omitted.
        fields: Field names from :data:`FIELD_TYPES` to raw values.
        timestamp_ns: Nanoseconds since the epoch.
    """

    tags: Mapping[str, str]
    fields: Mapping[str, Any]
    timestamp_ns: int


class TimestampSource:
    """Hand out strictly increasing nanosecond timestamps within a process."""

    def __init__(self, clock_ns: Callable[[], int] = time.time_ns) -> None:
        """Build the source.

        Args:
            clock_ns: Wall clock in nanoseconds since the epoch.
        """
        self._clock_ns = clock_ns
        self._last = 0
        self._lock = threading.Lock()

    def next_ns(self) -> int:
        """Return the wall-clock time, or one past the last stamp if later.

        Returns:
            A timestamp greater than every one returned before it.
        """
        with self._lock:
            self._last = max(self._clock_ns(), self._last + 1)
            return self._last


def _one_line(text: str) -> str:
    """Replace line breaks with spaces, since a point is one line."""
    return text.replace("\r", " ").replace("\n", " ")


def _escape_key(text: str) -> str:
    """Escape a measurement, tag key, tag value or field key."""
    text = _one_line(text)
    return text.replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def _escape_string_value(text: str) -> str:
    """Escape a string field value, without its surrounding quotes."""
    return _one_line(text).replace("\\", "\\\\").replace('"', '\\"')


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _as_str(value: Any) -> Optional[str]:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _encode_field_value(name: str, value: Any) -> Optional[str]:
    """Encode one field's value per its declared type, or ``None`` to omit it."""
    declared = FIELD_TYPES.get(name)
    if declared is None or value is None or value == "":
        return None
    if declared is bool:
        coerced = _as_bool(value)
        return None if coerced is None else ("true" if coerced else "false")
    if declared is int:
        coerced = _as_int(value)
        return None if coerced is None else f"{coerced}i"
    if declared is float:
        coerced = _as_float(value)
        return None if coerced is None else repr(coerced)
    coerced = _as_str(value)
    if not coerced:
        return None
    return f'"{_escape_string_value(coerced)}"'


def encode_point(point: Point) -> Optional[str]:
    """Encode one point as a line of line protocol.

    Args:
        point: The event to encode.

    Returns:
        The line, without a trailing newline, or ``None`` when no field
        survives coercion, since a point must carry at least one field.
    """
    tags = "".join(
        f",{_escape_key(key)}={_escape_key(str(value))}"
        for key, value in sorted(point.tags.items())
        if value not in (None, "")
    )
    fields = []
    for name in sorted(point.fields):
        encoded = _encode_field_value(name, point.fields[name])
        if encoded is not None:
            fields.append(f"{_escape_key(name)}={encoded}")
    if not fields:
        return None
    return f"{_escape_key(MEASUREMENT)}{tags} {','.join(fields)} {point.timestamp_ns}"


def encode_points(points: Iterable[Point]) -> List[str]:
    """Encode points, dropping any that carry no usable field.

    Args:
        points: The events to encode.

    Returns:
        One line per encodable point, in order.
    """
    lines = (encode_point(point) for point in points)
    return [line for line in lines if line is not None]
