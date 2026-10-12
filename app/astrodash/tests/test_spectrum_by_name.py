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

The five refusal conditions are covered twice over. The service-level cases
prove the seam raises five distinguishable classes carrying the five statuses
in :data:`UNREACHABLE_STATUS` and its neighbours; the view-level cases drive
the REST endpoint over the same stubs and prove each of those statuses reaches
the client, in the JSON error envelope, rather than being flattened or
relabelled on the way out (AE7, KTD6).
"""

import asyncio
import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock

from django.core.cache import cache
from django.db import OperationalError
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from astrodash.core.exceptions import (
    AmbiguousDesignationException,
    IncompleteSourceExportException,
    NoEligibleSpectrumException,
    SpectrumValidationException,
    StorageException,
    ValidationException,
)
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.repositories.spectrum_repository import RetainedSpectrumStore
from astrodash.domain.services import spectrum_service as service_module
from astrodash.domain.services.spectrum_service import SpectrumService
from astrodash.infrastructure.django_repositories import (
    DjangoCandidateSetRepository,
    DjangoSpectrumRepository,
)
from astrodash.infrastructure.storage.file_spectrum_repository import (
    FileSpectrumRepository,
)
from astrodash.infrastructure.storage.wiserep_spectrum_repository import (
    WiserepUnusableSpectrumError,
)
from astrodash.infrastructure.wiserep.client import (
    WiserepIncompleteExportError,
    WiserepUnreachableError,
)
from astrodash.infrastructure.wiserep.parsing import (
    extract_csv_text_from_zip,
    parse_csv_rows,
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

#: The status each by-name refusal answers with. Written as literals, not read
#: off the exception classes, so that renumbering a refusal fails here instead
#: of being carried along by the assertion. A consumer distinguishes the five
#: conditions by these numbers, so they are part of the contract (AE7).
UNREACHABLE_STATUS = 500
INCOMPLETE_EXPORT_STATUS = 502
AMBIGUOUS_STATUS = 409
NO_ELIGIBLE_STATUS = 404
UNUSABLE_STATUS = 422

#: The status a failure of the server's own storage answers with. Not one of
#: the five: it is what a database failure reports instead of borrowing the
#: unusable-spectrum status, which would send a researcher to look at a
#: spectrum that is fine.
STORAGE_FAILURE_STATUS = 500

#: Captured WISeREP exports, used where one fits rather than invented rows.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wiserep"

#: A real export for the bare year ``2002``: fifty rows spanning ten distinct
#: objects, none of them spelled ``2002``, which is the ambiguity refusal.
MULTI_OBJECT_FIXTURE = "name_2002_multi_object_page0.zip"
MULTI_OBJECT_NAME = "2002"

#: The delimiter WISeREP's spectrum export uses.
EXPORT_DELIMITER = "\t"


def fixture_rows(name: str) -> List[Dict[str, str]]:
    """Read a captured export's rows as the transport would hand them over.

    Args:
        name: The fixture's filename under ``fixtures/wiserep/``.

    Returns:
        One dictionary per data row, keyed by WISeREP's own column names.
    """
    text = extract_csv_text_from_zip(
        (FIXTURES / name).read_bytes(), delimiter=EXPORT_DELIMITER
    )
    return parse_csv_rows(text, delimiter=EXPORT_DELIMITER)


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

    def __init__(
        self,
        stored: Optional[List[Dict[str, Any]]] = None,
        resolved_at: Any = None,
    ) -> None:
        """Build the store.

        Args:
            stored: A candidate set already stored for the designation.
            resolved_at: When that set was resolved, which decides whether it
                is still current. Defaults to the moment it is read, i.e. a
                set that has just been resolved.
        """
        self.stored = stored
        self.resolved_at = resolved_at
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
        return {
            "candidates": self.stored,
            "resolved_at": self.resolved_at or timezone.now(),
        }

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
            resolved_at: When it was resolved.

        Returns:
            None.
        """
        self.writes.append(normalized_name)
        self.stored = candidates
        self.resolved_at = resolved_at


def stub_settings(**overrides: float) -> SimpleNamespace:
    """Build a settings stand-in carrying the resolution-reuse durations.

    Args:
        **overrides: Durations to replace, by settings field name.

    Returns:
        A settings stand-in holding the deployed defaults, overridden as
        asked. Cases that do not care pass no settings at all, which also
        covers the service falling back to these same defaults when the
        settings object carries no usable number.
    """
    values: Dict[str, float] = {
        "wiserep_candidate_set_ttl_seconds": 86400.0,
        "wiserep_refusal_cache_ttl_seconds": 120.0,
        "wiserep_resolution_wait_seconds": 150.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def build_service(
    client: StubClient,
    wiserep_repo: StubWiserepRepo,
    db_repo: Any,
    candidate_sets: Any,
    settings: Any = None,
) -> SpectrumService:
    """Build the service over stubs.

    Args:
        client: The transport resolution reads through.
        wiserep_repo: The repository downloads go through.
        db_repo: The retention store.
        candidate_sets: The candidate-set store.
        settings: The settings to read the resolution-reuse durations from.
            A bare mock by default, which is what the service's fallback to
            its own defaults is for.

    Returns:
        The service under test.
    """
    return SpectrumService(
        file_repo=None,
        wiserep_repo=wiserep_repo,
        db_repo=db_repo,
        candidate_set_repo=candidate_sets,
        wiserep_client=client,
        settings=settings if settings is not None else mock.Mock(),
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

        The cache is cleared first because a refusal is now cached under the
        normalized designation: these cases drive several different refusals
        through one designation, and each must be the first lookup of it.

        Args:
            client: The transport to resolve through.
            name: The designation to submit.

        Returns:
            The exception the seam raised.
        """
        cache.clear()
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
        self.assertEqual(unreachable.status_code, UNREACHABLE_STATUS)
        self.assertEqual(empty.status_code, NO_ELIGIBLE_STATUS)

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
        self.assertEqual(
            statuses,
            [
                UNREACHABLE_STATUS,
                INCOMPLETE_EXPORT_STATUS,
                AMBIGUOUS_STATUS,
                NO_ELIGIBLE_STATUS,
                UNUSABLE_STATUS,
            ],
            f"the refusal statuses moved: {statuses}",
        )

    def _unusable_failure(self) -> Exception:
        """Run a lookup whose download is refused as unusable.

        Returns:
            The exception the seam raised.
        """
        cache.clear()
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


@override_settings(CACHES=LOCMEM_CACHE)
class RefusalStatusThroughTheApiTests(TransactionTestCase):
    """Each by-name refusal reaches the REST client as its own status.

    The cases above prove the seam raises five distinguishable failures; these
    prove the five statuses survive the view. The real :class:`SpectrumService`
    runs here, over the same stubs -- only the transport and the download are
    stood in for -- so a refusal originates where it does in production rather
    than being injected at the service boundary.

    Processing and classification are never reached: every case refuses during
    the lookup. They are stubbed anyway so that reaching them would surface as
    a wrong status rather than as a missing model weight.

    These cases commit rather than running inside a test transaction: the seam
    reaches storage through ``asyncio.to_thread``, and a worker thread holds its
    own database connection, which would not see an uncommitted transaction
    opened on the main thread.
    """

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()
        self.url = reverse("astrodash_api:process_spectrum")

    def _classify(
        self,
        stub_client: StubClient,
        wiserep_repo: Optional[StubWiserepRepo] = None,
        name: str = SUBMITTED_NAME,
    ) -> Any:
        """POST a by-name classify request and return the response.

        Args:
            stub_client: The transport resolution reads through.
            wiserep_repo: The repository the chosen download goes through;
                one that answers no download by default, since most cases
                refuse before reaching it.
            name: The designation to submit as ``objectName``.

        Returns:
            The view's response.
        """
        service = build_service(
            stub_client,
            wiserep_repo or StubWiserepRepo(),
            StubDbRepo(),
            StubCandidateSets(),
        )
        params = json.dumps({"modelType": "dash", "objectName": name})
        with mock.patch("astrodash.views.API_WRITES_ENABLED", True), mock.patch(
            "astrodash.views.get_spectrum_service", return_value=service
        ), mock.patch(
            "astrodash.views.get_spectrum_processing_service",
            return_value=mock.MagicMock(),
        ), mock.patch(
            "astrodash.views.get_classification_service",
            return_value=mock.MagicMock(),
        ):
            return self.client.post(self.url, data={"params": params})

    def _assert_refused(self, response: Any, status: int) -> Dict[str, Any]:
        """Assert a response is the JSON refusal envelope with that status.

        Args:
            response: The view's response.
            status: The status the refusal must carry.

        Returns:
            The decoded response body.
        """
        self.assertEqual(response.status_code, status)
        self.assertEqual(response["Content-Type"], "application/json")
        return response.json()

    def test_an_unreachable_source_answers_500(self) -> None:
        """Covers AE7: WISeREP being down is a server-side failure."""
        response = self._classify(
            StubClient(error=WiserepUnreachableError("connection refused"))
        )
        body = self._assert_refused(response, UNREACHABLE_STATUS)
        self.assertIn("WISeREP", body["detail"])

    def test_an_incomplete_export_answers_502(self) -> None:
        """Covers AE19: a truncated export is the upstream's failure, not ours."""
        response = self._classify(
            StubClient(
                error=WiserepIncompleteExportError(
                    f"export for {SUBMITTED_NAME!r} still had rows outstanding"
                )
            )
        )
        body = self._assert_refused(response, INCOMPLETE_EXPORT_STATUS)
        self.assertIn(SUBMITTED_NAME, body["detail"])

    def test_an_ambiguous_designation_answers_409(self) -> None:
        """A designation naming several objects conflicts, and names them.

        Driven from the captured export for the bare year ``2002``, whose fifty
        rows span ten objects and none of which is spelled ``2002``.
        """
        rows = fixture_rows(MULTI_OBJECT_FIXTURE)
        response = self._classify(StubClient(rows=rows), name=MULTI_OBJECT_NAME)

        body = self._assert_refused(response, AMBIGUOUS_STATUS)
        self.assertIn("more than one object", body["detail"])
        for designation in sorted({row["IAU name"] for row in rows}):
            self.assertIn(designation, body["detail"])

    def test_a_designation_with_no_eligible_spectrum_answers_404(self) -> None:
        """An embargoed spectrum is nothing to classify, not a source failure."""
        response = self._classify(StubClient(rows=[export_row(Public="N")]))
        body = self._assert_refused(response, NO_ELIGIBLE_STATUS)
        self.assertIn(SUBMITTED_NAME, body["detail"])

    def test_an_unusable_spectrum_answers_422(self) -> None:
        """A fetched spectrum that cannot be classified is unprocessable."""
        response = self._classify(
            StubClient(),
            wiserep_repo=StubWiserepRepo(
                error=WiserepUnusableSpectrumError("too few points in the window")
            ),
        )
        body = self._assert_refused(response, UNUSABLE_STATUS)
        self.assertIn("too few points", body["detail"])


#: A second, later epoch of the same object, as WISeREP would publish it after
#: the first lookup resolved the designation.
LATER_SPECTRUM_ID = "5002"
LATER_EXPORT_ROW = export_row(
    **{
        "Spec. ID": LATER_SPECTRUM_ID,
        "Obs-date": "2002-09-10 00:00:00",
        "JD": "2452527.5",
    }
)


@override_settings(CACHES=LOCMEM_CACHE)
class StoredCandidateSetLifetimeTests(SimpleTestCase):
    """A stored candidate set is re-resolved once it ages past its lifetime.

    This is where R13 is narrowed from "a repeat lookup makes no outbound
    request" to "a repeat lookup makes no outbound download". WISeREP keeps
    publishing epochs for an object it already holds spectra for, so a set
    resolved once and kept forever would hide every later epoch while the
    result line still called the served one the earliest eligible observation.
    """

    def setUp(self) -> None:
        cache.clear()

    def _service(
        self, client: StubClient, sets: StubCandidateSets, **settings: float
    ) -> Any:
        """Build a service whose retention already holds the first epoch.

        Args:
            client: The transport resolution would read through.
            sets: The candidate-set store, carrying the stored set and its age.
            **settings: Durations to override on the settings stand-in.

        Returns:
            A tuple of the service and the download stub, so a case can read
            back whether anything was fetched.
        """
        wiserep_repo = StubWiserepRepo(spectrum())
        service = build_service(
            client,
            wiserep_repo,
            StubDbRepo(spectrum()),
            sets,
            settings=stub_settings(**settings),
        )
        return service, wiserep_repo

    def test_a_set_within_its_lifetime_is_served_as_it_stands(self) -> None:
        """A fresh set still makes no outbound request at all."""
        sets = StubCandidateSets(
            stored_records(), resolved_at=timezone.now() - timedelta(hours=23)
        )
        client = StubClient()
        service, wiserep_repo = self._service(client, sets)

        result = lookup(service)

        self.assertEqual(client.searches, [])
        self.assertEqual(wiserep_repo.downloads, [])
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)

    def test_a_set_past_its_lifetime_is_re_resolved_without_a_download(self) -> None:
        """One search, no download, and the epoch published since is stored."""
        sets = StubCandidateSets(
            stored_records(), resolved_at=timezone.now() - timedelta(hours=25)
        )
        client = StubClient(rows=[EXPORT_ROW, LATER_EXPORT_ROW])
        service, wiserep_repo = self._service(client, sets)

        result = lookup(service)

        self.assertEqual(client.searches, [SUBMITTED_NAME], "the aged set was served")
        self.assertEqual(
            wiserep_repo.downloads, [], "the retained epoch was re-fetched"
        )
        self.assertEqual(
            [entry["source_spectrum_id"] for entry in sets.stored or []],
            [SPECTRUM_ID, LATER_SPECTRUM_ID],
            "the epoch published since the first lookup is still not offered",
        )
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)

    def test_an_unreachable_source_falls_back_to_the_aged_set(self) -> None:
        """An outage degrades freshness, not availability.

        Re-resolution exists to notice epochs published since, which is a
        freshness concern. A designation whose spectrum is already retained
        should keep working while WISeREP is down, because the aged set still
        locates a spectrum held in local storage.
        """
        sets = StubCandidateSets(
            stored_records(), resolved_at=timezone.now() - timedelta(hours=25)
        )
        client = StubClient(error=WiserepUnreachableError("WISeREP is down"))
        service, wiserep_repo = self._service(client, sets)

        result = lookup(service)

        self.assertEqual(client.searches, [SUBMITTED_NAME], "re-resolution was tried")
        self.assertEqual(
            wiserep_repo.downloads, [], "the retained epoch was re-fetched"
        )
        self.assertEqual(result.source_spectrum_id, SPECTRUM_ID)

    def test_an_unreachable_source_still_refuses_a_designation_never_resolved(
        self,
    ) -> None:
        """The fallback must not turn a real outage into a silent success."""
        sets = StubCandidateSets(None)
        client = StubClient(error=WiserepUnreachableError("WISeREP is down"))
        service, _ = self._service(client, sets)

        with self.assertRaises(WiserepUnreachableError):
            lookup(service)

    def test_the_lifetime_is_configurable(self) -> None:
        """A shorter configured lifetime ages a set that 24 hours would not."""
        sets = StubCandidateSets(
            stored_records(), resolved_at=timezone.now() - timedelta(minutes=5)
        )
        client = StubClient()
        service, _ = self._service(client, sets, wiserep_candidate_set_ttl_seconds=60.0)

        lookup(service)

        self.assertEqual(client.searches, [SUBMITTED_NAME])

    def test_a_set_of_unknown_age_is_re_resolved(self) -> None:
        """A set with no recorded resolution time cannot be aged, so it is."""
        sets = StubCandidateSets(stored_records())
        sets.resolved_at = None
        sets.get = lambda name: {"candidates": sets.stored, "resolved_at": None}
        client = StubClient()
        service, _ = self._service(client, sets)

        lookup(service)

        self.assertEqual(client.searches, [SUBMITTED_NAME])


@override_settings(CACHES=LOCMEM_CACHE)
class RefusalCacheTests(SimpleTestCase):
    """A refused designation does not re-paginate the export every time.

    The classify page is anonymous and the export takes 50 to 100 seconds to
    paginate, so a name that refuses is the cheapest way to occupy both
    workers. The cached refusal must be indistinguishable from a fresh one:
    same class, same status, same message.
    """

    def setUp(self) -> None:
        cache.clear()

    def _refuse_twice(
        self, client: StubClient, name: str = SUBMITTED_NAME
    ) -> List[Exception]:
        """Submit one designation twice over, expecting both to refuse.

        Args:
            client: The transport resolution reads through, shared by both
                submissions so its searches can be counted.
            name: The designation to submit.

        Returns:
            The two failures, in order.
        """
        service = build_service(
            client,
            StubWiserepRepo(),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        failures: List[Exception] = []
        for _ in range(2):
            with self.assertRaises(Exception) as caught:
                lookup(service, name)
            failures.append(caught.exception)
        return failures

    def _assert_identical(self, first: Exception, second: Exception) -> None:
        """Assert a cached refusal reports exactly what a fresh one did.

        Args:
            first: The fresh refusal.
            second: The one served from the cache.

        Returns:
            None.
        """
        self.assertIs(type(second), type(first))
        self.assertEqual(second.status_code, first.status_code)
        self.assertEqual(second.message, first.message)

    def test_no_eligible_spectrum_is_refused_from_the_cache(self) -> None:
        """An embargoed designation paginates the export once, not twice."""
        client = StubClient(rows=[export_row(Public="N")])
        first, second = self._refuse_twice(client)

        self.assertEqual(len(client.searches), 1, "the export was paginated again")
        self._assert_identical(first, second)
        self.assertIsInstance(second, NoEligibleSpectrumException)

    def test_an_incomplete_export_is_refused_from_the_cache(self) -> None:
        """The detail of where the export stopped survives the cache."""
        client = StubClient(
            error=WiserepIncompleteExportError("page cap reached at page 10")
        )
        first, second = self._refuse_twice(client)

        self.assertEqual(len(client.searches), 1)
        self._assert_identical(first, second)
        self.assertIn("page cap reached at page 10", second.message)

    def test_an_ambiguous_designation_is_refused_from_the_cache(self) -> None:
        """The matching objects are cached with the refusal, not re-derived."""
        client = StubClient(rows=fixture_rows(MULTI_OBJECT_FIXTURE))
        first, second = self._refuse_twice(client, name=MULTI_OBJECT_NAME)

        self.assertEqual(len(client.searches), 1)
        self._assert_identical(first, second)
        self.assertEqual(second.designations, first.designations)

    def test_an_unreachable_source_is_not_cached(self) -> None:
        """A transport failure is transient, so the next lookup tries again."""
        client = StubClient(error=WiserepUnreachableError("connection refused"))
        first, second = self._refuse_twice(client)

        self.assertEqual(len(client.searches), 2)
        self.assertIsInstance(second, WiserepUnreachableError)
        self._assert_identical(first, second)

    def test_an_absent_cache_leaves_the_lookup_resolving(self) -> None:
        """Covers the slim profiles: no cache degrades to doing the work."""
        client = StubClient(rows=[export_row(Public="N")])
        service = build_service(
            client,
            StubWiserepRepo(),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        with mock.patch.object(service_module, "cache", _unavailable_cache()):
            for _ in range(2):
                with self.assertRaises(NoEligibleSpectrumException):
                    lookup(service)

        self.assertEqual(len(client.searches), 2)

    def test_a_resolution_forgets_any_refusal_cached_for_the_name(self) -> None:
        """A refusal never outlives a set that resolved while it was cached."""
        normalized = normalize_designation(SUBMITTED_NAME)
        service = build_service(
            StubClient(),
            StubWiserepRepo(spectrum()),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        service._cache_refusal(normalized, {"kind": "no_eligible", "detail": None})
        self.assertIsNotNone(service._cached_refusal(normalized, SUBMITTED_NAME))

        service._resolve_candidate_records(SUBMITTED_NAME, normalized)

        self.assertIsNone(service._cached_refusal(normalized, SUBMITTED_NAME))

    def test_the_refusal_lifetime_is_configurable(self) -> None:
        """A non-positive lifetime caches nothing, which is the old behaviour."""
        client = StubClient(rows=[export_row(Public="N")])
        service = build_service(
            client,
            StubWiserepRepo(),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(wiserep_refusal_cache_ttl_seconds=0.0),
        )
        for _ in range(2):
            with self.assertRaises(NoEligibleSpectrumException):
                lookup(service)

        self.assertEqual(len(client.searches), 2)


class GatedClient(StubClient):
    """A transport that blocks inside the search until it is released."""

    def __init__(self, rows: Optional[List[Dict[str, str]]] = None) -> None:
        """Build the transport.

        Args:
            rows: The rows to answer a search with.
        """
        super().__init__(rows)
        self.entered = threading.Event()
        self.release = threading.Event()

    def search_spectra_rows(self, designation: str) -> List[Dict[str, str]]:
        """Announce the search, wait to be released, then answer it.

        Args:
            designation: The designation searched for.

        Returns:
            The configured rows.
        """
        self.entered.set()
        self.release.wait(timeout=10)
        return super().search_spectra_rows(designation)


@override_settings(CACHES=LOCMEM_CACHE)
class SingleFlightResolutionTests(SimpleTestCase):
    """One resolution per designation is in flight in a worker at a time.

    Two gunicorn workers at a 300-second timeout serve the anonymous classify
    page, so two submissions of one slow designation can occupy the whole
    application. The second caller waits on the first and reads its outcome.
    Nothing here caps how many lookups may run: a first-time lookup is never
    refused for being concurrent.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_a_second_caller_waits_rather_than_paginating_again(self) -> None:
        """One search and one download serve two concurrent submissions."""
        client = GatedClient()
        wiserep_repo = StubWiserepRepo(spectrum())
        service = build_service(
            client,
            wiserep_repo,
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        outcomes: Dict[str, Any] = {}

        def run(key: str) -> None:
            """Run one lookup, recording its outcome.

            Args:
                key: Which caller this is.

            Returns:
                None.
            """
            try:
                outcomes[key] = lookup(service)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                outcomes[key] = exc

        first = threading.Thread(target=run, args=("first",))
        first.start()
        self.assertTrue(client.entered.wait(timeout=10), "the first search never ran")

        second = threading.Thread(target=run, args=("second",))
        second.start()
        time.sleep(0.2)
        self.assertEqual(client.searches, [], "the first search has not answered yet")

        client.release.set()
        first.join(timeout=20)
        second.join(timeout=20)

        # One resolution, and both callers served from it. The download is not
        # single-flighted: it is the cheap half, and the second caller reaches
        # retention only once the first has retained what it fetched.
        self.assertEqual(len(client.searches), 1, "the export was paginated twice")
        for key in ("first", "second"):
            self.assertIsInstance(outcomes[key], Spectrum, outcomes[key])
            self.assertEqual(outcomes[key].source_spectrum_id, SPECTRUM_ID)

    def test_a_second_caller_sees_the_first_caller_refusal(self) -> None:
        """A refusal is shared the same way a resolution is."""
        client = GatedClient(rows=[export_row(Public="N")])
        service = build_service(
            client,
            StubWiserepRepo(),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        outcomes: Dict[str, Any] = {}

        def run(key: str) -> None:
            """Run one lookup, recording its outcome.

            Args:
                key: Which caller this is.

            Returns:
                None.
            """
            try:
                outcomes[key] = lookup(service)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                outcomes[key] = exc

        first = threading.Thread(target=run, args=("first",))
        first.start()
        self.assertTrue(client.entered.wait(timeout=10))
        second = threading.Thread(target=run, args=("second",))
        second.start()
        time.sleep(0.2)
        client.release.set()
        first.join(timeout=20)
        second.join(timeout=20)

        self.assertEqual(len(client.searches), 1)
        for key in ("first", "second"):
            self.assertIsInstance(outcomes[key], NoEligibleSpectrumException)
        self.assertEqual(outcomes["second"].message, outcomes["first"].message)


class NoRetentionStore:
    """A store that saves spectra but cannot serve a retained one."""

    def save(self, to_save: Spectrum) -> Spectrum:
        """Accept a spectrum without retaining anything readable.

        Args:
            to_save: The spectrum offered.

        Returns:
            That spectrum.
        """
        to_save.id = "not-retained"
        return to_save


@override_settings(CACHES=LOCMEM_CACHE)
class RetentionCapabilityTests(SimpleTestCase):
    """A store that cannot serve retention fails loudly, not silently.

    The retention lookup used to be a non-abstract method on the repository
    interface returning ``None``, so a repository that retains nothing
    answered "not retained" for every epoch -- which reads as a cache miss and
    re-downloads the spectrum on every single lookup.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_the_retention_lookup_is_not_on_the_wider_interface(self) -> None:
        """Only the retaining repository carries the retention lookup."""
        self.assertFalse(
            hasattr(FileSpectrumRepository, "get_by_designation_and_spectrum_id")
        )
        self.assertTrue(issubclass(DjangoSpectrumRepository, RetainedSpectrumStore))

    def test_a_store_that_cannot_retain_is_a_server_side_failure(self) -> None:
        """The misconfiguration is reported, not absorbed as a miss."""
        service = build_service(
            StubClient(),
            StubWiserepRepo(spectrum()),
            NoRetentionStore(),
            StubCandidateSets(),
            settings=stub_settings(),
        )

        with self.assertRaises(StorageException) as caught:
            lookup(service)

        self.assertEqual(caught.exception.status_code, STORAGE_FAILURE_STATUS)
        self.assertNotIsInstance(caught.exception, SpectrumValidationException)


class FailingDbRepo(StubDbRepo):
    """Retention whose writes fail the way a database under load does."""

    message = "FATAL: remaining connection slots are reserved"

    def save(self, to_save: Spectrum) -> Spectrum:
        """Fail the write.

        Args:
            to_save: The spectrum offered.

        Returns:
            Never returns.

        Raises:
            OperationalError: Always.
        """
        raise OperationalError(self.message)


@override_settings(CACHES=LOCMEM_CACHE)
class InfrastructureFailureTests(SimpleTestCase):
    """A database failure is not reported as an unusable spectrum.

    Everything outside the pass-through allowlist used to be relabelled
    ``SpectrumValidationException`` -- 422, "unusable spectrum" -- carrying the
    raw exception text. A researcher told the spectrum is unusable goes and
    looks at the spectrum, and the text names the server's internals.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_a_database_failure_answers_a_server_side_status(self) -> None:
        """Covers #11: the storage failure is the server's, and says so."""
        service = build_service(
            StubClient(),
            StubWiserepRepo(spectrum()),
            FailingDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )

        with self.assertRaises(StorageException) as caught:
            lookup(service)

        failure = caught.exception
        self.assertEqual(failure.status_code, STORAGE_FAILURE_STATUS)
        self.assertNotIsInstance(failure, SpectrumValidationException)
        self.assertNotEqual(failure.status_code, UNUSABLE_STATUS)

    def test_no_failure_puts_raw_exception_text_in_the_response(self) -> None:
        """The exception's own text is logged, not answered with."""
        service = build_service(
            StubClient(),
            StubWiserepRepo(spectrum()),
            FailingDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )

        with self.assertRaises(StorageException) as caught:
            lookup(service)
        self.assertNotIn("connection slots", caught.exception.message)

        unclassified = build_service(
            StubClient(),
            StubWiserepRepo(error=RuntimeError("torch barfed on /mnt/astrodash-data")),
            StubDbRepo(),
            StubCandidateSets(),
            settings=stub_settings(),
        )
        with self.assertRaises(SpectrumValidationException) as relabelled:
            lookup(unclassified)
        self.assertNotIn("/mnt/astrodash-data", relabelled.exception.message)
