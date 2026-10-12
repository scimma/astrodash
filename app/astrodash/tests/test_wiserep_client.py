"""Tests for the bounded WISeREP transport.

Every case here runs against zip exports captured from the live service and
stored under ``fixtures/wiserep/``; nothing reaches the network. The session is
a stub that hands back queued responses and records what it was asked for, and
the clock is a stub so a deadline can be crossed without waiting for it.

The captured pages are:

* ``2026puh_page0.zip`` -- one row.
* ``2002er_page0.zip`` -- 35 rows, under the export's 50-row cap.
* ``2023ixf_page0.zip`` and ``2023ixf_page1.zip`` -- 50 rows each, two
  consecutive pages of the same search, so a full page and its successor are
  both real.
* ``name_2002_multi_object_page0.zip`` -- 50 rows spanning ten objects.

One case needs a page that repeats some rows while carrying others that are
new. No captured response has that shape, so it is assembled from the rows of
the two real ``2023ixf`` pages rather than from invented rows.
"""

import io
import zipfile
from pathlib import Path
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from astrodash.infrastructure.wiserep import client as client_module
from astrodash.infrastructure.wiserep.client import (
    EXPORT_PAGE_ROW_CAP,
    SEARCH_PATH,
    WiserepClient,
    WiserepIncompleteExportError,
    WiserepUnreachableError,
    make_session,
)
from astrodash.infrastructure.wiserep.parsing import USER_AGENT

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wiserep"

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "wiserep-client-tests",
    }
}


def read_fixture(name: str) -> bytes:
    """Load a captured export payload.

    Args:
        name: The fixture's filename under ``fixtures/wiserep/``.

    Returns:
        The zip payload exactly as WISeREP served it.
    """
    return (FIXTURES / name).read_bytes()


def fixture_rows(name: str) -> list[str]:
    """Read a captured export's data lines.

    Args:
        name: The fixture's filename under ``fixtures/wiserep/``.

    Returns:
        The member's data lines, header excluded.
    """
    with zipfile.ZipFile(io.BytesIO(read_fixture(name))) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    return [line for line in text.splitlines() if line.strip()][1:]


def fixture_header(name: str) -> str:
    """Read a captured export's header line.

    Args:
        name: The fixture's filename under ``fixtures/wiserep/``.

    Returns:
        The header line as the export spelled it.
    """
    with zipfile.ZipFile(io.BytesIO(read_fixture(name))) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    return text.splitlines()[0]


def zip_export(header: str, lines: list[str]) -> bytes:
    """Repack export lines into a zip shaped like WISeREP's.

    Args:
        header: The header line to write first.
        lines: The data lines to write after it.

    Returns:
        A zip payload holding one ``wiserep_spectra.tsv`` member.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("wiserep_spectra.tsv", "\n".join([header, *lines]) + "\n")
    return buffer.getvalue()


class FakeResponse:
    """One queued response, streamed in chunks like ``requests`` streams one."""

    def __init__(
        self,
        body: bytes = b"",
        chunks: list[bytes] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Build the response.

        Args:
            body: The whole body, streamed as a single chunk.
            chunks: Explicit chunks to stream instead of ``body``.
            error: An exception for ``raise_for_status`` to raise.
        """
        self._chunks = chunks if chunks is not None else [body]
        self._error = error
        self.yielded = 0
        self.closed = False

    def raise_for_status(self) -> None:
        """Raise the configured status error, if any.

        Raises:
            Exception: Whatever was passed as ``error``.
        """
        if self._error is not None:
            raise self._error

    def iter_content(self, chunk_size: int = 1):
        """Yield the body in chunks, counting how many were taken.

        Args:
            chunk_size: Ignored; the chunk boundaries are fixed at build time.

        Yields:
            Each configured chunk.
        """
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    def __enter__(self) -> "FakeResponse":
        """Enter the response context.

        Returns:
            This response.
        """
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Leave the response context, marking it closed.

        Args:
            *exc_info: The exception triple, ignored.

        Returns:
            ``False``, so an exception in the body propagates.
        """
        self.closed = True
        return False


class FakeSession:
    """A session that answers from a queue and records every call."""

    def __init__(self, responses: list[object]) -> None:
        """Build the session.

        Args:
            responses: Responses to hand back in order. An entry that is an
                exception is raised instead of returned.
        """
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> object:
        """Answer the next queued response.

        Args:
            url: The requested URL.
            **kwargs: The request keyword arguments, recorded verbatim.

        Returns:
            The next queued response.

        Raises:
            Exception: When the next queued entry is an exception.
            AssertionError: When the queue is empty, meaning the code under
                test issued more requests than the case allows.
        """
        self.calls.append({"url": url, **kwargs})
        assert self._responses, f"unexpected extra request to {url}"
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def pages(self) -> list[str]:
        """The ``page`` parameter of every recorded call.

        Returns:
            One page value per call, in call order.
        """
        return [call["params"]["page"] for call in self.calls]


class FakeClock:
    """A stub clock whose monotonic time advances only when told to."""

    def __init__(self, step: float = 0.0) -> None:
        """Build the clock.

        Args:
            step: Seconds to advance on each ``monotonic`` reading.
        """
        self.now = 0.0
        self.step = step
        self.sleeps: list[float] = []
        self.wall = 1_000_000.0

    def monotonic(self) -> float:
        """Read and advance the monotonic clock.

        Returns:
            The current monotonic reading.
        """
        value = self.now
        self.now += self.step
        return value

    def time(self) -> float:
        """Read the wall clock.

        Returns:
            A fixed wall-clock reading, so the shared interval is computed
            against a stable now.
        """
        return self.wall

    def sleep(self, seconds: float) -> None:
        """Record a sleep instead of performing it.

        Args:
            seconds: How long the caller asked to wait.
        """
        self.sleeps.append(seconds)


def build_settings(**overrides: object):
    """Build a settings stand-in carrying only the transport fields.

    Args:
        **overrides: Fields to change from the defaults the client expects.

    Returns:
        An object exposing the ``wiserep_*`` settings as attributes.
    """
    values = {
        "wiserep_base_url": "https://www.wiserep.org",
        "wiserep_connect_timeout": 5.0,
        "wiserep_read_timeout": 30.0,
        "wiserep_total_deadline_seconds": 120.0,
        "wiserep_page_cap": 10,
        "wiserep_min_request_interval_seconds": 0.0,
        "wiserep_download_deadline_seconds": 60.0,
    }
    values.update(overrides)
    return type("TransportSettings", (), values)()


class WiserepPaginationTests(SimpleTestCase):
    """Walking the export to exhaustion."""

    def setUp(self) -> None:
        """Freeze the clock for every case in this class."""
        self.clock = FakeClock()
        patcher = mock.patch.object(client_module, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_client(self, responses: list[object], **overrides: object):
        """Build a client over a queued session.

        Args:
            responses: Responses the session hands back in order.
            **overrides: Settings fields to change.

        Returns:
            A tuple of the client and its session.
        """
        session = FakeSession(responses)
        client = WiserepClient(settings=build_settings(**overrides), session=session)
        return client, session

    def test_single_row_page_issues_exactly_one_request(self) -> None:
        client, session = self.make_client(
            [FakeResponse(read_fixture("2026puh_page0.zip"))]
        )

        rows = client.search_spectra_rows("2026puh")

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(session.calls), 1)

    def test_page_under_the_cap_issues_exactly_one_request(self) -> None:
        client, session = self.make_client(
            [FakeResponse(read_fixture("2002er_page0.zip"))]
        )

        rows = client.search_spectra_rows("2002er")

        self.assertEqual(len(rows), 35)
        self.assertLess(len(rows), EXPORT_PAGE_ROW_CAP)
        self.assertEqual(session.pages, ["0"])

    def test_full_page_fetches_the_next_and_concatenates(self) -> None:
        client, session = self.make_client(
            [
                FakeResponse(read_fixture("2023ixf_page0.zip")),
                FakeResponse(read_fixture("2023ixf_page1.zip")),
                FakeResponse(read_fixture("2026puh_page0.zip")),
            ]
        )

        rows = client.search_spectra_rows("2023ixf")

        self.assertEqual(session.pages, ["0", "1", "2"])
        self.assertEqual(len(rows), 101)
        spec_ids = {row["Spec. ID"] for row in rows}
        for line in fixture_rows("2023ixf_page0.zip") + fixture_rows(
            "2023ixf_page1.zip"
        ):
            self.assertIn(line.split("\t")[7], spec_ids)

    def test_page_repeating_every_row_stops_pagination(self) -> None:
        payload = read_fixture("2023ixf_page0.zip")
        client, session = self.make_client(
            [FakeResponse(payload), FakeResponse(payload)]
        )

        rows = client.search_spectra_rows("2023ixf")

        self.assertEqual(session.pages, ["0", "1"])
        self.assertEqual(len(rows), 50)

    def test_page_repeating_some_rows_continues_pagination(self) -> None:
        header = fixture_header("2023ixf_page0.zip")
        first = fixture_rows("2023ixf_page0.zip")
        second = fixture_rows("2023ixf_page1.zip")
        overlapping = zip_export(header, first[:25] + second[:25])

        client, session = self.make_client(
            [
                FakeResponse(read_fixture("2023ixf_page0.zip")),
                FakeResponse(overlapping),
                FakeResponse(read_fixture("2026puh_page0.zip")),
            ]
        )

        rows = client.search_spectra_rows("2023ixf")

        self.assertEqual(session.pages, ["0", "1", "2"])
        self.assertEqual(len(rows), 76)

    def test_page_cap_raises_incomplete_export(self) -> None:
        client, session = self.make_client(
            [
                FakeResponse(read_fixture("2023ixf_page0.zip")),
                FakeResponse(read_fixture("2023ixf_page1.zip")),
            ],
            wiserep_page_cap=2,
        )

        with self.assertRaises(WiserepIncompleteExportError):
            client.search_spectra_rows("2023ixf")

        self.assertEqual(session.pages, ["0", "1"])

    def test_request_carries_the_tsv_export_parameters(self) -> None:
        client, session = self.make_client(
            [FakeResponse(read_fixture("2002er_page0.zip"))]
        )

        client.search_spectra_rows("SN 2002er")

        call = session.calls[0]
        self.assertEqual(call["url"], f"https://www.wiserep.org{SEARCH_PATH}")
        self.assertEqual(
            call["params"],
            {
                "name": "SN 2002er",
                "format": "tsv",
                "public": "yes",
                "num_page": "50",
                "page": "0",
            },
        )
        self.assertEqual(call["timeout"], (5.0, 30.0))
        self.assertNotIn("verify", call)


class WiserepBoundsTests(SimpleTestCase):
    """The deadlines, the timeouts and the byte cap."""

    def test_read_timeout_raises_unreachable(self) -> None:
        session = FakeSession([requests.exceptions.ReadTimeout("too slow")])
        client = WiserepClient(settings=build_settings(), session=session)

        with self.assertRaises(WiserepUnreachableError):
            client.search_spectra_rows("2002er")

    def test_http_error_status_raises_unreachable(self) -> None:
        session = FakeSession(
            [FakeResponse(error=requests.exceptions.HTTPError("503"))]
        )
        client = WiserepClient(settings=build_settings(), session=session)

        with self.assertRaises(WiserepUnreachableError):
            client.search_spectra_rows("2002er")

    def test_elapsed_time_past_the_deadline_stops_pagination(self) -> None:
        clock = FakeClock(step=90.0)
        session = FakeSession([FakeResponse(read_fixture("2023ixf_page0.zip"))])
        client = WiserepClient(
            settings=build_settings(wiserep_total_deadline_seconds=120.0),
            session=session,
        )

        with mock.patch.object(client_module, "time", clock):
            with self.assertRaises(WiserepUnreachableError):
                client.search_spectra_rows("2023ixf")

        self.assertEqual(session.pages, ["0"])

    def test_body_past_the_byte_cap_is_abandoned(self) -> None:
        response = FakeResponse(chunks=[b"x" * 600, b"y" * 600, b"z" * 600])
        session = FakeSession([response])
        client = WiserepClient(
            settings=build_settings(), session=session, max_bytes=1000
        )

        with mock.patch.object(client_module, "time", FakeClock()):
            with self.assertRaises(WiserepUnreachableError):
                client.search_spectra_rows("2002er")

        self.assertEqual(response.yielded, 2)
        self.assertTrue(response.closed)

    def test_download_returns_the_file_bytes(self) -> None:
        session = FakeSession([FakeResponse(b"3500.0 1.0\n3501.0 1.1\n")])
        client = WiserepClient(settings=build_settings(), session=session)

        with mock.patch.object(client_module, "time", FakeClock()):
            data = client.download_spectrum("https://www.wiserep.org/a.ascii")

        self.assertEqual(data, b"3500.0 1.0\n3501.0 1.1\n")
        self.assertEqual(session.calls[0]["url"], "https://www.wiserep.org/a.ascii")

    def test_download_trickling_past_its_deadline_is_abandoned(self) -> None:
        response = FakeResponse(chunks=[b"a", b"b", b"c", b"d"])
        session = FakeSession([response])
        client = WiserepClient(
            settings=build_settings(wiserep_download_deadline_seconds=60.0),
            session=session,
        )

        with mock.patch.object(client_module, "time", FakeClock(step=40.0)):
            with self.assertRaises(WiserepUnreachableError):
                client.download_spectrum("https://www.wiserep.org/a.ascii")

        self.assertLess(response.yielded, 4)
        self.assertTrue(response.closed)


@override_settings(CACHES=LOCMEM_CACHES)
class WiserepRequestIntervalTests(SimpleTestCase):
    """The shared minimum interval between outbound requests."""

    def setUp(self) -> None:
        """Start each case with an empty cache."""
        cache.clear()
        self.addCleanup(cache.clear)

    def test_two_lookups_inside_the_interval_are_spaced_apart(self) -> None:
        clock = FakeClock()
        payload = read_fixture("2026puh_page0.zip")
        session = FakeSession([FakeResponse(payload), FakeResponse(payload)])
        client = WiserepClient(
            settings=build_settings(wiserep_min_request_interval_seconds=1.0),
            session=session,
        )

        with mock.patch.object(client_module, "time", clock):
            client.search_spectra_rows("2026puh")
            client.search_spectra_rows("2026puh")

        self.assertEqual(clock.sleeps, [1.0])

    def test_an_absent_cache_backend_leaves_the_lookup_working(self) -> None:
        clock = FakeClock()
        session = FakeSession([FakeResponse(read_fixture("2026puh_page0.zip"))])
        client = WiserepClient(
            settings=build_settings(wiserep_min_request_interval_seconds=1.0),
            session=session,
        )
        broken = mock.Mock()
        broken.get.side_effect = RuntimeError("no cache backend")
        broken.set.side_effect = RuntimeError("no cache backend")

        with mock.patch.object(client_module, "time", clock):
            with mock.patch.object(client_module, "cache", broken):
                rows = client.search_spectra_rows("2026puh")

        self.assertEqual(len(rows), 1)
        self.assertEqual(clock.sleeps, [])


class WiserepSessionTests(SimpleTestCase):
    """The session's identity and its transport posture."""

    def test_session_sends_the_shared_descriptive_user_agent(self) -> None:
        session = make_session()

        self.assertEqual(session.headers["User-Agent"], USER_AGENT)
        self.assertIn("AstroDASH", session.headers["User-Agent"])

    def test_session_leaves_certificate_verification_enabled(self) -> None:
        session = make_session()

        self.assertTrue(session.verify)

    def test_session_mounts_a_retry_adapter_for_both_schemes(self) -> None:
        session = make_session()

        for prefix in ("https://", "http://"):
            retries = session.get_adapter(prefix).max_retries
            self.assertGreater(retries.total, 0)
            self.assertIn(503, retries.status_forcelist)


class WiserepMultiObjectExportTests(SimpleTestCase):
    """A well-formed export spanning more than one object still parses."""

    def test_multi_object_page_returns_every_row(self) -> None:
        session = FakeSession(
            [
                FakeResponse(read_fixture("name_2002_multi_object_page0.zip")),
                FakeResponse(read_fixture("2026puh_page0.zip")),
            ]
        )
        client = WiserepClient(settings=build_settings(), session=session)

        with mock.patch.object(client_module, "time", FakeClock()):
            rows = client.search_spectra_rows("2002")

        self.assertEqual(len(rows), 51)
        self.assertGreater(len({row["IAU name"] for row in rows}), 1)


class WiserepRetryWaitTests(SimpleTestCase):
    """The waits the retry adapter takes inside ``HTTPAdapter.send``."""

    def setUp(self) -> None:
        """Take the retry policy the session actually mounts."""
        self.retries = make_session().get_adapter("https://").max_retries

    def retry_response(self, retry_after: str) -> mock.Mock:
        """Build a stand-in response carrying a ``Retry-After`` header.

        Args:
            retry_after: The header value WISeREP would have sent.

        Returns:
            An object exposing the header the way ``urllib3`` reads it.
        """
        return mock.Mock(headers={"Retry-After": retry_after})

    def set_deadline(self, deadline: float) -> None:
        """Put an absolute deadline in scope for the duration of a case.

        Args:
            deadline: The monotonic time the request must finish by.
        """
        token = client_module._request_deadline.set(deadline)
        self.addCleanup(client_module._request_deadline.reset, token)

    def test_the_mounted_policy_is_deadline_aware_and_stays_so_when_cloned(
        self,
    ) -> None:
        self.assertIsInstance(self.retries, client_module._DeadlineAwareRetry)
        self.assertIsInstance(self.retries.new(), client_module._DeadlineAwareRetry)

    def test_a_long_retry_after_is_clamped_to_the_remaining_budget(self) -> None:
        clock = FakeClock()
        self.set_deadline(12.0)

        with mock.patch.object(client_module, "time", clock):
            self.retries.sleep(self.retry_response("3600"))

        self.assertEqual(clock.sleeps, [12.0])

    def test_a_long_retry_after_is_clamped_even_with_no_budget_in_scope(self) -> None:
        clock = FakeClock()

        with mock.patch.object(client_module, "time", clock):
            self.retries.sleep(self.retry_response("3600"))

        self.assertEqual(clock.sleeps, [client_module.MAX_RETRY_WAIT_SECONDS])

    def test_a_retry_asked_for_past_the_deadline_is_refused(self) -> None:
        clock = FakeClock()
        self.set_deadline(-1.0)

        with mock.patch.object(client_module, "time", clock):
            with self.assertRaises(WiserepUnreachableError):
                self.retries.sleep(self.retry_response("3600"))

        self.assertEqual(clock.sleeps, [])


class WiserepWholeOperationBudgetTests(SimpleTestCase):
    """One budget for the whole walk, not one per page."""

    def test_each_page_gets_only_what_is_left_of_the_budget(self) -> None:
        clock = FakeClock(step=20.0)
        session = FakeSession(
            [
                FakeResponse(read_fixture("2023ixf_page0.zip")),
                FakeResponse(read_fixture("2023ixf_page1.zip")),
                FakeResponse(read_fixture("2026puh_page0.zip")),
            ]
        )
        client = WiserepClient(
            settings=build_settings(wiserep_total_deadline_seconds=400.0),
            session=session,
        )
        budgets: list[float] = []
        bounded = client._get_bounded

        def record(url, params, deadline_seconds, what):
            budgets.append(deadline_seconds)
            return bounded(
                url, params=params, deadline_seconds=deadline_seconds, what=what
            )

        with mock.patch.object(client, "_get_bounded", record):
            with mock.patch.object(client_module, "time", clock):
                client.search_spectra_rows("2023ixf")

        self.assertEqual(len(budgets), 3)
        self.assertLessEqual(budgets[0], 400.0)
        self.assertLess(budgets[1], budgets[0])
        self.assertLess(budgets[2], budgets[1])

    def test_a_request_timeout_is_capped_by_what_is_left_of_the_budget(self) -> None:
        session = FakeSession([FakeResponse(b"3500.0 1.0\n")])
        client = WiserepClient(
            settings=build_settings(
                wiserep_read_timeout=30.0, wiserep_download_deadline_seconds=4.0
            ),
            session=session,
        )

        with mock.patch.object(client_module, "time", FakeClock()):
            client.download_spectrum("https://www.wiserep.org/a.ascii")

        self.assertEqual(session.calls[0]["timeout"], (5.0, 4.0))


class WiserepUnreadableExportTests(SimpleTestCase):
    """Payloads that arrive with a good status and are not an export."""

    def client_over(self, payload: bytes) -> WiserepClient:
        """Build a client whose single response carries a payload.

        Args:
            payload: The bytes the stubbed response streams.

        Returns:
            A client wired to a session queued with that one response.
        """
        return WiserepClient(
            settings=build_settings(), session=FakeSession([FakeResponse(payload)])
        )

    def test_an_html_error_page_served_as_success_is_unreachable(self) -> None:
        client = self.client_over(
            b"<html><body>WISeREP is undergoing maintenance</body></html>"
        )

        with mock.patch.object(client_module, "time", FakeClock()):
            with self.assertRaises(WiserepUnreachableError):
                client.search_spectra_rows("2002er")

    def test_a_zip_without_a_delimited_member_is_unreachable(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "no table here\n")
        client = self.client_over(buffer.getvalue())

        with mock.patch.object(client_module, "time", FakeClock()):
            with self.assertRaises(WiserepUnreachableError):
                client.search_spectra_rows("2002er")

    def test_an_export_missing_downstream_columns_is_not_a_transport_failure(
        self,
    ) -> None:
        payload = zip_export(
            "IAU name\tObj. Type\tRedshift",
            ["SN 2002er\tSN Ia\t0.0086", "SN 2023ixf\tSN II\t0.000804"],
        )
        client = self.client_over(payload)

        with mock.patch.object(client_module, "time", FakeClock()):
            rows = client.search_spectra_rows("2002er")

        self.assertEqual(len(rows), 2)
        self.assertNotIn("Spec. ID", rows[0])
        self.assertNotIn("Ascii file", rows[0])
