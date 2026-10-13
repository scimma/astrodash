"""Tests for the classify page's by-name surface.

Everything here drives the real classify view through the Django test client
and a real :class:`SpectrumService`, but over stubs for the two things that
would otherwise leave the process: WISeREP's transport and the download of a
chosen spectrum. Retention and the candidate set are the real Django-backed
repositories, because the epoch control reads its choices back out of the
stored candidate set -- that read is part of what these tests prove.

Processing and classification are stubbed too. They are not what this surface
is responsible for, and running them would need model weights and the external
data mount.
"""

import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.services.spectrum_service import SpectrumService
from astrodash.forms import REDSHIFT_REQUIRED_MESSAGE
from astrodash.infrastructure.django_repositories import (
    DjangoCandidateSetRepository,
    DjangoSpectrumRepository,
)
from astrodash.infrastructure.storage.wiserep_spectrum_repository import (
    WiserepUnusableSpectrumError,
)
from astrodash.models import SOURCE_NAME_WISEREP

#: The object every single-object case resolves to, as WISeREP spells it, and
#: the internal designations the same row carries.
DESIGNATION = "SN 2026puh"
INTERNAL_NAMES = "ATLAS26hqe, GOTO26gey"

#: A second object, for the cases where one lookup must not inherit another's.
OTHER_DESIGNATION = "SN 2026zzz"

#: Every spelling of that designation a researcher may submit (AE2).
SPELLINGS = (
    "2026puh",
    "SN2026puh",
    "AT2026puh",
    "SN 2026puh",
    "ATLAS26hqe",
    "GOTO26gey",
)

REDSHIFT = "0.0086"

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "classify-by-name-tests",
    }
}


def export_row(
    spectrum_id: str,
    observation_date: str,
    julian_date: str,
    instrument: str,
    designation: str = DESIGNATION,
    internal_names: str = INTERNAL_NAMES,
    redshift: str = REDSHIFT,
    quality: str = "",
    public: str = "Y",
    spectrum_type: str = "Object",
) -> Dict[str, str]:
    """Build one row of a WISeREP spectrum export.

    Args:
        spectrum_id: WISeREP's ``Spec. ID``, which locates the spectrum.
        observation_date: The observation timestamp.
        julian_date: The observation's Julian date, the ranking key.
        instrument: The instrument name.
        designation: The object's IAU designation.
        internal_names: The object's internal designations.
        redshift: The object's recorded redshift; empty for none.
        quality: WISeREP's quality flag.
        public: Whether WISeREP marks the row publicly released.
        spectrum_type: WISeREP's spectrum type.

    Returns:
        The row, keyed by WISeREP's column names.
    """
    return {
        "IAU name": designation,
        "Internal name/s": internal_names,
        "Obj. Type": "SN Ia",
        "Redshift": redshift,
        "Spec. ID": spectrum_id,
        "Obs-date": observation_date,
        "JD": julian_date,
        "Telescope": "Gemini-S",
        "Instrument": instrument,
        "Public": public,
        "Ascii file": f"https://www.wiserep.org/system/files/{spectrum_id}.txt",
        "Spec. type": spectrum_type,
        "Spec. quality": quality,
    }


EARLIEST_ROW = export_row("5001", "2026-06-25 00:00:00", "2461186.5", "GMOS-S")
LATER_ROW = export_row("5002", "2026-07-10 00:00:00", "2461201.5", "FLOYDS")


def spectrum_for(spectrum_id: str, redshift: Optional[float] = 0.0086) -> Spectrum:
    """Build the spectrum a download of one epoch would return.

    Args:
        spectrum_id: WISeREP's identifier for the epoch.
        redshift: The redshift the source recorded, or ``None`` when it
            records none.

    Returns:
        A spectrum carrying the source identity a retained row is keyed by.
    """
    points = 200
    return Spectrum(
        x=[4000.0 + index for index in range(points)],
        y=[1.0] * points,
        redshift=redshift,
        object_designation=DESIGNATION,
        source_name=SOURCE_NAME_WISEREP,
        source_spectrum_id=spectrum_id,
        retrieved_at=timezone.now(),
        file_name=f"{spectrum_id}.txt",
    )


class StubClient:
    """A WISeREP transport answering canned rows and counting its searches."""

    def __init__(
        self,
        rows_by_designation: Optional[Dict[str, List[Dict[str, str]]]] = None,
        rows: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """Build the transport.

        Args:
            rows_by_designation: Rows to answer each searched designation
                with, keyed by the designation as submitted.
            rows: Rows to answer every search with, when the answer does not
                depend on what was searched for.
        """
        self.rows_by_designation = rows_by_designation or {}
        self.rows = rows
        self.searches: List[str] = []

    def search_spectra_rows(self, designation: str) -> List[Dict[str, str]]:
        """Answer the rows configured for a designation.

        Args:
            designation: The designation searched for, recorded.

        Returns:
            The configured rows, or an empty list when none are configured.
        """
        self.searches.append(designation)
        if self.rows is not None:
            return list(self.rows)
        return list(self.rows_by_designation.get(designation, []))


class StubWiserepRepo:
    """A spectrum download answering per epoch, and counting its downloads."""

    def __init__(
        self,
        spectra: Optional[Dict[str, Spectrum]] = None,
        unusable: Optional[str] = None,
    ) -> None:
        """Build the repository.

        Args:
            spectra: The spectrum to answer with, keyed by epoch identifier.
            unusable: An epoch identifier whose download is refused as
                unusable.
        """
        self.spectra = spectra or {}
        self.unusable = unusable
        self.downloads: List[str] = []

    def get_for_candidate(self, candidate: Any) -> Spectrum:
        """Answer the configured spectrum for one chosen epoch.

        Args:
            candidate: The chosen candidate, whose identifier is recorded.

        Returns:
            The spectrum configured for that epoch.

        Raises:
            WiserepUnusableSpectrumError: If the epoch is the one configured
                as unusable.
            AssertionError: If no spectrum is configured for the epoch.
        """
        self.downloads.append(candidate.spectrum_id)
        if self.unusable is not None and candidate.spectrum_id == self.unusable:
            raise WiserepUnusableSpectrumError(
                f"spectrum {candidate.spectrum_id} leaves too few points in the "
                f"4000-9000 Angstrom window to classify"
            )
        assert candidate.spectrum_id in self.spectra, "unexpected download"
        return self.spectra[candidate.spectrum_id]


class StubFileRepo:
    """A spectrum file reader answering one canned spectrum."""

    def __init__(self) -> None:
        """Build the reader with nothing read yet."""
        self.reads: List[str] = []

    def get_from_file(self, file: Any) -> Spectrum:
        """Answer a spectrum for an uploaded file.

        Args:
            file: The uploaded file, whose name is recorded.

        Returns:
            A spectrum carrying no source identity, as an upload does not.
        """
        self.reads.append(getattr(file, "name", ""))
        return Spectrum(
            x=[4000.0 + index for index in range(200)],
            y=[1.0] * 200,
            redshift=None,
            file_name=getattr(file, "name", "uploaded.dat"),
        )


@override_settings(CACHES=LOCMEM_CACHE)
class ClassifyByNameTestCase(TransactionTestCase):
    """Shared wiring for driving the classify page by name.

    A ``TransactionTestCase`` rather than a ``TestCase``: the seam performs its
    storage work in worker threads (``asyncio.to_thread``), and a thread opens
    its own database connection, which is outside the transaction a
    ``TestCase`` wraps each test in. Those writes therefore commit for real,
    and under ``TestCase`` they are neither rolled back nor visible
    consistently -- a retained spectrum or a candidate set from one case would
    satisfy the next one's lookup. This class truncates between tests instead.
    """

    def setUp(self) -> None:
        """Clear the candidate-set cache and select a model for the session."""
        cache.clear()
        self.url = reverse("astrodash:classify")
        self.candidate_sets = DjangoCandidateSetRepository()
        self.select_model("dash")

    def select_model(self, model_type: str) -> None:
        """Put a model selection in the session, as the picker does.

        Args:
            model_type: The model the session has selected.
        """
        session = self.client.session
        session["selected_model_type"] = model_type
        session.save()
        self.model_type = model_type

    def build_service(
        self,
        client: StubClient,
        wiserep_repo: StubWiserepRepo,
        file_repo: Optional[StubFileRepo] = None,
    ) -> SpectrumService:
        """Build the real seam over stubbed transport and download.

        Args:
            client: The stub transport resolution reads rows through.
            wiserep_repo: The stub download.
            file_repo: The stub file reader, when an upload is in play.

        Returns:
            The service the view will read spectra through.
        """
        return SpectrumService(
            file_repo=file_repo or StubFileRepo(),
            wiserep_repo=wiserep_repo,
            db_repo=DjangoSpectrumRepository(),
            candidate_set_repo=self.candidate_sets,
            wiserep_client=client,
        )

    def submit(self, service: SpectrumService, data: Dict[str, Any], **files: Any):
        """Post the classify form with classification stubbed out.

        Args:
            service: The spectrum service the view should read through.
            data: The POST payload.
            **files: Uploaded files to include in the payload.

        Returns:
            A tuple of the response and the stubbed classification service, so
            a test can read back whether a classification ran and with what.
        """
        processed = MagicMock()
        processed.x = [4000.0, 6000.0, 9000.0]
        processed.y = [1.0, 2.0, 1.0]

        classification = MagicMock()
        classification.model_type = self.model_type
        # Left empty deliberately: a non-empty match list makes the view load
        # DASH's template data from the external mount, which this surface's
        # behaviour does not depend on.
        classification.results = {"best_matches": [], "embedding": [0.0] * 1024}
        classification_svc = MagicMock(
            classify_spectrum=AsyncMock(return_value=classification)
        )
        payload = dict(data)
        payload.update(files)
        with patch(
            "astrodash.ui_views.get_spectrum_service", return_value=service
        ), patch(
            "astrodash.ui_views.get_spectrum_processing_service",
            return_value=MagicMock(
                process_spectrum_with_params=AsyncMock(return_value=processed)
            ),
        ), patch(
            "astrodash.ui_views.get_classification_service",
            return_value=classification_svc,
        ):
            response = self.client.post(self.url, data=payload)
        return response, classification_svc

    def form_payload(self, **overrides: Any) -> Dict[str, Any]:
        """Build a classify-form payload.

        Args:
            **overrides: Field values to add or replace.

        Returns:
            The POST payload.
        """
        payload = {
            "model": self.model_type,
            "supernova_name": SPELLINGS[0],
            "smoothing": 0,
            "min_wave": 3500,
            "max_wave": 10000,
        }
        payload.update(overrides)
        return payload

    def report(self, response) -> str:
        """Extract the lookup report region from a rendered page.

        Args:
            response: The response to read.

        Returns:
            The markup of the lookup report region.

        Raises:
            AssertionError: If the page carries no such region.
        """
        body = response.content.decode()
        match = re.search(r'<section id="lookup-report".*?</section>', body, re.DOTALL)
        self.assertIsNotNone(match, "the page carried no lookup report region")
        return match.group(0)


class SingleObjectLookupTests(ClassifyByNameTestCase):
    """A designation that resolves to one object with eligible spectra."""

    def test_every_accepted_spelling_resolves_to_one_object(self):
        """AE2: six spellings of one designation reach the same object."""
        for spelling in SPELLINGS:
            with self.subTest(spelling=spelling):
                client = StubClient(rows=[EARLIEST_ROW])
                service = self.build_service(
                    client, StubWiserepRepo({"5001": spectrum_for("5001")})
                )
                response, classification_svc = self.submit(
                    service, self.form_payload(supernova_name=spelling)
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(classification_svc.classify_spectrum.await_count)
                self.assertIn(DESIGNATION, self.report(response))

    def test_the_result_credits_wiserep_as_the_source(self):
        """AE16: a result from a fetched spectrum credits the source."""
        client = StubClient(rows=[EARLIEST_ROW])
        repo = StubWiserepRepo({"5001": spectrum_for("5001")})
        response, _ = self.submit(self.build_service(client, repo), self.form_payload())
        report = self.report(response)
        self.assertEqual(repo.downloads, ["5001"])
        self.assertIn("https://www.wiserep.org/", report)
        self.assertIn("WISeREP", report)

    def test_the_result_names_the_spectrum_and_how_it_was_chosen(self):
        """R7: the observation date, instrument and retrieval time are named."""
        client = StubClient(rows=[EARLIEST_ROW, LATER_ROW])
        repo = StubWiserepRepo({"5001": spectrum_for("5001")})
        response, _ = self.submit(self.build_service(client, repo), self.form_payload())
        report = self.report(response)
        self.assertIn("2026-06-25 00:00:00", report)
        self.assertIn("GMOS-S", report)
        self.assertIn("the earliest eligible observation", report)
        self.assertIn("Retrieved", report)

    def test_one_eligible_spectrum_renders_no_epoch_control(self):
        """The control is offered only where there is a choice to make."""
        client = StubClient(rows=[EARLIEST_ROW])
        response, _ = self.submit(
            self.build_service(client, StubWiserepRepo({"5001": spectrum_for("5001")})),
            self.form_payload(),
        )
        self.assertNotIn('id="epoch-form"', response.content.decode())

    def test_a_file_and_a_name_together_still_prefer_the_file(self):
        """The file wins, so nothing is resolved and no spectrum is named."""
        client = StubClient(rows=[EARLIEST_ROW])
        file_repo = StubFileRepo()
        service = self.build_service(client, StubWiserepRepo(), file_repo=file_repo)
        upload = SimpleUploadedFile(
            "spectrum.dat", b"4000 1.0\n4001 1.0\n", content_type="text/plain"
        )
        response, classification_svc = self.submit(
            service, self.form_payload(), file=upload
        )
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertEqual(client.searches, [])
        self.assertEqual(file_repo.reads, ["spectrum.dat"])
        self.assertNotIn("the earliest eligible observation", self.report(response))


class RedshiftTests(ClassifyByNameTestCase):
    """What the source's redshift does to the form and the classification."""

    def test_a_recorded_redshift_is_populated_and_labelled(self):
        """AE3: the value is used, labelled as the source's, and editable."""
        self.select_model("transformer")
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        response, classification_svc = self.submit(service, self.form_payload())
        body = response.content.decode()
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.0086)
        self.assertTrue(params["knownZ"])
        self.assertIn('id="redshift-source-note"', body)
        self.assertIn("Redshift from", body)
        redshift_input = re.search(r'<input[^>]*id="id_redshift"[^>]*>', body).group(0)
        self.assertIn('value="0.0086"', redshift_input)
        self.assertNotIn("readonly", redshift_input)
        self.assertNotIn("disabled", redshift_input)

    def test_an_edited_redshift_is_used_and_no_longer_attributed(self):
        """R10: a value the researcher typed is theirs, not the source's."""
        self.select_model("transformer")
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="0.5", known_z="on")
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.5)
        self.assertNotIn('id="redshift-source-note"', response.content.decode())

    def test_no_recorded_redshift_leaves_the_field_empty_and_refuses(self):
        """AE4: the existing requirement refuses rather than using zero."""
        self.select_model("transformer")
        client = StubClient(
            rows=[
                export_row(
                    "5001", "2026-06-25 00:00:00", "2461186.5", "GMOS-S", redshift=""
                )
            ]
        )
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001", redshift=None)})
        )
        response, classification_svc = self.submit(service, self.form_payload())
        body = response.content.decode()
        self.assertEqual(classification_svc.classify_spectrum.await_count, 0)
        self.assertIn(REDSHIFT_REQUIRED_MESSAGE, body)
        redshift_input = re.search(r'<input[^>]*id="id_redshift"[^>]*>', body).group(0)
        self.assertNotIn("value=", redshift_input)
        known_z_input = re.search(r'<input[^>]*id="id_known_z"[^>]*>', body).group(0)
        self.assertNotIn("checked", known_z_input)

    def test_the_refusal_is_reported_where_focus_lands(self):
        """The region focus moves to carries the refusal, not nothing."""
        self.select_model("transformer")
        client = StubClient(
            rows=[
                export_row(
                    "5001", "2026-06-25 00:00:00", "2461186.5", "GMOS-S", redshift=""
                )
            ]
        )
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001", redshift=None)})
        )
        response, _ = self.submit(service, self.form_payload())
        self.assertIn(REDSHIFT_REQUIRED_MESSAGE, self.report(response))

    def test_a_second_designation_takes_its_own_recorded_redshift(self):
        """The field still holding the first object's value is not an edit."""
        self.select_model("transformer")
        first_service = self.build_service(
            StubClient(rows=[EARLIEST_ROW]),
            StubWiserepRepo({"5001": spectrum_for("5001")}),
        )
        self.submit(first_service, self.form_payload())

        second_row = export_row(
            "6001",
            "2026-08-01 00:00:00",
            "2461223.5",
            "GMOS-S",
            designation=OTHER_DESIGNATION,
            internal_names="ATLAS26zzz",
            redshift="0.25",
        )
        second_service = self.build_service(
            StubClient(rows=[second_row]),
            StubWiserepRepo({"6001": spectrum_for("6001", redshift=0.25)}),
        )
        response, classification_svc = self.submit(
            second_service,
            self.form_payload(
                supernova_name=OTHER_DESIGNATION,
                redshift="0.0086",
                known_z="on",
            ),
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.25)
        self.assertTrue(params["knownZ"])
        self.assertIn('id="redshift-source-note"', response.content.decode())

    def test_another_epoch_takes_its_own_recorded_redshift(self):
        """The value the previous epoch filled in is not an edit either."""
        self.select_model("transformer")
        service = self.build_service(
            StubClient(rows=[EARLIEST_ROW, LATER_ROW]),
            StubWiserepRepo(
                {
                    "5001": spectrum_for("5001"),
                    "5002": spectrum_for("5002", redshift=0.035),
                }
            ),
        )
        self.submit(service, self.form_payload())
        response, classification_svc = self.submit(
            service,
            self.form_payload(
                source_spectrum_id="5002",
                redshift="0.0086",
                known_z="on",
            ),
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.035)
        self.assertTrue(params["knownZ"])
        self.assertIn('id="redshift-source-note"', response.content.decode())

    def test_an_empty_box_on_a_first_lookup_is_still_filled_in(self):
        """R9: a name alone classifies, and the page submits an empty box."""
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="")
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.0086)
        self.assertTrue(params["knownZ"])
        self.assertIn('id="redshift-source-note"', response.content.decode())

    def test_a_cleared_redshift_classifies_without_one(self):
        """R10: removing the prefilled value is honoured, not written back."""
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        self.submit(service, self.form_payload())
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="")
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertIsNone(params["zValue"])
        self.assertFalse(params["knownZ"])
        body = response.content.decode()
        self.assertNotIn('id="redshift-source-note"', body)
        redshift_input = re.search(r'<input[^>]*id="id_redshift"[^>]*>', body).group(0)
        self.assertNotIn("value=", redshift_input)
        known_z_input = re.search(r'<input[^>]*id="id_known_z"[^>]*>', body).group(0)
        self.assertNotIn("checked", known_z_input)

    def test_unchecking_known_redshift_classifies_without_one(self):
        """R10: the checkbox is an opt-out even with the value still in view."""
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        self.submit(service, self.form_payload())
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="0.0086")
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertIsNone(params["zValue"])
        self.assertFalse(params["knownZ"])
        self.assertNotIn('id="redshift-source-note"', response.content.decode())

    def test_the_prefilled_value_left_alone_is_still_the_source_s(self):
        """R9: an untouched prefill is used and stays labelled as the source's."""
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        self.submit(service, self.form_payload())
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="0.0086", known_z="on")
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertEqual(params["zValue"], 0.0086)
        self.assertTrue(params["knownZ"])
        self.assertIn('id="redshift-source-note"', response.content.decode())

    def test_the_opt_out_holds_on_the_next_submission(self):
        """The value does not spring back once it has been declined."""
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        self.submit(service, self.form_payload())
        self.submit(service, self.form_payload(redshift=""))
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="", smoothing=3)
        )
        params = classification_svc.classify_spectrum.await_args.kwargs["params"]
        self.assertIsNone(params["zValue"])
        self.assertFalse(params["knownZ"])
        self.assertNotIn('id="redshift-source-note"', response.content.decode())

    def test_a_required_redshift_model_refuses_rather_than_overriding(self):
        """An opt-out under a model that needs one refuses, as an upload does."""
        self.select_model("transformer")
        client = StubClient(rows=[EARLIEST_ROW])
        service = self.build_service(
            client, StubWiserepRepo({"5001": spectrum_for("5001")})
        )
        self.submit(service, self.form_payload())
        response, classification_svc = self.submit(
            service, self.form_payload(redshift="")
        )
        self.assertEqual(classification_svc.classify_spectrum.await_count, 0)
        self.assertIn(REDSHIFT_REQUIRED_MESSAGE, self.report(response))


class EpochControlTests(ClassifyByNameTestCase):
    """Switching to another eligible spectrum of the same object."""

    def setUp(self) -> None:
        """Resolve a two-epoch object and classify its earliest spectrum."""
        super().setUp()
        self.client_stub = StubClient(rows=[EARLIEST_ROW, LATER_ROW])
        self.wiserep_repo = StubWiserepRepo(
            {"5001": spectrum_for("5001"), "5002": spectrum_for("5002")}
        )
        self.service = self.build_service(self.client_stub, self.wiserep_repo)
        self.first_response, _ = self.submit(self.service, self.form_payload())

    def test_the_control_offers_every_eligible_epoch_in_date_order(self):
        """R8: one option per eligible spectrum, the current one preselected."""
        report = self.report(self.first_response)
        self.assertIn('for="id_source_spectrum_id"', report)
        options = re.findall(
            r'<option value="(\d+)"( selected)?>([^<]*)</option>', report
        )
        self.assertEqual([value for value, _, _ in options], ["5001", "5002"])
        self.assertEqual([bool(flag) for _, flag, _ in options], [True, False])
        self.assertIn(
            "2026-06-25 00:00:00 - GMOS-S - quality not stated", options[0][2]
        )
        self.assertIn(
            "2026-07-10 00:00:00 - FLOYDS - quality not stated", options[1][2]
        )

    def test_choosing_another_epoch_reclassifies_without_re_entry(self):
        """AE8: the switch names the designation for the researcher."""
        searches_before = list(self.client_stub.searches)
        response, classification_svc = self.submit(
            self.service, self.form_payload(source_spectrum_id="5002")
        )
        report = self.report(response)
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertEqual(self.client_stub.searches, searches_before)
        self.assertEqual(self.wiserep_repo.downloads, ["5001", "5002"])
        self.assertIn("2026-07-10 00:00:00", report)
        self.assertIn("FLOYDS", report)
        self.assertIn("the epoch you chose", report)

    def test_an_expired_cache_falls_through_to_the_stored_set(self):
        """A choice stays valid once the cache entry has aged out."""
        searches_before = list(self.client_stub.searches)
        cache.clear()
        response, classification_svc = self.submit(
            self.service, self.form_payload(source_spectrum_id="5002")
        )
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertEqual(self.client_stub.searches, searches_before)
        self.assertIn("2026-07-10 00:00:00", self.report(response))

    def test_a_choice_absent_from_the_derived_set_is_rejected(self):
        """A set was derived, so a choice outside it classifies nothing."""
        response, classification_svc = self.submit(
            self.service, self.form_payload(source_spectrum_id="9999")
        )
        self.assertEqual(classification_svc.classify_spectrum.await_count, 0)
        self.assertIn("Select a valid choice", response.content.decode())


class AmbiguityTests(ClassifyByNameTestCase):
    """A designation that matches more than one object."""

    OTHER_DESIGNATION = "SN 2026pui"

    def rows_for_two_objects(self) -> List[Dict[str, str]]:
        """Build an export describing two distinct objects.

        Returns:
            Rows for two objects, neither matching the submitted substring.
        """
        return [
            export_row("5001", "2026-06-25 00:00:00", "2461186.5", "GMOS-S"),
            export_row(
                "6001",
                "2026-06-26 00:00:00",
                "2461187.5",
                "FLOYDS",
                designation=self.OTHER_DESIGNATION,
                internal_names="ATLAS26hqf",
            ),
        ]

    def test_an_ambiguous_designation_offers_the_objects_and_classifies_nothing(self):
        """AE10, R19: the matching objects are reported for the researcher."""
        client = StubClient(rows=self.rows_for_two_objects())
        service = self.build_service(client, StubWiserepRepo())
        response, classification_svc = self.submit(
            service, self.form_payload(supernova_name="2026pu")
        )
        report = self.report(response)
        self.assertEqual(classification_svc.classify_spectrum.await_count, 0)
        self.assertIn("Matching objects", report)
        for designation in (DESIGNATION, self.OTHER_DESIGNATION):
            self.assertIn(
                f'<input type="hidden" name="supernova_name" value="{designation}">',
                report,
            )
            self.assertIn(f"Classify {designation}</button>", report)
        # A bare list of names asks the researcher to choose between things they
        # cannot tell apart, so each entry carries the type and redshift the
        # export reported for that object (U7 step 5).
        self.assertIn("SN Ia", report)  # the type export_row reports
        self.assertIn(f"z = {REDSHIFT}", report)

    def test_choosing_one_object_classifies_its_earliest_eligible_spectrum(self):
        """AE18: the choice resubmits that exact designation and classifies it.

        The choice resolves the designation it names rather than the submitted
        substring: the ambiguity refusal stores no candidate set, because
        ranking produced none, so the exact designation is resolved once here.
        """
        client = StubClient(
            rows_by_designation={
                "2026pu": self.rows_for_two_objects(),
                DESIGNATION: [EARLIEST_ROW, LATER_ROW],
            }
        )
        repo = StubWiserepRepo({"5001": spectrum_for("5001")})
        service = self.build_service(client, repo)
        self.submit(service, self.form_payload(supernova_name="2026pu"))
        response, classification_svc = self.submit(
            service, self.form_payload(supernova_name=DESIGNATION)
        )
        report = self.report(response)
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertEqual(repo.downloads, ["5001"])
        self.assertIn("2026-06-25 00:00:00", report)
        self.assertIn("the earliest eligible observation", report)
        self.assertNotIn("Matching objects", report)


class UnusableSpectrumTests(ClassifyByNameTestCase):
    """The earliest eligible spectrum cannot be classified as fetched."""

    def test_the_refusal_renders_with_the_epoch_control(self):
        """AE20: the other epochs are offered alongside the refusal."""
        client = StubClient(rows=[EARLIEST_ROW, LATER_ROW])
        repo = StubWiserepRepo({"5002": spectrum_for("5002")}, unusable="5001")
        service = self.build_service(client, repo)
        response, classification_svc = self.submit(service, self.form_payload())
        report = self.report(response)
        self.assertEqual(classification_svc.classify_spectrum.await_count, 0)
        self.assertIn("4000-9000 Angstrom window", report)
        self.assertIn('id="epoch-form"', report)

        response, classification_svc = self.submit(
            service, self.form_payload(source_spectrum_id="5002")
        )
        report = self.report(response)
        self.assertTrue(classification_svc.classify_spectrum.await_count)
        self.assertIn("2026-07-10 00:00:00", report)
        self.assertIn("the epoch you chose", report)
        self.assertEqual(client.searches, [SPELLINGS[0]])


class AccessibilityTests(ClassifyByNameTestCase):
    """Labels, the announced regions, and the in-flight state."""

    def setUp(self) -> None:
        """Classify a two-epoch object so every new control is rendered."""
        super().setUp()
        client = StubClient(rows=[EARLIEST_ROW, LATER_ROW])
        self.response, _ = self.submit(
            self.build_service(
                client,
                StubWiserepRepo({"5001": spectrum_for("5001")}),
            ),
            self.form_payload(),
        )
        self.body = self.response.content.decode()

    def test_the_epoch_control_carries_an_associated_label(self):
        """The select is reachable and named for a screen reader."""
        self.assertIn(
            '<label class="mr-2 mb-0" for="id_source_spectrum_id">', self.body
        )
        self.assertIn('id="id_source_spectrum_id"', self.body)

    def test_the_report_region_is_announced_and_takes_focus(self):
        """The outcome is announced, and focus lands on it after a submit."""
        region = re.search(r'<section id="lookup-report"[^>]*>', self.body).group(0)
        self.assertIn('role="region"', region)
        self.assertIn('aria-live="polite"', region)
        self.assertIn('tabindex="-1"', region)
        self.assertIn('getElementById("lookup-report")', self.body)
        self.assertIn("report.focus()", self.body)

    def test_a_lookup_in_flight_disables_the_submit_and_announces_itself(self):
        """The page carries the politely announced in-flight status."""
        status = re.search(r'<div id="lookup-status"[^>]*>', self.body).group(0)
        self.assertIn('role="status"', status)
        self.assertIn('aria-live="polite"', status)
        self.assertIn('button.setAttribute("aria-disabled", "true");', self.body)
        self.assertIn('"Looking up " + designation +', self.body)
        epoch_form = re.search(r'<form[^>]*id="epoch-form"[^>]*>', self.body).group(0)
        self.assertIn("by-name-aware", epoch_form)

    def test_a_lookup_in_flight_shows_a_moving_indicator_where_it_is_seen(self):
        """The pending state is visible, animated and placed at the click.

        The report region sits in a different column from the sidebar form, so
        words written only there can go unread while the researcher is looking
        at the button they just pressed. The spinner therefore goes on the
        pressed button, and the elapsed seconds are what distinguish a live
        request from a stalled one.
        """
        # The spinner is the project's own, because the pinned Bootstrap
        # 4.1.3 ships no spinner utility to lean on.
        self.assertIn(".lookup-spinner {", self.body)
        self.assertIn("@keyframes lookup-spin", self.body)
        self.assertIn("animation: lookup-spin", self.body)
        self.assertNotIn("spinner-border", self.body)

        # It lands on the button that was actually pressed.
        self.assertIn("event.submitter", self.body)
        self.assertIn(
            '<span class="lookup-spinner mr-2"></span>Looking up...', self.body
        )
        self.assertIn("data-idle-label", self.body)

        # And the visible panel carries the words plus a ticking counter.
        panel = re.search(r'<div id="lookup-pending"[^>]*>', self.body).group(0)
        self.assertIn("alert", panel)
        self.assertIn("hidden", panel)
        self.assertIn('id="lookup-elapsed"', self.body)
        self.assertIn("window.setInterval(", self.body)

    def test_the_moving_indicator_does_not_talk_over_the_announcement(self):
        """Motion and the counter stay out of the live regions.

        ``#lookup-status`` announces once. The visual panel repeats those words
        for sighted users, so it is aria-hidden: the surrounding region is
        itself ``aria-live``, and a counter inside it would otherwise be read
        out every second.
        """
        panel = re.search(r'<div id="lookup-pending"[^>]*>', self.body).group(0)
        self.assertIn('aria-hidden="true"', panel)

        # The announcement stays screen-reader-only rather than being toggled
        # visible, because the panel is now what sighted users read.
        self.assertNotIn('status.classList.toggle("sr-only"', self.body)

        # A researcher who has asked for less motion still gets the counter.
        self.assertIn("prefers-reduced-motion: reduce", self.body)
        self.assertIn("animation: none;", self.body)

        # The counter is torn down on every release path, so a restored
        # document does not resume ticking.
        self.assertIn("stopElapsed();", self.body)
        self.assertIn("window.clearInterval(", self.body)

    def test_the_in_flight_guard_covers_every_submit_path_and_lifts_itself(self):
        """One flag guards all by-name forms, and a restore clears it."""
        # The guard is page-wide, not per-form: a second submit from any
        # by-name form is cancelled while one is already waiting on a worker.
        self.assertIn(
            "document.querySelectorAll('form.by-name-aware button[type=\"submit\"]')",
            self.body,
        )
        self.assertIn("if (inFlight) {", self.body)
        self.assertIn("event.preventDefault();", self.body)
        # The marking is lifted again, so an aborted navigation or a
        # back/forward restore does not leave the page unusable. The buttons
        # are aria-disabled rather than disabled, so they keep keyboard focus.
        self.assertIn('window.addEventListener("pageshow"', self.body)
        self.assertIn("event.persisted", self.body)
        self.assertNotIn("button.disabled = true;", self.body)
