"""The InfluxDB writer never raises into its caller and backs off after failure.

A fake session stands in for ``requests`` and a fake clock for time, so these
tests make no network call.
"""

import requests
from django.test import SimpleTestCase

from astrodash.infrastructure.usage import USAGE_LOGGER_NAME
from astrodash.infrastructure.usage.influx_client import InfluxClient

PASSWORD = "pw-never-logged-5e1d"
LINES = [
    "usage,event=a count=1i 1",
    "usage,event=b count=1i 2",
    "usage,event=c count=1i 3",
]


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Records each POST and answers from a queue of responses or exceptions."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_client(session, clock=None, **overrides):
    options = dict(
        url="https://influx.example.org:8086",
        database="astrodash_usage",
        username="writer",
        password=PASSWORD,
        retention_policy=None,
        cert_pair=None,
        connect_timeout=2.0,
        read_timeout=3.0,
        pause_seconds=60.0,
    )
    options.update(overrides)
    return InfluxClient(session=session, clock=clock or FakeClock(), **options)


class WriteTests(SimpleTestCase):
    def test_204_is_success_with_one_post_carrying_every_line(self):
        session = FakeSession(FakeResponse(204))
        self.assertTrue(make_client(session).write(LINES))
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://influx.example.org:8086/write")
        self.assertEqual(kwargs["data"].decode("utf-8").split("\n"), LINES)
        self.assertEqual(kwargs["params"]["db"], "astrodash_usage")
        self.assertEqual(kwargs["params"]["precision"], "ns")
        self.assertNotIn("rp", kwargs["params"])
        self.assertEqual(kwargs["auth"], ("writer", PASSWORD))
        self.assertEqual(kwargs["timeout"], (2.0, 3.0))
        self.assertIsNone(kwargs.get("cert"))

    def test_retention_policy_is_sent_when_configured(self):
        session = FakeSession(FakeResponse(204))
        make_client(session, retention_policy="twenty_five_months").write(LINES)
        self.assertEqual(session.calls[0][1]["params"]["rp"], "twenty_five_months")

    def test_certificate_pair_is_passed_when_configured(self):
        session = FakeSession(FakeResponse(204))
        make_client(session, cert_pair=("/c.crt", "/c.key")).write(LINES)
        self.assertEqual(session.calls[0][1]["cert"], ("/c.crt", "/c.key"))

    def test_no_lines_makes_no_call(self):
        session = FakeSession()
        self.assertTrue(make_client(session).write([]))
        self.assertEqual(session.calls, [])


class FailureTests(SimpleTestCase):
    def _assert_pausing_failure(self, outcome):
        clock = FakeClock()
        session = FakeSession(outcome)
        client = make_client(session, clock=clock)
        with self.assertLogs(USAGE_LOGGER_NAME, level="WARNING") as logs:
            self.assertFalse(client.write(LINES))
            # Paused: the next write makes no network call.
            self.assertFalse(client.write(LINES))
        self.assertEqual(len(session.calls), 1)
        self.assertIn("3 usage events", logs.output[0])
        self.assertIn("paused", logs.output[1])
        for line in logs.output:
            self.assertNotIn(PASSWORD, line)
        return clock, session, client

    def test_401_fails_and_pauses(self):
        self._assert_pausing_failure(FakeResponse(401, f"bad password {PASSWORD}"))

    def test_500_fails_and_pauses(self):
        self._assert_pausing_failure(FakeResponse(500))

    def test_connection_error_fails_without_raising(self):
        self._assert_pausing_failure(requests.ConnectionError(f"refused {PASSWORD}"))

    def test_timeout_fails_without_raising(self):
        self._assert_pausing_failure(requests.Timeout("read timed out"))

    def test_write_resumes_after_the_pause_elapses(self):
        clock, session, client = self._assert_pausing_failure(FakeResponse(503))
        session.outcomes.append(FakeResponse(204))
        clock.now += 60.0
        self.assertTrue(client.write(LINES))
        self.assertEqual(len(session.calls), 2)

    def test_400_fails_logs_the_count_and_does_not_pause(self):
        session = FakeSession(FakeResponse(400, "partial write"), FakeResponse(204))
        client = make_client(session)
        with self.assertLogs(USAGE_LOGGER_NAME, level="WARNING") as logs:
            self.assertFalse(client.write(LINES))
        self.assertIn("rejected 3 usage events", logs.output[0])
        self.assertTrue(client.write(LINES))
        self.assertEqual(len(session.calls), 2)
