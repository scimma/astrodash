"""Parsing for WISeREP's zipped, delimited search export.

Two callers read the same export with opposite lifecycles: the request path,
and the offline monthly scraper under ``app/wiserep_scrape/``. The parsing
lives here, in the application, because the scraper's own documentation states
that nothing in the request path imports it -- so the dependency runs the
other way and the scraper imports from this module.

WISeREP serves the export as a zip holding one delimited member:
``wiserep_spectra.csv`` for ``format=csv`` and ``wiserep_spectra.tsv`` for
``format=tsv``. Columns are matched by name, never by position, because the
spellings of a WISeREP header have changed more than once.

Nothing here performs a request, imports Django, or reads settings, so either
caller can import it without the other's environment.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Iterable

# Identify the client plainly. No API key or login is needed for the public
# pages this reads.
#
# An earlier revision sent a TNS-style marker with "tns_id":0. TNS issues real
# bot ids on registration and WISeREP is operated by the same group, so a
# marker carrying a placeholder id claims a registration that does not exist.
# If AstroDASH registers a bot id, configure it rather than reinstating the
# literal below.
USER_AGENT = "AstroDASH-WISeREP-Ingest/1.0 (+https://astrodash.scimma.org)"

# Ceiling on the decompressed size of a single export member. The export is a
# few megabytes of text at WISeREP's 50-row page cap; this is generous for
# that and still bounds a member whose compressed size says nothing about its
# expansion. Callers may pass a different ceiling.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024

# Read the member in pieces so a refusal happens before the whole of it has
# been decompressed.
_READ_CHUNK_BYTES = 64 * 1024

# Which member extension each delimiter is served as.
_DELIMITER_SUFFIXES = {",": ".csv", "\t": ".tsv"}

_RECOGNIZED_SUFFIXES = frozenset(_DELIMITER_SUFFIXES.values())

# WISeREP names the member after the search it exports; a zip that also
# carries something else is still readable through this one.
_PREFERRED_STEM = "wiserep_spectra"


def compact(text: object) -> str:
    """Collapse whitespace in a value and strip it.

    Args:
        text: Any value; ``None`` is treated as empty.

    Returns:
        The value as a string with internal whitespace runs collapsed to a
        single space and the ends stripped.
    """
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def normalize_col(name: str) -> str:
    """Reduce a column name to a comparable key.

    Args:
        name: A column name as WISeREP spelled it.

    Returns:
        The name lowercased with every non-alphanumeric character removed, so
        that ``"Spec. ID"``, ``"Spec ID"`` and ``"spec.id"`` compare equal.
    """
    return re.sub(r"[^a-z0-9]+", "", compact(name).lower())


def find_column(
    fieldnames: Iterable[str],
    preferred: Iterable[str],
    contains: Iterable[str] = (),
) -> str | None:
    """Resolve one logical column against the header WISeREP actually sent.

    Args:
        fieldnames: The header names present in the export.
        preferred: Known spellings, tried in order; the first that matches a
            header under :func:`normalize_col` wins.
        contains: Fallback tokens; a header containing all of them matches.
            Empty -- the default -- means there is no fallback, so a header
            that no ``preferred`` alias names does not match at all.

    Returns:
        The matching header name as it appears in the export, or ``None`` when
        no header matches.
    """
    fields = list(fieldnames)
    normalized = {normalize_col(f): f for f in fields}

    for alias in preferred:
        key = normalize_col(alias)
        if key in normalized:
            return normalized[key]

    # A fallback given no tokens must match nothing. Without this guard the
    # loop below asks ``all()`` over an empty sequence, which is vacuously
    # true, and so returns whichever column happens to come first -- for a
    # spectra export, the object's IAU name standing in for its spectrum id.
    contains_norm = [token for token in (normalize_col(x) for x in contains) if token]
    if not contains_norm:
        return None

    for f in fields:
        nf = normalize_col(f)
        if all(token in nf for token in contains_norm):
            return f
    return None


# The export's spectrum-identifier column. One list, because the client dedupes
# paginated rows by this column while the ranker uses it as a candidate's
# identity -- if the two drifted, a row could be deduped under one spelling and
# identified under another. ``normalize_col`` folds punctuation and case, so
# "Spec. ID" and "Spec ID" are already the same key.
SPEC_ID_ALIASES = ("Spec. ID", "Spectrum ID")


def clean_csv_text(text: str) -> str:
    """Strip what confuses :class:`csv.DictReader` at the start of an export.

    Args:
        text: The decoded export text.

    Returns:
        The text without a UTF-8 BOM or leading blank lines.
    """
    # Strip UTF-8 BOM and leading blank lines that occasionally confuse DictReader.
    return text.lstrip("﻿\r\n ")


def parse_csv_rows(text: str, delimiter: str = ",") -> list[dict[str, str]]:
    """Parse a delimited export into rows keyed by column name.

    Args:
        text: The decoded export text.
        delimiter: The field delimiter; ``","`` for the CSV export and
            ``"\\t"`` for the TSV one.

    Returns:
        One dictionary per data row, with both keys and values compacted. An
        empty list when the text is empty or carries no header.
    """
    cleaned = clean_csv_text(text)
    if not cleaned:
        return []

    reader = csv.DictReader(io.StringIO(cleaned, newline=""), delimiter=delimiter)
    if not reader.fieldnames:
        return []

    return [
        {compact(k): compact(v) for k, v in row.items() if k is not None}
        for row in reader
    ]


def select_zip_member(names: Iterable[str], delimiter: str = ",") -> str:
    """Choose which member of a WISeREP zip export holds the table.

    Args:
        names: The archive's member names.
        delimiter: The delimiter the caller asked WISeREP for. A member whose
            extension matches that delimiter is preferred over one that does
            not.

    Returns:
        The name of the member to read.

    Raises:
        RuntimeError: When no member looks like a delimited table. The message
            names the members that were found.
    """
    all_names = list(names)
    recognized = [
        name for name in all_names if Path(name).suffix.lower() in _RECOGNIZED_SUFFIXES
    ]
    if not recognized:
        raise RuntimeError(
            "WISeREP zip export contained no delimited table file "
            f"(members={all_names})"
        )

    wanted_suffix = _DELIMITER_SUFFIXES.get(delimiter)
    matching = [
        name for name in recognized if Path(name).suffix.lower() == wanted_suffix
    ]
    candidates = matching or recognized

    return next(
        (name for name in candidates if Path(name).stem.lower() == _PREFERRED_STEM),
        candidates[0],
    )


def _read_member_bounded(
    archive: zipfile.ZipFile, name: str, max_uncompressed_bytes: int
) -> bytes:
    """Read one archive member, refusing it past a decompressed-size ceiling.

    Args:
        archive: The open archive.
        name: The member to read.
        max_uncompressed_bytes: The most decompressed data to accept.

    Returns:
        The member's bytes.

    Raises:
        RuntimeError: When the member expands past the ceiling. It is refused
            partway through rather than after being fully decompressed.
    """
    chunks: list[bytes] = []
    total = 0
    with archive.open(name) as member:
        while True:
            chunk = member.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_uncompressed_bytes:
                raise RuntimeError(
                    f"WISeREP zip member '{name}' expands beyond the "
                    f"{max_uncompressed_bytes}-byte uncompressed limit"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def extract_csv_text_from_zip(
    content: bytes,
    delimiter: str = ",",
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
) -> str:
    """Pull the delimited table out of a WISeREP zip export.

    Args:
        content: The zip payload as served.
        delimiter: The delimiter the caller asked WISeREP for, used to pick
            between members when the archive carries more than one shape.
        max_uncompressed_bytes: The most decompressed data to accept from the
            chosen member.

    Returns:
        The member's decoded text.

    Raises:
        RuntimeError: When no member looks like a delimited table, or when the
            chosen member expands past ``max_uncompressed_bytes``.
        zipfile.BadZipFile: When the payload is not a zip archive.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        member = select_zip_member(archive.namelist(), delimiter)
        return _read_member_bounded(archive, member, max_uncompressed_bytes).decode(
            "utf-8-sig"
        )
