"""Eligibility and ranking over a parsed WISeREP spectrum export.

Everything here is a pure function over the rows
:mod:`astrodash.infrastructure.wiserep.parsing` produces. Nothing performs a
request, imports Django, or reads settings, so the decision of *which*
spectrum to classify is testable against a captured export.

Three facts about the upstream export shape the rules:

* **WISeREP matches names as substrings.** A search for ``2002e`` returns
  seven distinct objects and ``2002`` returns ten, so the returned rows are
  not by themselves an answer. Only rows whose IAU designation or one of its
  internal designations *equals* the submitted name, once prefix and internal
  spacing are normalized, are treated as the object the researcher asked for.
* **The phase column is always empty**, so a near-peak rule cannot run.
  Ranking is by Julian date ascending -- the earliest observation is normally
  the classification epoch -- breaking ties on the quality flag and then on
  the spectrum identifier so repeated calls agree.
* **The quality flag is sparse** (populated on 4 of 35 rows for a 2002
  object), so the tie-break commonly falls through to the identifier.

A refusal is a return value, not an exception: these functions report what
they found and leave the surface mapping to the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from astrodash.infrastructure.wiserep.parsing import compact, find_column

logger = logging.getLogger(__name__)

# WISeREP marks a publicly released spectrum with ``Y`` in the ``Public``
# column; anything else is still inside its proprietary period (R5).
_PUBLIC_VALUE = "y"

# ``Spec. type`` is one of Object, Host, Sky, Arcs or Synthetic. Only a
# spectrum of the object itself can be classified (R20).
_OBJECT_SPEC_TYPE = "object"

# Ordering of the quality flag for the tie-break, best first. An unset flag
# sorts last, behind every stated quality.
_QUALITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_QUALITY_UNSET_RANK = len(_QUALITY_ORDER)

# Designation prefixes WISeREP and the researcher use interchangeably: the
# submitted ``SN2002er``, ``AT 2002er`` and ``2002er`` are the same object.
# Stripped only when a digit follows, so a survey name such as ``ATLAS26hqe``
# keeps its leading letters.
_NAME_PREFIXES = ("sn", "at")

# Internal designations arrive as one comma-separated field.
_INTERNAL_NAME_SEPARATOR = ","


class RefusalReason(Enum):
    """Why a submitted name produced no selected spectrum."""

    #: The returned rows describe more than one distinct object, so the
    #: submitted name does not identify which one to classify (R19).
    AMBIGUOUS_NAME = "ambiguous_name"

    #: Nothing the submitted name matched survived eligibility (R5, R20) or
    #: carried a usable observation date.
    NO_ELIGIBLE_SPECTRUM = "no_eligible_spectrum"


@dataclass(frozen=True)
class ObjectSummary:
    """One distinct object present in an export, for an ambiguity refusal.

    Attributes:
        designation: The IAU designation as WISeREP spells it, e.g.
            ``"SN 2002er"``.
        object_type: WISeREP's object type, e.g. ``"SN Ia"``; empty when
            unclassified.
        redshift: The recorded redshift, or ``None`` when WISeREP records
            none.
    """

    designation: str
    object_type: str
    redshift: float | None


@dataclass(frozen=True)
class SpectrumCandidate:
    """One eligible spectrum, carrying what locates and describes it.

    Attributes:
        designation: The IAU designation of the object it belongs to.
        spectrum_id: WISeREP's ``Spec. ID``, which locates the spectrum
            upstream.
        observation_date: The observation timestamp as WISeREP spells it.
        julian_date: The observation's Julian date, the ranking key.
        telescope: The telescope name.
        instrument: The instrument name.
        quality: WISeREP's quality flag; often empty.
        redshift: The object's recorded redshift, or ``None``.
        ascii_url: The URL of the ASCII spectrum file.
    """

    designation: str
    spectrum_id: str
    observation_date: str
    julian_date: float
    telescope: str
    instrument: str
    quality: str
    redshift: float | None
    ascii_url: str


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of ranking one export for one submitted name.

    Attributes:
        candidates: Every eligible spectrum for the resolved object, earliest
            first. Empty on a refusal.
        refusal: Why nothing was selected, or ``None`` on success.
        objects: The distinct objects the export described. Carried on an
            ambiguity refusal so the caller can offer the choice; empty
            otherwise.
    """

    candidates: tuple[SpectrumCandidate, ...]
    refusal: RefusalReason | None
    objects: tuple[ObjectSummary, ...] = ()

    @property
    def selected(self) -> SpectrumCandidate | None:
        """The automatically selected spectrum.

        Returns:
            The earliest eligible spectrum, or ``None`` on a refusal.
        """
        return self.candidates[0] if self.candidates else None


def normalize_designation(name: str) -> str:
    """Reduce a designation to a comparable key.

    Args:
        name: A designation as submitted or as WISeREP spells it, e.g.
            ``"SN 2002er"``, ``"AT2002er"`` or ``"ATLAS26hqe"``.

    Returns:
        The designation lowercased, with all whitespace removed and an ``SN``
        or ``AT`` prefix dropped when a digit follows it, so that
        ``"SN 2002er"``, ``"SN2002er"``, ``"AT2002er"`` and ``"2002er"``
        compare equal while ``"ATLAS26hqe"`` is left intact. Empty when the
        name holds nothing comparable.
    """
    key = "".join(compact(name).lower().split())
    for prefix in _NAME_PREFIXES:
        if key.startswith(prefix) and key[len(prefix) :][:1].isdigit():
            return key[len(prefix) :]
    return key


def _parse_float(value: str) -> float | None:
    """Read a numeric export field.

    Args:
        value: The field's text; may be empty.

    Returns:
        The value as a float, or ``None`` when it is empty or not a number.
    """
    text = compact(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class _Columns:
    """The header names one export actually used, resolved once per call."""

    def __init__(self, fieldnames: Iterable[str]) -> None:
        """Resolve each logical column against an export's header.

        Args:
            fieldnames: The header names present in the export.
        """
        fields = list(fieldnames)
        self.iau_name = find_column(fields, ("IAU name", "IAU name/s", "Name"))
        self.internal_names = find_column(
            fields, ("Internal name/s", "Internal name", "Internal names")
        )
        self.object_type = find_column(fields, ("Obj. Type", "Object Type", "Type"))
        self.redshift = find_column(fields, ("Redshift", "Obj. Redshift"))
        self.spectrum_id = find_column(fields, ("Spec. ID", "Spectrum ID"))
        self.observation_date = find_column(
            fields, ("Obs-date", "Obs. date", "Observation date")
        )
        self.julian_date = find_column(fields, ("JD", "Julian date"))
        self.telescope = find_column(fields, ("Telescope",))
        self.instrument = find_column(fields, ("Instrument",))
        self.public = find_column(fields, ("Public",))
        self.ascii_file = find_column(fields, ("Ascii file", "ASCII file"))
        self.spectrum_type = find_column(fields, ("Spec. type", "Spectrum type"))
        self.quality = find_column(fields, ("Spec. quality", "Quality"))

    def value(self, row: Mapping[str, str], column: str | None) -> str:
        """Read one logical column out of a row.

        Args:
            row: One parsed export row.
            column: The resolved header name, or ``None`` when the export
                carried no such column.

        Returns:
            The compacted field value, or an empty string when the column is
            absent from the export or the row.
        """
        if column is None:
            return ""
        return compact(row.get(column, ""))


def _row_designation_keys(columns: _Columns, row: Mapping[str, str]) -> set[str]:
    """Collect every designation a row can be matched by.

    Args:
        columns: The export's resolved headers.
        row: One parsed export row.

    Returns:
        The normalized IAU designation together with every normalized
        internal designation the row carries.
    """
    keys = {normalize_designation(columns.value(row, columns.iau_name))}
    internal = columns.value(row, columns.internal_names)
    for part in internal.split(_INTERNAL_NAME_SEPARATOR):
        keys.add(normalize_designation(part))
    keys.discard("")
    return keys


def _object_summaries(
    columns: _Columns, rows: Sequence[Mapping[str, str]]
) -> tuple[ObjectSummary, ...]:
    """Summarize the distinct objects a set of rows describes.

    Args:
        columns: The export's resolved headers.
        rows: Parsed export rows.

    Returns:
        One summary per distinct IAU designation, in the order the
        designations first appear.
    """
    summaries: dict[str, ObjectSummary] = {}
    for row in rows:
        designation = columns.value(row, columns.iau_name)
        key = normalize_designation(designation)
        if key in summaries:
            continue
        summaries[key] = ObjectSummary(
            designation=designation,
            object_type=columns.value(row, columns.object_type),
            redshift=_parse_float(columns.value(row, columns.redshift)),
        )
    return tuple(summaries.values())


def _is_eligible(columns: _Columns, row: Mapping[str, str]) -> bool:
    """Decide whether a row may be classified.

    Args:
        columns: The export's resolved headers.
        row: One parsed export row.

    Returns:
        ``True`` when WISeREP marks the row public (R5), its spectrum type is
        ``Object`` (R20), and it carries a readable Julian date to rank by.
    """
    if columns.value(row, columns.public).lower() != _PUBLIC_VALUE:
        return False
    if columns.value(row, columns.spectrum_type).lower() != _OBJECT_SPEC_TYPE:
        return False
    return _parse_float(columns.value(row, columns.julian_date)) is not None


def _candidate(columns: _Columns, row: Mapping[str, str]) -> SpectrumCandidate:
    """Build a candidate from an eligible row.

    Args:
        columns: The export's resolved headers.
        row: An export row that passed :func:`_is_eligible`.

    Returns:
        The candidate record for that row.
    """
    julian_date = _parse_float(columns.value(row, columns.julian_date))
    assert julian_date is not None  # guaranteed by _is_eligible
    return SpectrumCandidate(
        designation=columns.value(row, columns.iau_name),
        spectrum_id=columns.value(row, columns.spectrum_id),
        observation_date=columns.value(row, columns.observation_date),
        julian_date=julian_date,
        telescope=columns.value(row, columns.telescope),
        instrument=columns.value(row, columns.instrument),
        quality=columns.value(row, columns.quality),
        redshift=_parse_float(columns.value(row, columns.redshift)),
        ascii_url=columns.value(row, columns.ascii_file),
    )


def _ranking_key(candidate: SpectrumCandidate) -> tuple[float, int, int, float, str]:
    """Order candidates: earliest observation first (R6).

    Args:
        candidate: One eligible spectrum.

    Returns:
        A sort key of Julian date ascending, then the quality flag best
        first, then the spectrum identifier -- numerically when it is a
        number -- so repeated calls agree.
    """
    quality_rank = _QUALITY_ORDER.get(candidate.quality.lower(), _QUALITY_UNSET_RANK)
    spectrum_id = candidate.spectrum_id
    if spectrum_id.isdigit():
        return (candidate.julian_date, quality_rank, 0, float(spectrum_id), "")
    return (candidate.julian_date, quality_rank, 1, 0.0, spectrum_id)


def rank_candidates(rows: Sequence[Mapping[str, str]], name: str) -> SelectionResult:
    """Turn export rows for a submitted name into ranked candidates.

    The submitted name is matched exactly against each row's IAU and internal
    designations, because WISeREP's own search matches substrings and returns
    neighbouring objects. Rows that do not describe the submitted object are
    dropped, then the remainder is filtered for eligibility and ranked.

    Args:
        rows: Parsed export rows, keyed by WISeREP's column names.
        name: The designation the researcher submitted, in any accepted form
            (``2002er``, ``SN2002er``, ``AT 2002er``, ``ATLAS26hqe``).

    Returns:
        A :class:`SelectionResult`. On success its ``candidates`` hold every
        eligible spectrum for the object, earliest first, and ``selected`` is
        the one to classify. Otherwise ``refusal`` is
        :attr:`RefusalReason.AMBIGUOUS_NAME`, with the distinct objects in
        ``objects`` for the caller to offer, or
        :attr:`RefusalReason.NO_ELIGIBLE_SPECTRUM`.
    """
    if not rows:
        return SelectionResult(
            candidates=(), refusal=RefusalReason.NO_ELIGIBLE_SPECTRUM
        )

    columns = _Columns(rows[0].keys())
    wanted = normalize_designation(name)
    matched = [row for row in rows if wanted in _row_designation_keys(columns, row)]

    if not matched:
        # WISeREP returned neighbours of the submitted name but not the name
        # itself. More than one neighbour means the submitted name does not
        # identify an object, which is the ambiguity the researcher resolves
        # by choosing; a single neighbour is simply not what was asked for.
        objects = _object_summaries(columns, rows)
        if len(objects) > 1:
            logger.info(
                "WISeREP export for %r describes %d distinct objects and none "
                "matches it exactly; refusing as ambiguous",
                name,
                len(objects),
            )
            return SelectionResult(
                candidates=(),
                refusal=RefusalReason.AMBIGUOUS_NAME,
                objects=objects,
            )
        logger.info("WISeREP export for %r held no row for that designation", name)
        return SelectionResult(
            candidates=(), refusal=RefusalReason.NO_ELIGIBLE_SPECTRUM
        )

    objects = _object_summaries(columns, matched)
    if len(objects) > 1:
        logger.info(
            "WISeREP designation %r matches %d distinct objects; refusing as "
            "ambiguous",
            name,
            len(objects),
        )
        return SelectionResult(
            candidates=(),
            refusal=RefusalReason.AMBIGUOUS_NAME,
            objects=objects,
        )

    candidates = sorted(
        (_candidate(columns, row) for row in matched if _is_eligible(columns, row)),
        key=_ranking_key,
    )
    if not candidates:
        logger.info(
            "WISeREP designation %r matched %d rows, none of them an eligible "
            "public Object spectrum",
            name,
            len(matched),
        )
        return SelectionResult(
            candidates=(), refusal=RefusalReason.NO_ELIGIBLE_SPECTRUM
        )

    return SelectionResult(candidates=tuple(candidates), refusal=None)
