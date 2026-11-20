"""Shared helpers for usage-recording tests.

``recording_on`` turns recording on with a fake writer in place of the
InfluxDB client, and ``parse_line`` reads a written line back into its tags and
typed fields so a test can assert on what InfluxDB would store.
"""

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple
from unittest import mock

from astrodash.core.usage import events

FULL_ENV = {
    "ASTRODASH_USAGE_INFLUX_URL": "https://influx.example.org:8086",
    "ASTRODASH_USAGE_INFLUX_DATABASE": "astrodash_usage",
    "ASTRODASH_USAGE_INFLUX_USERNAME": "writer",
    "ASTRODASH_USAGE_INFLUX_PASSWORD": "pw",
    "ASTRODASH_USAGE_ENVIRONMENT": "dev",
}


class FakeWriter:
    """Stands in for ``InfluxClient`` and keeps every line it is given."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.writes: List[List[str]] = []
        self.succeed = True

    def write(self, lines: List[str]) -> bool:
        self.writes.append(list(lines))
        return self.succeed

    @property
    def lines(self) -> List[str]:
        """Every line written, across all writes, in order."""
        return [line for write in self.writes for line in write]

    def events(self) -> List[Tuple[Dict[str, str], Dict[str, Any]]]:
        """Every written line parsed into its tags and fields."""
        return [parse_line(line) for line in self.lines]


@contextmanager
def recording_on(writer: FakeWriter = None) -> Iterator[FakeWriter]:
    """Turn usage recording on for the duration, writing to a fake.

    Args:
        writer: The fake to write to; a new one when omitted.

    Yields:
        The fake writer.
    """
    writer = writer or FakeWriter()
    events._recorder.cache_clear()
    try:
        with mock.patch.dict(os.environ, FULL_ENV, clear=False), mock.patch.object(
            events, "InfluxClient", lambda **kwargs: writer
        ):
            yield writer
    finally:
        events._recorder.cache_clear()


def _split_unescaped(text: str, separator: str) -> List[str]:
    """Split on a separator that is neither escaped nor inside quotes."""
    parts, current, quoted, index = [], "", False, 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            current += text[index : index + 2]
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        if char == separator and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
        index += 1
    parts.append(current)
    return parts


def _unescape(text: str) -> str:
    out, index = "", 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            out += text[index + 1]
            index += 2
        else:
            out += text[index]
            index += 1
    return out


def _field_value(raw: str) -> Any:
    if raw.startswith('"'):
        return _unescape(raw[1:-1])
    if raw in ("true", "false"):
        return raw == "true"
    if raw.endswith("i"):
        return int(raw[:-1])
    return float(raw)


def parse_line(line: str) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Parse one line of line protocol into tags and typed fields.

    Args:
        line: A line as written.

    Returns:
        The tags (measurement excluded) and the fields with Python types:
        ``str`` for strings, ``int`` for integers, ``float`` for floats and
        ``bool`` for booleans.
    """
    head, fields, _timestamp = _split_unescaped(line, " ")
    tags = {}
    for tag in _split_unescaped(head, ",")[1:]:
        key, value = _split_unescaped(tag, "=")
        tags[_unescape(key)] = _unescape(value)
    parsed = {}
    for field in _split_unescaped(fields, ","):
        key, value = _split_unescaped(field, "=")
        parsed[_unescape(key)] = _field_value(value)
    return tags, parsed
