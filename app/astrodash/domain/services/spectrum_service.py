"""Spectrum sources behind the application's shared entry seam.

:meth:`SpectrumService.get_spectrum_data` is the one seam both the
interactive classify view and the REST endpoint read a spectrum through. It
takes either an uploaded file or an object designation; a designation is
resolved against WISeREP, ranked, fetched and retained.

Resolution is looked up before it is performed: the Django cache is consulted
first as a fast path, then the durably stored candidate set, and WISeREP is
asked only when neither holds a usable set for the designation. The cache fails
open, because the slim profiles run without Redis, while the stored set is what
keeps a repeat lookup from downloading anything -- retention is keyed by a
spectrum identifier that only resolution produces.

A stored set does not live forever. WISeREP publishes new epochs for an object
it already holds spectra for, so a set older than
``wiserep_candidate_set_ttl_seconds`` is re-resolved on the next lookup of that
designation. That narrows R13 from "a repeat lookup makes no outbound request"
to "a repeat lookup makes no outbound *download*": the re-resolution is one
search request, and the spectrum the researcher already fetched still comes
from local storage. Without it the result line's claim that the served epoch is
the earliest eligible observation would quietly stop being true.

Resolution is also the expensive half -- a wide designation paginates the
export for 50 to 100 seconds -- so two things protect the workers from it. A
refusal is cached briefly under the normalized designation, so a name that
cannot resolve does not re-paginate on every submission; and at most one
resolution per normalized designation is in flight in a worker at a time, a
second caller waiting on the first rather than paginating the same export
again. Neither is a limit on how many lookups may run: a researcher's
first-time lookup is never refused for being concurrent.

Every way the lookup can refuse carries its own failure class and its own
status, and each one is registered in the allowlist
:data:`_PASS_THROUGH_FAILURES` that :meth:`SpectrumService.get_spectrum_data`
re-raises unchanged. Anything outside that tuple is relabelled, and the two
relabellings are deliberately different: a database or storage failure is the
server's own, reported as such, while a genuinely unclassified failure stays a
spectrum data error. Neither carries the exception's text into the response.
"""

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from django.core.cache import cache
from django.db import Error as DatabaseError
from django.utils import timezone

from astrodash.config.logging import get_logger
from astrodash.config.settings import Settings, get_settings
from astrodash.core.exceptions import (
    AmbiguousDesignationException,
    AppException,
    FileReadException,
    IncompleteSourceExportException,
    NoEligibleSpectrumException,
    SpectrumValidationException,
    StorageException,
    ValidationException,
)
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.repositories.spectrum_repository import SpectrumRepository
from astrodash.infrastructure.db_threads import run_db_work, run_non_db_work
from astrodash.infrastructure.django_repositories import DjangoCandidateSetRepository
from astrodash.infrastructure.storage.wiserep_spectrum_repository import (
    WiserepSpectrumRepository,
    WiserepUnusableSpectrumError,
)
from astrodash.infrastructure.wiserep.client import (
    WiserepClient,
    WiserepIncompleteExportError,
    WiserepUnreachableError,
)
from astrodash.infrastructure.wiserep.selection import (
    RefusalReason,
    SpectrumCandidate,
    normalize_designation,
    rank_candidates,
)
from astrodash.shared.utils.validators import (
    validate_file_extension,
    validate_spectrum,
)

logger = get_logger(__name__)

# Prefix of the cache key holding one designation's ranked candidate set. The
# cache is only a fast path in front of the stored set, so its lifetime bounds
# nothing: an expired entry is re-read from storage, not re-resolved. The
# version segment is what retires entries written before the resolution time
# travelled with the set; an entry without one cannot be aged.
_CANDIDATE_CACHE_PREFIX = "wiserep:candidates:v2:"

# Prefix of the cache key holding one designation's cached refusal.
_REFUSAL_CACHE_PREFIX = "wiserep:refusal:v1:"

# How long a cached candidate set stays readable. Bounds cache occupancy only;
# the set's own lifetime is wiserep_candidate_set_ttl_seconds.
_CANDIDATE_CACHE_TTL_SECONDS = 3600

# Fallbacks for the configurable durations, used when the settings object does
# not carry a usable number -- which is what a test double hands over.
_DEFAULT_CANDIDATE_SET_TTL_SECONDS = 86400.0
_DEFAULT_REFUSAL_CACHE_TTL_SECONDS = 120.0
_DEFAULT_RESOLUTION_WAIT_SECONDS = 150.0


def _candidate_cache_key(normalized_name: str) -> str:
    """Build the cache key for one designation's candidate set.

    Args:
        normalized_name: The submitted designation, already normalized.

    Returns:
        The cache key holding that designation's ranked candidate set.
    """
    return f"{_CANDIDATE_CACHE_PREFIX}{normalized_name}"


def _refusal_cache_key(normalized_name: str) -> str:
    """Build the cache key for one designation's cached refusal.

    Args:
        normalized_name: The submitted designation, already normalized.

    Returns:
        The cache key holding that designation's most recent refusal.
    """
    return f"{_REFUSAL_CACHE_PREFIX}{normalized_name}"


def refusal_from_record(
    record: Mapping[str, Any], object_name: str
) -> Optional[AppException]:
    """Rebuild a cached refusal as the failure a fresh lookup would raise.

    The failure is rebuilt from its constructor arguments rather than from a
    stored message, so a refusal served from the cache carries exactly the
    status and the message a fresh one would. The submitted designation is
    taken from the caller rather than from the record: two spellings of one
    designation share a normalized key, and each caller must read its own
    spelling back.

    Args:
        record: The cached refusal, as :meth:`SpectrumService._cache_refusal`
            wrote it.
        object_name: The designation this caller submitted.

    Returns:
        The failure to raise, or ``None`` when the record describes no
        recognised refusal.
    """
    kind = record.get("kind")
    if kind == "incomplete_export":
        return IncompleteSourceExportException(object_name, record.get("detail"))
    if kind == "ambiguous":
        return AmbiguousDesignationException(object_name, record.get("objects") or ())
    if kind == "no_eligible":
        return NoEligibleSpectrumException(object_name, record.get("detail"))
    return None


class _ResolutionSingleFlight:
    """Keeps one resolution per normalized designation in flight at a time.

    Resolution is synchronous and holds a thread for as long as the export
    paginates, so two submissions of one slow designation would otherwise
    paginate the same export twice over -- with two gunicorn workers, that is
    the whole application. A second caller waits on the first instead and then
    reads its outcome from the cache or the stored set.

    Scoped to one worker process on purpose. Deduplicating across workers would
    need a lock in Redis, and Redis is optional here: the slim profiles run
    without it, and nothing in this seam may refuse because a cache is absent.
    A waiter whose wait runs out resolves the designation itself rather than
    failing, for the same reason.
    """

    def __init__(self) -> None:
        """Build the empty registry of in-flight designations."""
        self._guard = threading.Lock()
        self._slots: Dict[str, List[Any]] = {}

    @contextmanager
    def hold(self, key: str, wait_seconds: float) -> Iterator[bool]:
        """Hold the slot for one designation for the duration of the block.

        Args:
            key: The normalized designation being resolved.
            wait_seconds: How long to wait for a resolution already in flight
                before proceeding regardless. Non-positive waits without bound.

        Yields:
            ``True`` when this caller holds the slot, ``False`` when the wait
            ran out and it is proceeding anyway.
        """
        lock = self._claim(key)
        timeout = wait_seconds if wait_seconds > 0 else -1
        held = lock.acquire(timeout=timeout)
        try:
            yield held
        finally:
            if held:
                lock.release()
            self._discard(key)

    def _claim(self, key: str) -> threading.Lock:
        """Take a reference to one designation's lock, creating it if needed.

        Args:
            key: The normalized designation being resolved.

        Returns:
            The lock guarding that designation.
        """
        with self._guard:
            slot = self._slots.get(key)
            if slot is None:
                slot = [threading.Lock(), 0]
                self._slots[key] = slot
            slot[1] += 1
            return slot[0]

    def _discard(self, key: str) -> None:
        """Drop this caller's reference, forgetting the lock when it is last.

        Args:
            key: The normalized designation that was being resolved.

        Returns:
            None.
        """
        with self._guard:
            slot = self._slots.get(key)
            if slot is None:
                return
            slot[1] -= 1
            if slot[1] <= 0:
                self._slots.pop(key, None)


#: The in-flight resolutions of this worker process, shared by every service
#: instance in it so that per-request construction cannot defeat the dedupe.
_RESOLUTIONS = _ResolutionSingleFlight()


def candidate_to_record(candidate: SpectrumCandidate) -> Dict[str, Any]:
    """Reduce a ranked candidate to the mapping the candidate set stores.

    Args:
        candidate: One eligible spectrum produced by ranking.

    Returns:
        A JSON-serializable mapping carrying everything needed to reach the
        spectrum again without re-resolving the designation.
    """
    return {
        "designation": candidate.designation,
        "source_spectrum_id": candidate.spectrum_id,
        "observation_date": candidate.observation_date,
        "julian_date": candidate.julian_date,
        "telescope": candidate.telescope,
        "instrument": candidate.instrument,
        "quality": candidate.quality,
        "redshift": candidate.redshift,
        "download_url": candidate.ascii_url,
        "object_id": candidate.object_id,
    }


def candidate_from_record(record: Mapping[str, Any]) -> SpectrumCandidate:
    """Rebuild a ranked candidate from a stored or cached mapping.

    Args:
        record: One entry of a stored candidate set, as
            :func:`candidate_to_record` wrote it.

    Returns:
        The candidate that entry describes.
    """
    return SpectrumCandidate(
        designation=record.get("designation", ""),
        spectrum_id=record.get("source_spectrum_id", ""),
        observation_date=record.get("observation_date", ""),
        julian_date=record.get("julian_date") or 0.0,
        telescope=record.get("telescope", ""),
        instrument=record.get("instrument", ""),
        quality=record.get("quality", ""),
        redshift=record.get("redshift"),
        ascii_url=record.get("download_url", ""),
        object_id=record.get("object_id", ""),
    )


def select_record(
    records: List[Dict[str, Any]], source_spectrum_id: Optional[str] = None
) -> Dict[str, Any]:
    """Pick which record of a ranked candidate set to serve.

    Args:
        records: The ranked candidate records, earliest first.
        source_spectrum_id: The source's identifier for the spectrum the
            researcher chose, or ``None`` to take the automatic selection.

    Returns:
        The record carrying that identifier, or the first -- the earliest
        eligible observation (R6) -- when no identifier was given or the set
        does not hold it.
    """
    if source_spectrum_id:
        wanted = str(source_spectrum_id)
        for record in records:
            if str(record.get("source_spectrum_id")) == wanted:
                return record
    return records[0]


# Failure classes the seam re-raises unchanged, each carrying its own status.
# A class missing here is relabelled as a spectrum validation error by
# :meth:`SpectrumService.get_spectrum_data`, which is why every by-name
# refusal is listed explicitly rather than left to a shared base class.
_PASS_THROUGH_FAILURES = (
    ValidationException,
    SpectrumValidationException,
    FileReadException,
    WiserepUnreachableError,
    WiserepIncompleteExportError,
    WiserepUnusableSpectrumError,
    AmbiguousDesignationException,
    NoEligibleSpectrumException,
    IncompleteSourceExportException,
    StorageException,
)

# Failure classes that are the server's own rather than the spectrum's. They
# are relabelled, because their text describes a database or a connection and
# belongs in the log, but they are relabelled to a server-side status: a
# researcher told a spectrum is unusable would go and look at the spectrum.
_INFRASTRUCTURE_FAILURES = (DatabaseError,)

# What a relabelled failure says. Fixed strings: the exception's own text is
# logged, never answered with.
_INFRASTRUCTURE_FAILURE_MESSAGE = (
    "The spectrum store could not be reached. Please try again."
)
_UNCLASSIFIED_FAILURE_MESSAGE = "Spectrum data error: the spectrum could not be read."


class SpectrumService:
    """Reads spectra from an upload or from WISeREP by object designation."""

    def __init__(
        self,
        file_repo: SpectrumRepository,
        wiserep_repo: WiserepSpectrumRepository,
        db_repo: SpectrumRepository,
        candidate_set_repo: Optional[DjangoCandidateSetRepository] = None,
        wiserep_client: Optional[WiserepClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        """Build the service over its repositories.

        Args:
            file_repo: Reads an uploaded spectrum file.
            wiserep_repo: Downloads and reads the spectrum a chosen candidate
                points at.
            db_repo: Retains fetched spectra and serves retained ones.
            candidate_set_repo: Stores the ranked candidate set resolved for a
                designation. One over the Django database is built when
                omitted.
            wiserep_client: The bounded transport resolution reads WISeREP's
                export through. One is built when omitted.
            settings: The application settings. Loaded from the environment
                when omitted.
        """
        self.file_repo = file_repo
        self.wiserep_repo = wiserep_repo
        self.db_repo = db_repo
        self.settings = settings or get_settings()
        self.candidate_set_repo = candidate_set_repo or DjangoCandidateSetRepository()
        self.wiserep_client = wiserep_client or WiserepClient(settings=self.settings)
        self._candidate_set_ttl_seconds = self._configured_seconds(
            "wiserep_candidate_set_ttl_seconds", _DEFAULT_CANDIDATE_SET_TTL_SECONDS
        )
        self._refusal_cache_ttl_seconds = self._configured_seconds(
            "wiserep_refusal_cache_ttl_seconds", _DEFAULT_REFUSAL_CACHE_TTL_SECONDS
        )
        self._resolution_wait_seconds = self._configured_seconds(
            "wiserep_resolution_wait_seconds", _DEFAULT_RESOLUTION_WAIT_SECONDS
        )

    def _configured_seconds(self, field: str, default: float) -> float:
        """Read one configured duration, falling back to its default.

        Args:
            field: Name of the settings field holding the duration.
            default: The duration to use when the field holds no number.

        Returns:
            The configured duration in seconds.
        """
        try:
            return float(getattr(self.settings, field, default))
        except (TypeError, ValueError):
            logger.debug(
                "Settings field %s holds no usable number; using %.0fs",
                field,
                default,
            )
            return default

    async def get_spectrum_from_file(self, file: Any) -> Spectrum:
        logger.debug(f"Getting spectrum from file: {getattr(file, 'name', getattr(file, 'filename', 'unknown'))}")
        spectrum = await run_non_db_work(self.file_repo.get_from_file, file)
        logger.debug(f"Repository returned spectrum: {spectrum}")

        if not spectrum:
            logger.error("Repository returned None spectrum")
            raise FileReadException(getattr(file, 'name', getattr(file, 'filename', 'unknown')), "Invalid or unreadable spectrum file.")

        try:
            validate_spectrum(spectrum.x, spectrum.y, spectrum.redshift)
            logger.debug("Spectrum validation passed")
        except Exception as e:
            logger.error(f"Spectrum validation failed: {e}")
            raise SpectrumValidationException("Invalid or unreadable spectrum file.")

        return spectrum

    async def get_spectrum_by_name(
        self, object_name: str, source_spectrum_id: Optional[str] = None
    ) -> Spectrum:
        """Read the spectrum a submitted object designation resolves to.

        Resolution is looked up before it is performed -- cache, then the
        stored candidate set, then WISeREP -- and the selected epoch is served
        from retention when it is already held, so a repeat lookup makes no
        outbound download. It may make one outbound search: a candidate set
        older than ``wiserep_candidate_set_ttl_seconds`` is re-resolved, so an
        epoch WISeREP published after the first lookup is offered rather than
        hidden behind a set resolved once and kept forever (R13, narrowed). A
        newly fetched spectrum is retained with the identifiers that locate it
        upstream and the time it was retrieved.

        Args:
            object_name: The designation the researcher submitted, in any
                accepted form (``2002er``, ``SN2002er``, ``ATLAS26hqe``).
            source_spectrum_id: The source's identifier for the epoch the
                researcher chose, when they chose one (R8). Omitted, the
                automatic selection stands.

        Returns:
            The spectrum of the selected epoch, carrying the object
            designation, the WISeREP spectrum identifier, the recorded
            redshift when WISeREP holds one, and the retrieval time.

        Raises:
            ValidationException: If the designation holds nothing comparable.
            AmbiguousDesignationException: If it matches more than one object.
            NoEligibleSpectrumException: If it resolves to no public,
                classifiable spectrum.
            IncompleteSourceExportException: If WISeREP's export could not be
                read to exhaustion, so ranking would run on a partial set.
            WiserepUnreachableError: If WISeREP could not be read within its
                bounds.
            WiserepUnusableSpectrumError: If the selected spectrum cannot be
                classified as fetched, or if the retained copy no longer
                validates.
        """
        normalized = normalize_designation(object_name)
        if not normalized:
            raise ValidationException(
                f"'{object_name}' is not a usable object designation"
            )

        records = await run_db_work(self._load_candidate_records, normalized)
        if records is None:
            records = await run_db_work(self._resolve_once, object_name, normalized)

        selected = candidate_from_record(select_record(records, source_spectrum_id))
        logger.debug(
            "Selected WISeREP spectrum %s for %r (%s, %s)",
            selected.spectrum_id,
            object_name,
            selected.observation_date,
            selected.instrument,
        )

        retained = await run_db_work(self._retained_spectrum, selected)
        if retained is not None:
            logger.info(
                "Serving retained spectrum %s for %r, retrieved %s",
                selected.spectrum_id,
                object_name,
                retained.retrieved_at,
            )
            return retained

        spectrum = await run_non_db_work(self.wiserep_repo.get_for_candidate, selected)
        return await run_db_work(self.db_repo.save, spectrum)

    def _load_candidate_records(
        self, normalized_name: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Look up an already resolved, still current candidate set.

        The cache is read first as a fast path; a miss there falls through to
        the stored set rather than re-resolving, and a stored set found that
        way is written back to the cache. A set that has aged past
        ``wiserep_candidate_set_ttl_seconds`` is reported as absent, which is
        what sends the next lookup back to WISeREP for the epochs published
        since.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            The ranked candidate records, earliest first, or ``None`` when no
            current set is held for the designation.
        """
        cached = self._cache_read(normalized_name)
        if cached is not None:
            records, resolved_at = cached
            if records and self._is_current(resolved_at):
                logger.debug("Candidate set for %r served from cache", normalized_name)
                return list(records)

        stored = self.candidate_set_repo.get(normalized_name) or {}
        records = stored.get("candidates")
        resolved_at = stored.get("resolved_at")
        if not records:
            return None
        if not self._is_current(resolved_at):
            logger.info(
                "Candidate set for %r resolved %s has aged past %.0fs; re-resolving",
                normalized_name,
                resolved_at,
                self._candidate_set_ttl_seconds,
            )
            return None
        logger.debug("Candidate set for %r served from storage", normalized_name)
        self._cache_write(normalized_name, records, resolved_at)
        return list(records)

    def _is_current(self, resolved_at: Optional[datetime]) -> bool:
        """Report whether a candidate set resolved then may still be served.

        Args:
            resolved_at: When the set was resolved, or ``None`` when its age is
                not recorded.

        Returns:
            ``True`` when the set is younger than its configured lifetime. A
            set whose age cannot be established is not current: re-resolving it
            costs one search, while serving it indefinitely would hide every
            epoch published since.
        """
        if resolved_at is None:
            return False
        try:
            age = timezone.now() - resolved_at
        except TypeError:
            logger.warning(
                "Candidate set resolution time %r is not comparable; re-resolving",
                resolved_at,
            )
            return False
        return age <= timedelta(seconds=self._candidate_set_ttl_seconds)

    def _resolve_once(
        self, object_name: str, normalized_name: str
    ) -> List[Dict[str, Any]]:
        """Resolve a designation, at most once at a time and not if refused.

        Three things happen before any outbound request. The designation's slot
        is claimed, so a second caller for the same name waits here rather than
        paginating the same export again; a refusal cached by a recent lookup is
        re-raised, so a name that cannot resolve does not re-paginate on every
        submission; and the lookup is retried, because the caller we waited on
        may have resolved the set while we held still.

        Args:
            object_name: The designation as the researcher submitted it.
            normalized_name: That designation normalized.

        Returns:
            The ranked candidate records, earliest first.

        Raises:
            AppException: The refusal a recent lookup of this designation
                cached, carrying the status and message a fresh one would, or
                whatever resolving the designation afresh raises.
        """
        with _RESOLUTIONS.hold(normalized_name, self._resolution_wait_seconds) as held:
            if not held:
                logger.warning(
                    "Waited %.0fs on a resolution of %r already in flight; "
                    "resolving it again rather than refusing",
                    self._resolution_wait_seconds,
                    normalized_name,
                )
            refusal = self._cached_refusal(normalized_name, object_name)
            if refusal is not None:
                logger.info(
                    "Refusing %r from the cached %s rather than re-resolving",
                    object_name,
                    type(refusal).__name__,
                )
                raise refusal
            records = self._load_candidate_records(normalized_name)
            if records is not None:
                return records
            try:
                return self._resolve_candidate_records(object_name, normalized_name)
            except WiserepUnreachableError:
                aged = self._aged_candidate_records(normalized_name)
                if aged is None:
                    raise
                logger.warning(
                    "WISeREP is unreachable, so %r is served from the candidate "
                    "set resolved before the TTL expired; a newer epoch may "
                    "exist upstream",
                    object_name,
                )
                return aged

    def _aged_candidate_records(
        self, normalized_name: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Read a stored candidate set without regard to its age.

        Only the unreachable-source fallback uses this. Re-resolution exists to
        notice epochs published since, but a designation whose spectrum is
        already retained should not stop working because WISeREP is down: the
        aged set still locates a spectrum held in local storage, so serving it
        degrades freshness rather than availability.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            The ranked candidate records, earliest first, or ``None`` when no
            set at all is held for the designation.
        """
        stored = self.candidate_set_repo.get(normalized_name) or {}
        records = stored.get("candidates")
        if not records:
            return None
        return list(records)

    def _resolve_candidate_records(
        self, object_name: str, normalized_name: str
    ) -> List[Dict[str, Any]]:
        """Resolve a designation against WISeREP and store what it produced.

        Each of the three refusals it can reach is cached briefly under the
        normalized designation before being raised, so the pagination behind it
        is not repeated on the next submission of that name. The refusal is
        cached as the arguments it was built from, not as a message, so the
        cached one is indistinguishable from a fresh one.

        Args:
            object_name: The designation as the researcher submitted it, which
                is what WISeREP is searched for and what the rows are matched
                against.
            normalized_name: That designation normalized, which keys the
                stored set.

        Returns:
            The ranked candidate records, earliest first.

        Raises:
            IncompleteSourceExportException: If pagination stopped with rows
                still outstanding, so nothing is ranked from the pages already
                fetched.
            AmbiguousDesignationException: If the export describes more than
                one object.
            NoEligibleSpectrumException: If nothing the designation matched is
                an eligible public spectrum of the object.
            WiserepUnreachableError: If WISeREP could not be read within its
                bounds.
        """
        try:
            rows = self.wiserep_client.search_spectra_rows(object_name)
        except WiserepIncompleteExportError as exc:
            self._cache_refusal(
                normalized_name, {"kind": "incomplete_export", "detail": exc.message}
            )
            raise IncompleteSourceExportException(object_name, exc.message) from exc

        result = rank_candidates(rows, object_name)
        if result.refusal is RefusalReason.AMBIGUOUS_NAME:
            objects = [
                {
                    "designation": summary.designation,
                    "object_type": summary.object_type,
                    "redshift": summary.redshift,
                }
                for summary in result.objects
            ]
            self._cache_refusal(
                normalized_name, {"kind": "ambiguous", "objects": objects}
            )
            raise AmbiguousDesignationException(object_name, objects)
        if not result.candidates:
            self._cache_refusal(
                normalized_name, {"kind": "no_eligible", "detail": None}
            )
            raise NoEligibleSpectrumException(object_name)

        records = [candidate_to_record(item) for item in result.candidates]
        resolved_at = timezone.now()
        self.candidate_set_repo.save(normalized_name, records, resolved_at=resolved_at)
        self._cache_write(normalized_name, records, resolved_at)
        self._clear_refusal(normalized_name)
        logger.info(
            "Resolved %r against WISeREP: %d eligible spectrum(s)",
            object_name,
            len(records),
        )
        return records

    def _retained_spectrum(self, candidate: SpectrumCandidate) -> Optional[Spectrum]:
        """Fetch the retained copy of one epoch, when storage holds it.

        Args:
            candidate: The selected epoch, carrying the object designation and
                the source's identifier for the spectrum.

        Returns:
            The retained spectrum, or ``None`` when none is held for that pair
            or storage could not be read.

        Raises:
            StorageException: If the configured store cannot serve retention at
                all, which is a misconfiguration rather than a miss and would
                otherwise re-download every epoch on every lookup.
            WiserepUnusableSpectrumError: If a retained spectrum is held but
                its stored data no longer validates, which is a condition of
                the held copy rather than of the source.
        """
        lookup = getattr(self.db_repo, "get_by_designation_and_spectrum_id", None)
        if lookup is None:
            logger.error(
                "Retention store %s cannot serve retained spectra",
                type(self.db_repo).__name__,
            )
            raise StorageException(_INFRASTRUCTURE_FAILURE_MESSAGE)

        try:
            retained = lookup(candidate.designation, candidate.spectrum_id)
        except DatabaseError as exc:
            logger.warning(
                "Retention lookup failed for %r spectrum %s: %s",
                candidate.designation,
                candidate.spectrum_id,
                exc,
            )
            return None

        if retained is None:
            return None

        try:
            validate_spectrum(retained.x, retained.y, retained.redshift)
        except Exception as exc:  # noqa: BLE001 - reported as an unusable copy
            raise WiserepUnusableSpectrumError(
                f"retained spectrum {candidate.spectrum_id} for "
                f"{candidate.designation!r} no longer validates: {exc}"
            ) from exc
        return retained

    def _cache_read(
        self, normalized_name: str
    ) -> Optional[Tuple[List[Dict[str, Any]], Optional[datetime]]]:
        """Read a candidate set from the cache, tolerating its absence.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            The cached candidate records with the time they were resolved, or
            ``None`` when the cache holds none or cannot be reached.
        """
        try:
            entry = cache.get(_candidate_cache_key(normalized_name))
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning(
                "Candidate set cache unreadable for %r: %s", normalized_name, exc
            )
            return None
        if not isinstance(entry, Mapping):
            return None
        records = entry.get("candidates")
        if not records:
            return None
        return list(records), entry.get("resolved_at")

    def _cache_write(
        self,
        normalized_name: str,
        records: List[Dict[str, Any]],
        resolved_at: Optional[datetime],
    ) -> None:
        """Write a candidate set to the cache, tolerating its absence.

        The resolution time travels with the records: the cache's own timeout
        bounds occupancy, while whether the set may still be served is decided
        by its age against ``wiserep_candidate_set_ttl_seconds``.

        Args:
            normalized_name: The submitted designation, already normalized.
            records: The ranked candidate records to cache.
            resolved_at: When those records were resolved.

        Returns:
            None.
        """
        try:
            cache.set(
                _candidate_cache_key(normalized_name),
                {"candidates": records, "resolved_at": resolved_at},
                timeout=_CANDIDATE_CACHE_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning(
                "Candidate set cache unwritable for %r: %s", normalized_name, exc
            )

    def _cached_refusal(
        self, normalized_name: str, object_name: str
    ) -> Optional[AppException]:
        """Read back the refusal a recent lookup of this designation cached.

        Args:
            normalized_name: The submitted designation, already normalized.
            object_name: The designation this caller submitted, which the
                rebuilt refusal reports.

        Returns:
            The failure to raise, or ``None`` when no refusal is cached or the
            cache cannot be reached -- in which case the designation is
            resolved again, which is the work the cache only ever saves.
        """
        try:
            record = cache.get(_refusal_cache_key(normalized_name))
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning("Refusal cache unreadable for %r: %s", normalized_name, exc)
            return None
        if not isinstance(record, Mapping):
            return None
        return refusal_from_record(record, object_name)

    def _cache_refusal(self, normalized_name: str, record: Dict[str, Any]) -> None:
        """Cache one refusal briefly, tolerating the cache's absence.

        Args:
            normalized_name: The submitted designation, already normalized.
            record: The refusal's kind and the arguments it was built from, as
                :func:`refusal_from_record` reads them back.

        Returns:
            None.
        """
        if self._refusal_cache_ttl_seconds <= 0:
            return
        try:
            cache.set(
                _refusal_cache_key(normalized_name),
                record,
                timeout=self._refusal_cache_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning("Refusal cache unwritable for %r: %s", normalized_name, exc)

    def _clear_refusal(self, normalized_name: str) -> None:
        """Forget any cached refusal for a designation that now resolves.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            None.
        """
        try:
            cache.delete(_refusal_cache_key(normalized_name))
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning(
                "Refusal cache not clearable for %r: %s", normalized_name, exc
            )

    async def get_spectrum_data(
        self,
        file: Optional[Any] = None,
        object_name: Optional[str] = None,
        source_spectrum_id: Optional[str] = None,
    ) -> Spectrum:
        """Read a spectrum from an upload or from an object designation.

        Args:
            file: An uploaded file object, when the researcher supplied one.
            object_name: The object designation to resolve against WISeREP,
                when no file was supplied.
            source_spectrum_id: The source's identifier for the epoch the
                researcher chose, when they chose one. Ignored for an upload.

        Returns:
            The spectrum read from whichever source was supplied.

        Raises:
            ValidationException: If neither a file nor a designation was
                provided.
            SpectrumValidationException: If the spectrum is invalid.
            FileReadException: If an uploaded file cannot be read.
            AmbiguousDesignationException: If the designation matches more
                than one object.
            NoEligibleSpectrumException: If it resolves to no eligible
                spectrum.
            IncompleteSourceExportException: If WISeREP's export could not be
                read to exhaustion.
            WiserepUnreachableError: If WISeREP could not be reached.
            WiserepUnusableSpectrumError: If the selected spectrum cannot be
                classified as fetched.
            StorageException: If the spectrum store itself failed. Reported as
                the server's own failure rather than as an unusable spectrum,
                and without the underlying exception's text.
        """
        try:
            if file:
                logger.debug(
                    f"Processing uploaded file: {getattr(file, 'name', getattr(file, 'filename', 'unknown'))}"
                )
                # Validate file extension (now supports .fits as well)
                validate_file_extension(
                    getattr(file, "name", getattr(file, "filename", "")),
                    [
                        ".dat",
                        ".lnw",
                        ".txt",
                        ".fits",
                        ".ascii",
                        ".csv",
                        ".flm",
                        ".spec",
                    ],
                )
                return await self.get_spectrum_from_file(file)
            elif object_name:
                logger.debug(f"Processing object designation: {object_name}")
                return await self.get_spectrum_by_name(
                    object_name, source_spectrum_id=source_spectrum_id
                )
            else:
                raise ValidationException(
                    "No spectrum file or object designation provided"
                )
        except _PASS_THROUGH_FAILURES:
            raise
        except _INFRASTRUCTURE_FAILURES as exc:
            logger.exception("Storage failure getting spectrum data: %s", exc)
            raise StorageException(_INFRASTRUCTURE_FAILURE_MESSAGE) from exc
        except Exception as e:
            logger.exception("Error getting spectrum data: %s", e)
            raise SpectrumValidationException(_UNCLASSIFIED_FAILURE_MESSAGE) from e

    async def save_spectrum(self, spectrum: Spectrum) -> Spectrum:
        """
        Save spectrum to database.

        Args:
            spectrum: Spectrum object to save

        Returns:
            Spectrum object (after saving)

        Raises:
            SpectrumValidationException: If spectrum validation fails
        """
        try:
            logger.debug(f"Saving spectrum {spectrum.id} to database")

            # Validate spectrum before saving
            validate_spectrum(spectrum.x, spectrum.y, spectrum.redshift)

            # Save to database using async wrapper
            saved_spectrum = await run_db_work(self.db_repo.save, spectrum)

            logger.debug(f"Successfully saved spectrum {spectrum.id} to database")
            return saved_spectrum

        except Exception as e:
            logger.error(f"Error saving spectrum to database: {e}")
            raise SpectrumValidationException(f"Failed to save spectrum: {str(e)}")
