"""Every successful classification records one usage event per spectrum.

The classify page, the batch page and both REST routes are driven through the
test client with their services stubbed, and the events are read back from a
fake writer.
"""

import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from astrodash.core import model_access
from astrodash.domain.models.spectrum import Spectrum
from astrodash.infrastructure.usage import USAGE_LOGGER_NAME
from astrodash.tests.gate_fixtures import classify_post, gated_gate, redeem_scope
from astrodash.tests.usage_fixtures import recording_on

MARKER = "FILE-CONTENT-MARKER-3b9e"


def spectrum_file(name: str = "sn_upload.dat") -> SimpleUploadedFile:
    body = f"# {MARKER}\n" + "\n".join(f"{4000 + i} 1.0" for i in range(200))
    return SimpleUploadedFile(name, body.encode("ascii"), content_type="text/plain")


def classify_services(spectrum: Spectrum, model_type: str = "dash", fail=False):
    """Patch the classify view's services so it runs without models or WISeREP."""
    processed = MagicMock()
    processed.x = [4000.0, 6000.0, 9000.0]
    processed.y = [1.0, 2.0, 1.0]
    classification = MagicMock()
    classification.model_type = model_type
    classification.results = {"best_matches": [], "embedding": [0.0] * 1024}
    classify = AsyncMock(
        side_effect=RuntimeError("model failed") if fail else None,
        return_value=classification,
    )
    return [
        patch(
            "astrodash.ui_views.get_spectrum_service",
            return_value=MagicMock(get_spectrum_data=AsyncMock(return_value=spectrum)),
        ),
        patch(
            "astrodash.ui_views.get_spectrum_processing_service",
            return_value=MagicMock(
                process_spectrum_with_params=AsyncMock(return_value=processed)
            ),
        ),
        patch(
            "astrodash.ui_views.get_classification_service",
            return_value=MagicMock(classify_spectrum=classify),
        ),
    ]


def by_name_spectrum(designation: str = "SN 2011fe") -> Spectrum:
    return Spectrum(
        x=[4000.0 + i for i in range(200)],
        y=[1.0] * 200,
        redshift=None,
        object_designation=designation,
        source_spectrum_id="12345",
    )


def upload_spectrum() -> Spectrum:
    return Spectrum(
        x=[4000.0 + i for i in range(200)], y=[1.0] * 200, file_name="sn_upload.dat"
    )


class ClassifyEventTestCase(TestCase):
    def setUp(self):
        self.select_model("dash")

    def select_model(self, model_type, model_id=None):
        session = self.client.session
        session["selected_model_type"] = model_type
        if model_id:
            session["selected_model_id"] = model_id
        session.save()

    def post_classify(self, spectrum, data, model_type="dash", fail=False, **files):
        patches = classify_services(spectrum, model_type, fail)
        for patcher in patches:
            patcher.start()
        try:
            payload = dict(data)
            payload.update(files)
            return self.client.post(reverse("astrodash:classify"), data=payload)
        finally:
            for patcher in patches:
                patcher.stop()


class UiClassifyEventTests(ClassifyEventTestCase):
    def test_by_name_classify_records_one_event_with_the_submission(self):
        with recording_on() as writer:
            response = self.post_classify(
                by_name_spectrum(),
                dict(classify_post("dash"), redshift="0.0008", known_z="on"),
            )
        self.assertEqual(response.status_code, 200)
        [(tags, fields)] = writer.events()
        self.assertEqual(tags["event"], "classification")
        self.assertEqual(tags["surface"], "ui")
        self.assertEqual(tags["identified"], "no")
        self.assertEqual(fields["source"], "by_name")
        self.assertEqual(fields["object_name"], "SN2011fe")
        self.assertEqual(fields["designation"], "SN 2011fe")
        self.assertEqual(fields["model_type"], "dash")
        self.assertEqual(fields["model_id"], "dash")
        self.assertEqual(fields["smoothing"], 0.0)
        self.assertEqual(fields["min_wave"], 3500.0)
        self.assertEqual(fields["max_wave"], 10000.0)
        self.assertEqual(fields["redshift"], 0.0008)
        self.assertIs(fields["known_z"], True)
        self.assertIs(fields["batch"], False)
        self.assertNotIn("filename", fields)
        self.assertNotIn("visitor", tags)

    def test_submitted_spelling_and_resolved_designation_both_recorded(self):
        with recording_on() as writer:
            self.post_classify(
                by_name_spectrum("SN 2011fe"),
                dict(classify_post("dash"), supernova_name="sn2011fe"),
            )
        [(_, fields)] = writer.events()
        self.assertEqual(fields["object_name"], "sn2011fe")
        self.assertEqual(fields["designation"], "SN 2011fe")

    def test_upload_classify_records_the_filename_not_the_contents(self):
        with recording_on() as writer:
            data = classify_post("dash")
            data.pop("supernova_name")
            self.post_classify(upload_spectrum(), data, file=spectrum_file())
        [(_, fields)] = writer.events()
        self.assertEqual(fields["source"], "upload")
        self.assertEqual(fields["filename"], "sn_upload.dat")
        self.assertNotIn("designation", fields)
        for line in writer.lines:
            self.assertNotIn(MARKER, line)

    def test_name_submitted_with_a_file_is_an_upload_that_keeps_the_name(self):
        with recording_on() as writer:
            self.post_classify(
                upload_spectrum(), classify_post("dash"), file=spectrum_file()
            )
        [(_, fields)] = writer.events()
        self.assertEqual(fields["source"], "upload")
        self.assertEqual(fields["object_name"], "SN2011fe")

    def test_a_classification_that_raises_records_nothing(self):
        with recording_on() as writer:
            self.post_classify(by_name_spectrum(), classify_post("dash"), fail=True)
        self.assertEqual(writer.lines, [])

    def test_get_rerender_records_nothing(self):
        with recording_on() as writer:
            self.post_classify(by_name_spectrum(), classify_post("dash"))
            self.assertEqual(len(writer.lines), 1)
            self.client.get(reverse("astrodash:classify"))
            self.client.get(reverse("astrodash:classify"), {"overlay_elements": "H"})
        self.assertEqual(len(writer.lines), 1)

    def test_failed_write_still_renders_the_result_and_warns(self):
        with recording_on() as writer:
            writer.succeed = False
            with self.assertLogs(USAGE_LOGGER_NAME, level="WARNING"):
                # The fake does not log, so make the real client's warning
                # appear by failing inside the flush instead.
                with patch.object(writer, "write", side_effect=OSError("down")):
                    response = self.post_classify(
                        by_name_spectrum(), classify_post("dash")
                    )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["success"])

    def test_gated_model_scope_records_the_model_id(self):
        redeem_scope(self.client, self)
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), recording_on() as writer:
            self.post_classify(by_name_spectrum(), classify_post("dash"))
        [(_, fields)] = writer.events()
        self.assertEqual(fields["model_id"], "dash")
        self.assertEqual(fields["model_type"], "dash")

    def test_user_uploaded_model_records_its_type_and_id(self):
        self.select_model("user_uploaded", "um-42")
        with recording_on() as writer:
            self.post_classify(
                by_name_spectrum(),
                classify_post("user_uploaded"),
                model_type="user_uploaded",
            )
        [(_, fields)] = writer.events()
        self.assertEqual(fields["model_type"], "user_uploaded")
        self.assertEqual(fields["model_id"], "um-42")


def batch_results(n_ok, n_failed):
    results = {}
    for i in range(n_ok):
        results[f"ok_{i}.dat"] = {
            "classification": {"best_match": {"type": "Ia", "probability": 0.9}},
            "model_type": "dash",
        }
    for i in range(n_failed):
        results[f"bad_{i}.dat"] = {"error": "could not read"}
    return results


class UiBatchEventTests(ClassifyEventTestCase):
    def post_batch(self, results, **data):
        payload = {
            "smoothing": 0,
            "min_wave": 3500,
            "max_wave": 10000,
            "files": [
                SimpleUploadedFile(name, b"4000 1.0\n4001 1.0\n") for name in results
            ],
        }
        payload.update(data)
        service = MagicMock(process_batch=AsyncMock(return_value=results))
        with patch(
            "astrodash.ui_views.get_batch_processing_service", return_value=service
        ):
            return self.client.post(reverse("astrodash:batch_process_ui"), data=payload)

    def test_batch_of_ten_with_two_failures_records_eight_batch_events(self):
        with recording_on() as writer:
            response = self.post_batch(batch_results(8, 2))
        self.assertEqual(response.status_code, 200)
        events = writer.events()
        self.assertEqual(len(events), 8)
        self.assertEqual(
            sorted(fields["filename"] for _, fields in events),
            sorted(f"ok_{i}.dat" for i in range(8)),
        )
        for tags, fields in events:
            self.assertEqual(tags["surface"], "ui")
            self.assertIs(fields["batch"], True)
            self.assertEqual(fields["source"], "upload")
            self.assertEqual(fields["model_id"], "dash")

    def test_batch_redshift_text_never_shares_the_numeric_redshift_field(self):
        with recording_on() as writer:
            self.post_classify(
                by_name_spectrum(), dict(classify_post("dash"), redshift="0.02")
            )
            self.post_batch(batch_results(1, 0), redshift="ok_0.dat=0.01")
        types = {}
        for _, fields in writer.events():
            for name, value in fields.items():
                types.setdefault(name, set()).add(type(value))
        for name, seen in types.items():
            self.assertEqual(len(seen), 1, f"{name} was written as {seen}")
        batch_fields = writer.events()[-1][1]
        self.assertEqual(batch_fields["redshift_text"], "ok_0.dat=0.01")
        self.assertNotIn("redshift", batch_fields)


def rest_services(spectrum):
    processed = MagicMock()
    processed.x = [4000.0, 6000.0]
    processed.y = [1.0, 1.0]
    processed.redshift = 0.0
    classification = MagicMock()
    classification.model_type = "dash"
    classification.results = {"best_matches": []}
    return [
        patch("astrodash.views.API_WRITES_ENABLED", True),
        patch(
            "astrodash.views.get_spectrum_service",
            return_value=MagicMock(
                get_spectrum_data=AsyncMock(return_value=spectrum),
                save_spectrum=AsyncMock(return_value=None),
            ),
        ),
        patch(
            "astrodash.views.get_spectrum_processing_service",
            return_value=MagicMock(
                process_spectrum_with_params=AsyncMock(return_value=processed)
            ),
        ),
        patch(
            "astrodash.views.get_classification_service",
            return_value=MagicMock(
                classify_spectrum=AsyncMock(return_value=classification)
            ),
        ),
    ]


class RestEventTests(TestCase):
    def post(self, url_name, spectrum=None, **data):
        patches = rest_services(spectrum or by_name_spectrum())
        for patcher in patches:
            patcher.start()
        try:
            return self.client.post(reverse(url_name), data=data)
        finally:
            for patcher in patches:
                patcher.stop()

    def test_rest_process_records_one_anonymous_api_event(self):
        params = {"modelType": "dash", "objectName": "SN2011fe", "smoothing": 2}
        with recording_on() as writer:
            response = self.post(
                "astrodash_api:process_spectrum", params=json.dumps(params)
            )
        self.assertEqual(response.status_code, 200)
        [(tags, fields)] = writer.events()
        self.assertEqual(tags["surface"], "api")
        self.assertEqual(tags["identified"], "no")
        self.assertNotIn("visitor", tags)
        self.assertEqual(fields["source"], "by_name")
        self.assertEqual(fields["object_name"], "SN2011fe")
        self.assertEqual(fields["designation"], "SN 2011fe")
        self.assertEqual(fields["smoothing"], 2.0)
        self.assertEqual(json.loads(fields["params_json"]), params)
        self.assertIs(fields["batch"], False)

    def test_rest_number_sent_as_string_keeps_its_declared_type(self):
        params = {"modelType": "dash", "objectName": "SN2011fe", "zValue": "0.05"}
        with recording_on() as writer:
            self.post("astrodash_api:process_spectrum", params=json.dumps(params))
        [(_, fields)] = writer.events()
        self.assertEqual(fields["redshift"], 0.05)

    def test_rest_upload_records_the_filename(self):
        with recording_on() as writer:
            self.post(
                "astrodash_api:process_spectrum",
                spectrum=upload_spectrum(),
                params=json.dumps({"modelType": "dash"}),
                file=spectrum_file("api.dat"),
            )
        [(_, fields)] = writer.events()
        self.assertEqual(fields["source"], "upload")
        self.assertEqual(fields["filename"], "api.dat")

    def test_rest_batch_records_one_api_event_per_success(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("a.dat", "4000 1.0\n")
        upload = SimpleUploadedFile("batch.zip", archive.getvalue())
        service = MagicMock(process_batch=AsyncMock(return_value=batch_results(3, 1)))
        with recording_on() as writer, patch(
            "astrodash.views.API_WRITES_ENABLED", True
        ), patch("astrodash.views.get_batch_processing_service", return_value=service):
            response = self.client.post(
                reverse("astrodash_api:batch_process"),
                data={"params": json.dumps({"modelType": "dash"}), "zip_file": upload},
            )
        self.assertEqual(response.status_code, 200)
        events = writer.events()
        self.assertEqual(len(events), 3)
        for tags, fields in events:
            self.assertEqual(tags["surface"], "api")
            self.assertIs(fields["batch"], True)
            self.assertTrue(fields["filename"].startswith("ok_"))
