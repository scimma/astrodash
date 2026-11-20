"""FIND TWINS searches and explorer selections are recorded as usage events."""

import json
import re
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from astrodash.infrastructure.explorer import daep_bundle
from astrodash.tests.gate_fixtures import classify_post
from astrodash.tests.test_spectral_twins_page import TwinsPageTestCase
from astrodash.tests.test_usage_classification_events import (
    ClassifyEventTestCase,
    by_name_spectrum,
    spectrum_file,
    upload_spectrum,
)
from astrodash.tests.usage_fixtures import recording_on

DATASET = daep_bundle.DEFAULT_TWINS_DATASET.id
TWINS_RESULT = {
    "query_umap": [0.0, 0.0],
    "query_pca": [0.0, 0.0],
    "twin_indices": [1],
    "twin_similarities": [0.9],
}


class FindTwinsEventTests(ClassifyEventTestCase):
    def search(self, find_twins=None):
        service = MagicMock(
            find_twins=find_twins or MagicMock(return_value=TWINS_RESULT)
        )
        with patch("astrodash.ui_views.get_twins_search_service", return_value=service):
            return self.client.post(reverse("astrodash:twins_search"))

    def test_search_after_an_upload_is_flagged_and_names_nothing(self):
        data = classify_post("dash")
        data.pop("supernova_name")
        with recording_on() as writer:
            self.post_classify(upload_spectrum(), data, file=spectrum_file())
            response = self.search()
        self.assertEqual(response.status_code, 200)
        tags, fields = writer.events()[-1]
        self.assertEqual(tags["event"], "twins_search")
        self.assertEqual(tags["surface"], "ui")
        self.assertIs(fields["uploaded"], True)
        self.assertNotIn("designation", fields)

    def test_search_after_a_by_name_classify_names_the_designation(self):
        with recording_on() as writer:
            self.post_classify(by_name_spectrum("SN 2011fe"), classify_post("dash"))
            self.search()
        tags, fields = writer.events()[-1]
        self.assertEqual(tags["event"], "twins_search")
        self.assertEqual(fields["designation"], "SN 2011fe")
        self.assertNotIn("uploaded", fields)

    def test_by_name_then_upload_forgets_the_designation(self):
        upload = classify_post("dash")
        upload.pop("supernova_name")
        with recording_on() as writer:
            self.post_classify(by_name_spectrum("SN 2011fe"), classify_post("dash"))
            self.post_classify(upload_spectrum(), upload, file=spectrum_file())
            self.search()
        _, fields = writer.events()[-1]
        self.assertIs(fields["uploaded"], True)
        self.assertNotIn("designation", fields)

    def test_refusal_without_an_embedding_records_nothing(self):
        with recording_on() as writer:
            self.post_classify(by_name_spectrum(), classify_post("dash"))
            session = self.client.session
            session.pop("classify_dash_embedding")
            session.save()
            response = self.search()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(writer.events()), 1)

    def test_a_failed_search_records_nothing(self):
        with recording_on() as writer:
            self.post_classify(by_name_spectrum(), classify_post("dash"))
            response = self.search(MagicMock(side_effect=RuntimeError("no artifacts")))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(writer.events()), 1)


class SelectionReportTests(TestCase):
    url = reverse("astrodash:twins_selection")

    def report(self, client=None, **data):
        payload = {"dataset": DATASET, "object": "SN 2011fe"}
        payload.update(data)
        return (client or self.client).post(self.url, data=payload)

    def test_a_valid_report_records_one_selection(self):
        with recording_on() as writer:
            response = self.report()
        self.assertEqual(response.status_code, 204)
        [(tags, fields)] = writer.events()
        self.assertEqual(tags["event"], "twins_selection")
        self.assertEqual(tags["surface"], "ui")
        self.assertEqual(fields["dataset"], DATASET)
        self.assertEqual(fields["designation"], "SN 2011fe")

    def test_every_report_is_its_own_event(self):
        with recording_on() as writer:
            for name in (
                "SN 2011fe",
                "SN 2011fe",
                "SN 2011fe",
                "SN 1998bw",
                "SN 1987A",
            ):
                self.report(object=name)
        names = [fields["designation"] for _, fields in writer.events()]
        self.assertEqual(len(names), 5)
        self.assertEqual(len(set(names)), 3)

    def test_unknown_dataset_is_refused(self):
        with recording_on() as writer:
            response = self.report(dataset="not_a_dataset")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(writer.lines, [])

    def test_overlong_or_control_character_names_are_refused(self):
        with recording_on() as writer:
            for bad in ("x" * 101, "SN\n2011fe", "SN\x002011fe", "", "   "):
                with self.subTest(name=bad):
                    self.assertEqual(self.report(object=bad).status_code, 400)
        self.assertEqual(writer.lines, [])

    def test_a_report_without_a_csrf_token_is_refused(self):
        with recording_on() as writer:
            response = self.report(client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(writer.lines, [])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class ExplorerConfigTests(TwinsPageTestCase):
    def test_config_carries_the_report_url_and_a_token(self):
        self._install_descriptor()
        response = self.client.get(self.url)
        body = response.content.decode()
        match = re.search(
            r'<script id="twins-config" type="application/json">(.*?)</script>',
            body,
            re.DOTALL,
        )
        config = json.loads(match.group(1))
        self.assertEqual(
            config["selectionReportUrl"], reverse("astrodash:twins_selection")
        )
        self.assertEqual(config["datasetId"], DATASET)
        self.assertTrue(config["csrfToken"])
        self.assertIn("csrftoken", response.cookies)
