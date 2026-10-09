"""Spectrum sources behind the application's shared entry seam.

:meth:`SpectrumService.get_spectrum_data` is the one seam both the
interactive classify view and the REST endpoint read a spectrum through. It
takes either an uploaded file or an object designation; a designation is
resolved against WISeREP, ranked, fetched and retained.

Resolution is looked up before it is performed: the Django cache is consulted
first as a fast path, then the durably stored candidate set, and WISeREP is
asked only when neither holds a set for the designation. The cache fails open,
because the slim profiles run without Redis, while the stored set is what makes
the no-outbound-request guarantee hold -- retention is keyed by a spectrum
identifier that only resolution produces.

Every way the lookup can refuse carries its own failure class and its own
status, and each one is registered in the allowlist
:data:`_PASS_THROUGH_FAILURES` that :meth:`SpectrumService.get_spectrum_data`
re-raises unchanged. Anything outside that tuple is relabelled as a spectrum
data error, which would collapse the distinctions the surfaces report.
"""

import asyncio
from typing import Any, Dict, List, Mapping, Optional

from django.core.cache import cache

from astrodash.config.logging import get_logger
from astrodash.config.settings import Settings, get_settings
from astrodash.core.exceptions import (
    AmbiguousDesignationException,
    FileReadException,
    IncompleteSourceExportException,
    NoEligibleSpectrumException,
    SpectrumValidationException,
    ValidationException,
)
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.repositories.spectrum_repository import SpectrumRepository
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
# nothing: an expired entry is re-read from storage, not re-resolved.
_CANDIDATE_CACHE_PREFIX = "wiserep:candidates:"

# How long a cached candidate set stays readable.
_CANDIDATE_CACHE_TTL_SECONDS = 3600


def _candidate_cache_key(normalized_name: str) -> str:
    """Build the cache key for one designation's candidate set.

    Args:
        normalized_name: The submitted designation, already normalized.

    Returns:
        The cache key holding that designation's ranked candidate set.
    """
    return f"{_CANDIDATE_CACHE_PREFIX}{normalized_name}"


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
    )


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
)


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

    async def get_spectrum_from_file(self, file: Any) -> Spectrum:
        logger.debug(f"Getting spectrum from file: {getattr(file, 'name', getattr(file, 'filename', 'unknown'))}")
        spectrum = await asyncio.to_thread(self.file_repo.get_from_file, file)
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

    async def get_spectrum_by_name(self, object_name: str) -> Spectrum:
        """Read the spectrum a submitted object designation resolves to.

        Resolution is looked up before it is performed -- cache, then the
        stored candidate set, then WISeREP -- and the selected epoch is served
        from retention when it is already held, so a repeat lookup makes no
        outbound request. A newly fetched spectrum is retained with the
        identifiers that locate it upstream and the time it was retrieved.

        Args:
            object_name: The designation the researcher submitted, in any
                accepted form (``2002er``, ``SN2002er``, ``ATLAS26hqe``).

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

        records = await asyncio.to_thread(self._load_candidate_records, normalized)
        if records is None:
            records = await asyncio.to_thread(
                self._resolve_candidate_records, object_name, normalized
            )

        selected = candidate_from_record(records[0])
        logger.debug(
            "Selected WISeREP spectrum %s for %r (%s, %s)",
            selected.spectrum_id,
            object_name,
            selected.observation_date,
            selected.instrument,
        )

        retained = await asyncio.to_thread(self._retained_spectrum, selected)
        if retained is not None:
            logger.info(
                "Serving retained spectrum %s for %r, retrieved %s",
                selected.spectrum_id,
                object_name,
                retained.retrieved_at,
            )
            return retained

        spectrum = await asyncio.to_thread(
            self.wiserep_repo.get_for_candidate, selected
        )
        return await asyncio.to_thread(self.db_repo.save, spectrum)

    def _load_candidate_records(
        self, normalized_name: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Look up an already resolved candidate set for a designation.

        The cache is read first as a fast path; a miss there falls through to
        the stored set rather than re-resolving, and a stored set found that
        way is written back to the cache.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            The ranked candidate records, earliest first, or ``None`` when
            neither the cache nor storage holds a set for the designation.
        """
        cached = self._cache_read(normalized_name)
        if cached:
            logger.debug("Candidate set for %r served from cache", normalized_name)
            return list(cached)

        stored = self.candidate_set_repo.get(normalized_name)
        records = (stored or {}).get("candidates")
        if records:
            logger.debug("Candidate set for %r served from storage", normalized_name)
            self._cache_write(normalized_name, records)
            return list(records)

        return None

    def _resolve_candidate_records(
        self, object_name: str, normalized_name: str
    ) -> List[Dict[str, Any]]:
        """Resolve a designation against WISeREP and store what it produced.

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
            raise IncompleteSourceExportException(object_name, exc.message) from exc

        result = rank_candidates(rows, object_name)
        if result.refusal is RefusalReason.AMBIGUOUS_NAME:
            raise AmbiguousDesignationException(
                object_name, [summary.designation for summary in result.objects]
            )
        if not result.candidates:
            raise NoEligibleSpectrumException(object_name)

        records = [candidate_to_record(item) for item in result.candidates]
        self.candidate_set_repo.save(normalized_name, records)
        self._cache_write(normalized_name, records)
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
            WiserepUnusableSpectrumError: If a retained spectrum is held but
                its stored data no longer validates, which is a condition of
                the held copy rather than of the source.
        """
        try:
            retained = self.db_repo.get_by_designation_and_spectrum_id(
                candidate.designation, candidate.spectrum_id
            )
        except Exception as exc:  # noqa: BLE001 - retention lookup is best effort
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

    def _cache_read(self, normalized_name: str) -> Optional[List[Dict[str, Any]]]:
        """Read a candidate set from the cache, tolerating its absence.

        Args:
            normalized_name: The submitted designation, already normalized.

        Returns:
            The cached candidate records, or ``None`` when the cache holds
            none or cannot be reached.
        """
        try:
            return cache.get(_candidate_cache_key(normalized_name))
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning(
                "Candidate set cache unreadable for %r: %s", normalized_name, exc
            )
            return None

    def _cache_write(self, normalized_name: str, records: List[Dict[str, Any]]) -> None:
        """Write a candidate set to the cache, tolerating its absence.

        Args:
            normalized_name: The submitted designation, already normalized.
            records: The ranked candidate records to cache.

        Returns:
            None.
        """
        try:
            cache.set(
                _candidate_cache_key(normalized_name),
                records,
                timeout=_CANDIDATE_CACHE_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - the cache is a fast path
            logger.warning(
                "Candidate set cache unwritable for %r: %s", normalized_name, exc
            )

    async def get_spectrum_data(
        self, file: Optional[Any] = None, object_name: Optional[str] = None
    ) -> Spectrum:
        """Read a spectrum from an upload or from an object designation.

        Args:
            file: An uploaded file object, when the researcher supplied one.
            object_name: The object designation to resolve against WISeREP,
                when no file was supplied.

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
                return await self.get_spectrum_by_name(object_name)
            else:
                raise ValidationException(
                    "No spectrum file or object designation provided"
                )
        except _PASS_THROUGH_FAILURES:
            raise
        except Exception as e:
            logger.error(f"Error getting spectrum data: {e}")
            raise SpectrumValidationException(f"Spectrum data error: {str(e)}")

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
            saved_spectrum = await asyncio.to_thread(self.db_repo.save, spectrum)

            logger.debug(f"Successfully saved spectrum {spectrum.id} to database")
            return saved_spectrum

        except Exception as e:
            logger.error(f"Error saving spectrum to database: {e}")
            raise SpectrumValidationException(f"Failed to save spectrum: {str(e)}")
