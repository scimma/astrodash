"""Usage events are buffered per request and written after the response.

These tests drive tiny views through the Django test client, whose request
handling sends ``request_started`` and ``request_finished`` just as a server
does. A fake writer stands in for the InfluxDB client.
"""

import os
from unittest import mock

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path

from astrodash.core.usage import events
from astrodash.infrastructure.usage import USAGE_LOGGER_NAME

FULL_ENV = {
    "ASTRODASH_USAGE_INFLUX_URL": "https://influx.example.org:8086",
    "ASTRODASH_USAGE_INFLUX_DATABASE": "astrodash_usage",
    "ASTRODASH_USAGE_INFLUX_USERNAME": "writer",
    "ASTRODASH_USAGE_INFLUX_PASSWORD": "pw",
    "ASTRODASH_USAGE_ENVIRONMENT": "dev",
}
OFF_ENV = {name: "" for name in FULL_ENV}


class FakeWriter:
    """Stands in for ``InfluxClient``; records each write's lines."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.succeed = True
        FakeWriter.instances.append(self)

    def write(self, lines):
        self.writes.append(list(lines))
        return self.succeed


# Writes observed by the view before it returned, to show none happen inside it.
WRITES_SEEN_IN_VIEW = []


def _writer():
    return FakeWriter.instances[-1] if FakeWriter.instances else None


def record_n(request, n):
    for i in range(int(n)):
        events.record(
            request,
            events.EVENT_CLASSIFICATION,
            events.SURFACE_UI,
            {"model_type": "dash", "object_name": f"SN{i}"},
        )
    writer = _writer()
    WRITES_SEEN_IN_VIEW.append(len(writer.writes) if writer else 0)
    return HttpResponse("ok")


urlpatterns = [
    path("record/<int:n>/", record_n),
]


@override_settings(ROOT_URLCONF=__name__)
class BufferAndFlushTests(SimpleTestCase):
    def setUp(self):
        FakeWriter.instances = []
        WRITES_SEEN_IN_VIEW.clear()
        patches = [
            mock.patch.dict(os.environ, FULL_ENV, clear=False),
            mock.patch.object(events, "InfluxClient", FakeWriter),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        events._recorder.cache_clear()
        self.addCleanup(events._recorder.cache_clear)

    def test_one_event_is_written_once_after_the_view_returns(self):
        response = self.client.get("/record/1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WRITES_SEEN_IN_VIEW, [0])
        writer = _writer()
        self.assertEqual(len(writer.writes), 1)
        self.assertEqual(len(writer.writes[0]), 1)
        line = writer.writes[0][0]
        self.assertIn("environment=dev", line)
        self.assertIn("event=classification", line)
        self.assertIn("surface=ui", line)
        self.assertIn("identified=no", line)
        self.assertIn('object_name="SN0"', line)
        self.assertIn("count=1i", line)

    def test_three_events_are_one_write_of_three_lines(self):
        self.client.get("/record/3/")
        writes = _writer().writes
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(writes[0]), 3)

    def test_a_request_that_records_nothing_writes_nothing(self):
        self.client.get("/record/0/")
        writer = _writer()
        self.assertTrue(writer is None or writer.writes == [])

    def test_failed_write_still_returns_the_response(self):
        self.client.get("/record/1/")  # builds the writer
        _writer().succeed = False
        response = self.client.get("/record/3/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(_writer().writes[-1]), 3)

    def test_an_exception_while_flushing_is_logged_not_raised(self):
        self.client.get("/record/1/")  # builds the writer
        with mock.patch.object(_writer(), "write", side_effect=ValueError("boom")):
            with self.assertLogs(USAGE_LOGGER_NAME, level="WARNING") as logs:
                response = self.client.get("/record/3/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dropped 3 usage events", logs.output[0])
        self.assertNotIn("boom", logs.output[0])

    def test_sequential_requests_flush_only_their_own_events(self):
        self.client.get("/record/2/")
        self.client.get("/record/1/")
        self.assertEqual([len(w) for w in _writer().writes], [2, 1])

    def test_events_left_by_an_aborted_request_are_cleared_by_the_next(self):
        # Simulate a request whose flush never ran: events stranded in the
        # buffer of this thread.
        events.record(None, events.EVENT_CLASSIFICATION, events.SURFACE_UI, {})
        self.client.get("/record/1/")
        self.assertEqual([len(w) for w in _writer().writes], [1])

    def test_one_writer_is_built_per_process(self):
        self.client.get("/record/1/")
        self.client.get("/record/1/")
        self.assertEqual(len(FakeWriter.instances), 1)
        kwargs = FakeWriter.instances[0].kwargs
        self.assertEqual(kwargs["database"], "astrodash_usage")
        self.assertEqual(kwargs["pause_seconds"], 60.0)


@override_settings(ROOT_URLCONF=__name__)
class RecordingOffTests(SimpleTestCase):
    def setUp(self):
        FakeWriter.instances = []
        WRITES_SEEN_IN_VIEW.clear()
        for patcher in (
            mock.patch.dict(os.environ, OFF_ENV, clear=False),
            mock.patch.object(events, "InfluxClient", FakeWriter),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        events._recorder.cache_clear()
        self.addCleanup(events._recorder.cache_clear)

    def test_recording_off_builds_no_client_and_writes_nothing(self):
        response = self.client.get("/record/3/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeWriter.instances, [])
