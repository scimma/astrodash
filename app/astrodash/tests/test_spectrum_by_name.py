"""Tests for the by-name lookup behind the shared spectrum seam.

Everything here runs against stubs: a transport that answers export rows from
a queue and counts what it was asked for, a WISeREP repository that answers
downloads the same way, and in-memory stand-ins for retention and the stored
candidate set. Nothing reaches the network.

The counters are the point of most cases. A repeat lookup of a retained
spectrum must leave both counters at zero -- that is what "no outbound
request" means (R13, AE5) -- and a refusal must leave the candidate set
unstored when the export it would have been ranked from was partial (R22,
AE19).
"""

import asyncio
from typing import Any, Dict, List, Optional
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from astrodash.core.exceptions import (
    AmbiguousDesignationException,
    IncompleteSourceExportException,
    NoEligibleSpectrumException,
    SpectrumValidationException,
    ValidationException,
)
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.services import spectrum_service as service_module
from astrodash.domain.services.spectrum_service import SpectrumService
from astrodash.infrastructure.django_repositories import (
    DjangoCandidateSetRepository,
    DjangoSpectrumRepository,
)
from astrodash.infrastructure.storage.wiserep_spectrum_repository import (
    WiserepUnusableSpectrumError,
)
from astrodash.infrastructure.wiserep.client import (
    WiserepIncompleteExportError,
    WiserepUnreachableError,
)
from astrodash.models import SOURCE_NAME_WISEREP, SpectrumRecord
from astrodash.infrastructure.wiserep.selection import normalize_designation

#: The designation submitted throughout, and the object WISeREP spells it as.
SUBMITTED_NAME = "SN2002er"
WISEREP_DESIGNATION = "SN 2002er"
SPECTRUM_ID = "5001"

#: One eligible export row for that object, carrying the columns eligibility
#: and ranking read.
EXPORT_ROW: Dict[str, str] = {
    "IAU name": WISEREP_DESIGNATION,
    "Internal name/s": "",
    "Obj. Type": "SN Ia",
    "Redshift": "0.0086",
    "Spec. ID": SPECTRUM_ID,
    "Obs-date": "2002-08-30 00:00:00",
    "JD": "2452516.5",
    "Telescope": "WHT",
    "Instrument": "ISIS",
    "Public": "Y",
    "Ascii file": "https://www.wiserep.org/system/files/2002er.txt",
    "Spec. type": "Object",
    "Spec. quality": "",
}

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "spectrum-by-name-tests",
    }
}


def export_row(**overrides: str) -> Dict[str, str]:
    """Build one export row.

    Args:
        **overrides: Column values to replace.

    Returns:
        A copy of :data:`EXPORT_ROW` with the overrides applied.
    """
    row = dict(EXPORT_ROW)
    row.update(overrides)
    return row


def spectrum(points: int = 200) -> Spectrum:
    """Build the spectrum a fetch would return for the eligible row.

    Args:
        points: How many wavelength points to carry.

    Returns:
        A spectrum carrying the designation, the WISeREP spectrum identifier,
        the source name and a retrieval time.
    """
    return Spectrum(
        x=[4000.0 + index for index in range(points)],
        y=[1.0] * points,
        redshift=0.0086,
        object_designation=WISEREP_DESIGNATION,
        source_name=SOURCE_NAME_WISEREP,
        source_spectrum_id=SPECTRUM_ID,
        retrieved_at=timezone.now(),
        file_name="2002er.txt",
    )


class StubClient:
    """A transport that answers export rows and counts its calls."""

    def __init__(
        self,
        rows: Optional[List[Dict[str, str]]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Build the transport.

        Args:
            rows: The rows to answer a search with.
            error: An exception to raise instead of answering.
        """
        self.rows = rows if rows is not None else [EXPORT_ROW]
        self.error = error
        self.searches: List[str] = []

    def search_spectra_rows(self, designation: str) -> List[Dict[str, str]]:
        """Answer the configured rows, or raise the configured failure.

        Args:
            designation: The designation searched for, recorded.

        Returns:
            The configured rows.

        Raises:
            Exception: The configured failure, when one was given.
        """
        self.searches.append(designation)
        if self.error is not None:
            raise self.error
        return list(self.rows)


class StubWiserepRepo:
    """A WISeREP repository that answers one download and counts its calls."""

    def __init__(
        self,
        fetched: Optional[Spectrum] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Build the repository.

        Args:
            fetched: The spectrum to answer with.
            error: An exception to raise instead of answering.
        """
        self.fetched = fetched
        self.error = error
        self.downloads: List[str] = []

    def get_for_candidate(self, candidate: Any) -> Spectrum:
        """Answer the configured spectrum, or raise the configured failure.

        Args:
            candidate: The chosen candidate, whose identifier is recorded.

        Returns:
            The configured spectrum.

        Raises:
            Exception: The configured failure, when one was given.
        """
        self.downloads.append(candidate.spectrum_id)
        if self.error is not None:
            raise self.error
        assert self.fetched is not None, "unexpected download"
        return self.fetched


class StubDbRepo:
    """Retention held in memory."""

    def __init__(self, retained: Optional[Spectrum] = None) -> None:
        """Build the store.

        Args:
            retained: A spectrum already retained for the eligible epoch.
        """
        self.retained = retained
        self.saved: List[Spectrum] = []

    def get_by_designation_and_spectrum_id(
        self, object_designation: str, source_spectrum_id: str
    ) -> Optional[Spectrum]:
        """Answer the retained spectrum for the requested epoch.

        Args:
            object_designation: The designation looked up.
            source_spectrum_id: The source's identifier for the spectrum.

        Returns:
            The retained spectrum, when one matches.
        """
        if self.retained is None:
            return None
        if (
            self.retained.object_designation == object_designation
            and self.retained.source_spectrum_id == source_spectrum_id
        ):
            return self.retained
        return None

    def save(self, to_save: Spectrum) -> Spectrum:
        """Record the spectrum as retained.

        Args:
            to_save: The spectrum to retain.

        Returns:
            That spectrum, with an id.
        """
        to_save.id = "retained-1"
        self.saved.append(to_save)
        self.retained = to_save
        return to_save


class StubCandidateSets:
    """A candidate-set store held in memory, counting its reads."""

    def __init__(self, stored: Optional[List[Dict[str, Any]]] = None) -> None:
        """Build the store.

        Args:
            stored: A candidate set already stored for the designation.
        """
        self.stored = stored
        self.reads: List[str] = []
        self.writes: List[str] = []

    def get(self, normalized_name: str) -> Optional[Dict[str, Any]]:
        """Answer the stored candidate set.

        Args:
            normalized_name: The normalized designation looked up.

        Returns:
            The stored set, or None when none is held.
        """
        self.reads.append(normalized_name)
        if self.stored is None:
            return None
        return {"candidates": self.stored, "resolved_at": timezone.now()}

    def save(
        self,
        normalized_name: str,
        candidates: List[Dict[str, Any]],
        resolved_at: Any = None,
    ) -> None:
        """Store a candidate set.

        Args:
            normalized_name: The normalized designation it was resolved for.
            candidates: The ranked candidate records.
            resolved_at: When it was resolved; unused here.

        Returns:
            None.
        """
        self.writes.append(normalized_name)
        self.stored = candidates


def build_service(
    client: StubClient,
    wiserep_repo: StubWiserepRepo,
    db_repo: Any,
    candidate_sets: Any,
) -> SpectrumService:
    """Build the service over stubs.

    Args:
        client: The transport resolution reads through.
        wiserep_repo: The repository downloads go through.
        db_repo: The retention store.
        candidate_sets: The candidate-set store.

    Returns:
        The service under test.
    """
    return SpectrumService(
        file_repo=None,
        wiserep_repo=wiserep_repo,
        db_repo=db_repo,
        candidate_set_repo=candidate_sets,
        wiserep_client=client,
        settings=mock.Mock(),
    )


def lookup(service: SpectrumService, name: str = SUBMITTED_NAME) -> Spectrum:
    """Run one by-name lookup through the shared seam.

    Args:
        service: The service under test.
        name: The designation to submit.

    Returns:
        The spectrum the seam produced.
    """
    return asyncio.run(service.get_spectrum_data(object_name=name))


def stored_records() -> List[Dict[str, Any]]:
    """Build the candidate records a prior resolution would have stored.

    Returns:
        A one-entry ranked candidate set for the eligible epoch.
    """
    client = StubClient()
    sets = StubCandidateSets()
    service = build_service(client, StubWiserepRepo(spectrum()), StubDbRepo(), sets)
    with mock.patch.object(service_module, "cache", _unavailable_cache()):
        lookup(service)
    return list(sets.stored or [])


def _unavailable_cache() -> mock.Mock:
    """Build a cache stand-in whose every operation fails.

    Returns:
        A mock raising ``ConnectionError`` from ``get`` and ``set``, which is
        what a worker sees in the slim profiles, where Redis is absent.
    """
    unavailable = mock.Mock()
    unavailable.get.side_effect = ConnectionError("no cache backend")
    unavailable.set.side_effect = ConnectionError("no cache backend")
    return unavailable


@override_settings(CACHES=LOCMEM_CACHE)
class RetentionReuseTests(SimpleTestCase):
    """A retained spectrum is served without consulting WISeREP."""

    def setUp(self) -> None:
        cache.clear()

    def test_second_lookup_issues_no_outbound_request(self) -> None:
        """Covers AE5: the held copy is served, with its retrieval time."""
        client = StubClient()
        wiserep_repo = StubWiserepRepo(spectrum())
        db_repo = StubDbRepo()
        sets = StubCandidateSets()
        service = build_service(client, wiserep_repo, db_repo, sets)

        first = lookup(service)
        self.assertEqual(len(client.searches), 1)
        self.assertEqual(len(wiserep_repo.downloads), 1)

        second = lookup(service)
        self.assertEqual(len(client.searches), 1, "resolution was repeated")
        self.assertEqual(len(wiserep_repo.downloads), 1, "the spectrum was re-fetched")
        self.assertEqual(second.source_spectrum_id, first.source_spectrum_id)
        self.assertEqual(second.retrieved_at, first.retrieved_at)

    def test_cache_hit_does_not_read_the_stored_set(self) -> None:
        """The cache is a fast path in front of the stored candidate set."""
        client = StubClient()
        sets = StubCandidateSets()
        service = build_service(client, StubWiserepRepo(spectrum()), StubDbRepo(), sets)
        lookup(service)
        reads_after_first = len(sets.reads)

        lookup(service)
        self.assertEqual(len(sets.reads), reads_after_first)

    def test_cache_miss_falls_through_to_the_stored_set(self) -> None:
        """An expired cache entry is re-read from storage, not re-resolved."""
        records = stored_records()
        client = StubClient()
        wiserep_repo = StubWiserepRepo()
        db_repo = StubDbRepo(spectrum())
        sets = StubCandidateSets(records)
        service = build_service(client, wiserep_repo, db_repo, sets)

        result = lookup(service)
        self.assertEqual(sets.reads, [normalize_designation(SUBMITTED_NAME)])
        self.assertEqual(client.searches, [])
        self.assertEqual(wiserep_repo.downloads, [])
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)


class AbsentCacheBackendTests(SimpleTestCase):
    """The lookup works with no cache backend reachable at all."""

    def test_first_lookup_succeeds_without_a_cache(self) -> None:
        """An unreachable cache leaves the lookup working, not failing."""
        client = StubClient()
        wiserep_repo = StubWiserepRepo(spectrum())
        service = build_service(client, wiserep_repo, StubDbRepo(), StubCandidateSets())
        with mock.patch.object(service_module, "cache", _unavailable_cache()):
            result = lookup(service)
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)

    def test_repeat_lookup_issues_no_outbound_request_without_a_cache(self) -> None:
        """Covers AE5 with the cache absent: the stored set carries the reuse."""
        records = stored_records()
        client = StubClient()
        wiserep_repo = StubWiserepRepo()
        service = build_service(
            client, wiserep_repo, StubDbRepo(spectrum()), StubCandidateSets(records)
        )
        with mock.patch.object(service_module, "cache", _unavailable_cache()):
            result = lookup(service)
        self.assertEqual(client.searches, [])
        self.assertEqual(wiserep_repo.downloads, [])
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)


@override_settings(CACHES=LOCMEM_CACHE)
class DistinctFailureTests(SimpleTestCase):
    """Each way the lookup refuses carries its own class and status."""

    def setUp(self) -> None:
        cache.clear()

    def _refuse(self, client: StubClient, name: str = SUBMITTED_NAME) -> Exception:
        """Run a lookup expected to refuse and return the failure.

        Args:
            client: The transport to resolve through.
            name: The designation to submit.

        Returns:
            The exception the seam raised.
        """
        service = build_service(
            client, StubWiserepRepo(), StubDbRepo(), StubCandidateSets()
        )
        with self.assertRaises(Exception) as caught:
            lookup(service, name)
        return caught.exception

    def test_unreachable_source_and_empty_resolution_differ(self) -> None:
        """Covers AE7: the two conditions are reported distinctly."""
        unreachable = self._refuse(
            StubClient(error=WiserepUnreachableError("connection refused"))
        )
        empty = self._refuse(StubClient(rows=[export_row(Public="N")]))

        self.assertIsInstance(unreachable, WiserepUnreachableError)
        self.assertIsInstance(empty, NoEligibleSpectrumException)
        self.assertNotEqual(unreachable.status_code, empty.status_code)

    def test_five_conditions_carry_five_distinct_statuses(self) -> None:
        """Every refusal is distinguishable by class and by status."""
        failures = [
            self._refuse(StubClient(error=WiserepUnreachableError("refused"))),
            self._refuse(
                StubClient(error=WiserepIncompleteExportError("page cap reached"))
            ),
            self._refuse(
                StubClient(
                    rows=[
                        export_row(**{"IAU name": "SN 2002ea"}),
                        export_row(**{"IAU name": "SN 2002eb", "Spec. ID": "5002"}),
                    ]
                ),
                name="2002e",
            ),
            self._refuse(StubClient(rows=[export_row(Public="N")])),
            self._unusable_failure(),
        ]
        self.assertEqual(
            [type(failure) for failure in failures],
            [
                WiserepUnreachableError,
                IncompleteSourceExportException,
                AmbiguousDesignationException,
                NoEligibleSpectrumException,
                WiserepUnusableSpectrumError,
            ],
        )
        statuses = [failure.status_code for failure in failures]
        self.assertEqual(len(set(statuses)), 5, f"statuses collapsed: {statuses}")

    def _unusable_failure(self) -> Exception:
        """Run a lookup whose download is refused as unusable.

        Returns:
            The exception the seam raised.
        """
        service = build_service(
            StubClient(),
            StubWiserepRepo(error=WiserepUnusableSpectrumError("too few points")),
            StubDbRepo(),
            StubCandidateSets(),
        )
        with self.assertRaises(WiserepUnusableSpectrumError) as caught:
            lookup(service)
        return caught.exception

    def test_no_failure_is_relabelled_as_a_validation_error(self) -> None:
        """Covers KTD6: the outer handler re-raises each class unchanged."""
        for failure in (
            self._refuse(StubClient(error=WiserepUnreachableError("refused"))),
            self._refuse(
                StubClient(error=WiserepIncompleteExportError("page cap reached"))
            ),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.assertNotIsInstance(failure, SpectrumValidationException)

    def test_incomplete_export_is_never_ranked_or_stored(self) -> None:
        """Covers AE19: nothing is ranked from the pages already fetched."""
        client = StubClient(
            error=WiserepIncompleteExportError(
                "export for 'SN2002er' still had rows outstanding"
            )
        )
        wiserep_repo = StubWiserepRepo()
        sets = StubCandidateSets()
        service = build_service(client, wiserep_repo, StubDbRepo(), sets)

        with self.assertRaises(IncompleteSourceExportException):
            lookup(service)

        self.assertEqual(sets.writes, [], "a partial set was stored")
        self.assertIsNone(sets.stored)
        self.assertEqual(wiserep_repo.downloads, [], "a spectrum was fetched anyway")

    def test_retained_spectrum_that_no_longer_validates(self) -> None:
        """An unusable held copy is not reported as the source being down."""
        broken = spectrum()
        broken.y = []
        records = stored_records()
        client = StubClient()
        wiserep_repo = StubWiserepRepo()
        service = build_service(
            client, wiserep_repo, StubDbRepo(broken), StubCandidateSets(records)
        )

        with self.assertRaises(WiserepUnusableSpectrumError):
            lookup(service)
        self.assertEqual(wiserep_repo.downloads, [])
        self.assertEqual(client.searches, [])

    def test_a_designation_with_nothing_comparable_is_a_validation_error(self) -> None:
        """A blank designation never reaches WISeREP."""
        client = StubClient()
        service = build_service(
            client, StubWiserepRepo(), StubDbRepo(), StubCandidateSets()
        )
        with self.assertRaises(ValidationException):
            lookup(service, "   ")
        self.assertEqual(client.searches, [])


@override_settings(CACHES=LOCMEM_CACHE)
class InteractiveRetentionTests(TransactionTestCase):
    """A first by-name lookup leaves a retained row in the database.

    The interactive classify view reads its spectrum by calling
    ``get_spectrum_data(file=..., object_name=...)``, which is what these
    cases call, over the real Django repositories rather than stubs.

    These cases commit rather than running inside a test transaction: the seam
    reaches storage through ``asyncio.to_thread``, and a worker thread holds
    its own database connection, which would not see an uncommitted
    transaction opened on the main thread.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_first_lookup_retains_the_spectrum_with_its_provenance(self) -> None:
        """Covers AE15: designation, spectrum identifier and retrieval time."""
        fetched = spectrum()
        service = SpectrumService(
            file_repo=None,
            wiserep_repo=StubWiserepRepo(fetched),
            db_repo=DjangoSpectrumRepository(),
            candidate_set_repo=DjangoCandidateSetRepository(),
            wiserep_client=StubClient(),
            settings=mock.Mock(),
        )

        lookup(service)

        record = SpectrumRecord.objects.get(
            object_designation=WISEREP_DESIGNATION, source_spectrum_id=SPECTRUM_ID
        )
        self.assertEqual(record.source_name, SOURCE_NAME_WISEREP)
        self.assertEqual(record.created_at, fetched.retrieved_at)
        self.assertEqual(len(record.x), len(fetched.x))

    def test_repeat_lookup_reuses_the_retained_row(self) -> None:
        """Covers AE5 end to end: one row, no second download."""
        wiserep_repo = StubWiserepRepo(spectrum())
        client = StubClient()
        service = SpectrumService(
            file_repo=None,
            wiserep_repo=wiserep_repo,
            db_repo=DjangoSpectrumRepository(),
            candidate_set_repo=DjangoCandidateSetRepository(),
            wiserep_client=client,
            settings=mock.Mock(),
        )

        lookup(service)
        lookup(service)

        self.assertEqual(len(wiserep_repo.downloads), 1)
        self.assertEqual(len(client.searches), 1)
        self.assertEqual(
            SpectrumRecord.objects.filter(
                object_designation=WISEREP_DESIGNATION
            ).count(),
            1,
        )

    def test_the_stored_candidate_set_carries_the_selected_epoch(self) -> None:
        """The stored set is what a later lookup reaches retention through."""
        service = SpectrumService(
            file_repo=None,
            wiserep_repo=StubWiserepRepo(spectrum()),
            db_repo=DjangoSpectrumRepository(),
            candidate_set_repo=DjangoCandidateSetRepository(),
            wiserep_client=StubClient(),
            settings=mock.Mock(),
        )
        lookup(service)

        stored = DjangoCandidateSetRepository().get(
            normalize_designation(SUBMITTED_NAME)
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored["candidates"][0]["source_spectrum_id"], SPECTRUM_ID)
        self.assertEqual(
            stored["candidates"][0]["instrument"], EXPORT_ROW["Instrument"]
        )
