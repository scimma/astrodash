"""Tests for reading one chosen WISeREP spectrum into a domain spectrum.

The real-data case runs against the ASCII file WISeREP serves for ``2026puh``,
captured byte-for-byte under ``fixtures/wiserep/``, paired with the captured
export page for the same object. Downloads are served by a stub transport that
records what it was asked for, and the redirect policy is exercised against a
stub HTTP adapter, so nothing here reaches the network.
"""

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import requests
from django.test import SimpleTestCase
from requests.adapters import HTTPAdapter
from urllib3.response import HTTPResponse

from astrodash.infrastructure.storage.wiserep_spectrum_repository import (
    MIN_WINDOW_POINTS,
    WiserepSpectrumRepository,
    WiserepUnusableSpectrumError,
    allowed_host,
    allowed_port,
    make_host_pinned_session,
)
from astrodash.infrastructure.wiserep.parsing import (
    MAX_UNCOMPRESSED_BYTES,
    extract_csv_text_from_zip,
    parse_csv_rows,
)
from astrodash.infrastructure.wiserep.selection import (
    SpectrumCandidate,
    rank_candidates,
)
from astrodash.models import SOURCE_NAME_WISEREP

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wiserep"

#: The ASCII spectrum WISeREP serves for ``2026puh``, captured verbatim.
REAL_SPECTRUM_FIXTURE = "SN2026puh_2026-06-25_Gemini-S_GMOS-S_TNS.txt"

#: How many of that file's points fall inside the 4000-9000 Angstrom window.
REAL_SPECTRUM_IN_WINDOW_POINTS = 1282

HOST = "www.wiserep.org"

SETTINGS = SimpleNamespace(wiserep_base_url=f"https://{HOST}")

#: Settings complete enough for the repository to build its own transport, so
#: a case can exercise the default download path rather than a stub. The
#: request interval is zeroed because nothing here reaches the network or the
#: cache.
TRANSPORT_SETTINGS = SimpleNamespace(
    wiserep_base_url=f"https://{HOST}",
    wiserep_connect_timeout=1.0,
    wiserep_read_timeout=1.0,
    wiserep_download_deadline_seconds=5.0,
    wiserep_min_request_interval_seconds=0.0,
)


def ascii_spectrum(in_window: int, below_window: int = 0) -> bytes:
    """Build a two-column ASCII spectrum with a known in-window point count.

    Args:
        in_window: How many points to place inside the 4000-9000 Angstrom
            window.
        below_window: How many points to place below it, which the readers
            drop.

    Returns:
        The file's bytes, headed by a comment line like WISeREP's files.
    """
    lines = ["# Wavelength(Angstrom) Flux"]
    lines += [f"{3000.0 + index:.4f} 1.0e-17" for index in range(below_window)]
    lines += [f"{4000.0 + index:.4f} 2.0e-17" for index in range(in_window)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def candidate(**overrides: Any) -> SpectrumCandidate:
    """Build one chosen candidate.

    Args:
        **overrides: Candidate fields to replace.

    Returns:
        A candidate pointing at an on-host ``.txt`` download, defaulted to the
        captured ``2026puh`` row's values.
    """
    values: dict[str, Any] = {
        "designation": "2026puh",
        "spectrum_id": "91887",
        "observation_date": "2026-06-25 00:00:00.00",
        "julian_date": 2461216.5,
        "telescope": "Gemini-S",
        "instrument": "GMOS-S",
        "quality": "",
        "redshift": 0.012,
        "ascii_url": f"https://{HOST}/system/files/uploaded/TNS/spectrum.txt",
    }
    values.update(overrides)
    return SpectrumCandidate(**values)


class StubClient:
    """A transport that answers downloads from a queue and records them."""

    def __init__(self, payloads: Optional[list[bytes]] = None) -> None:
        """Build the transport.

        Args:
            payloads: Bodies to hand back in order. A case that expects no
                download leaves this empty.
        """
        self._payloads = list(payloads or [])
        self.urls: list[str] = []

    def download_spectrum(self, url: str) -> bytes:
        """Answer the next queued body.

        Args:
            url: The requested URL, recorded.

        Returns:
            The next queued body.

        Raises:
            AssertionError: When the queue is empty, meaning the code under
                test issued a download the case does not allow.
        """
        self.urls.append(url)
        assert self._payloads, f"unexpected download from {url}"
        return self._payloads.pop(0)


class RedirectAdapter(HTTPAdapter):
    """An adapter that answers every request with one fixed redirect."""

    def __init__(self, location: str) -> None:
        """Build the adapter.

        Args:
            location: The ``Location`` header to answer with.
        """
        super().__init__()
        self.location = location
        self.urls: list[str] = []

    def send(self, request: Any, **kwargs: Any) -> requests.Response:
        """Answer the request with the configured redirect.

        Args:
            request: The prepared request, whose URL is recorded.
            **kwargs: Transport options, ignored.

        Returns:
            A 302 response pointing at ``location``.
        """
        self.urls.append(request.url)
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = self.location
        response.url = request.url
        response.request = request
        response.raw = HTTPResponse(
            body=io.BytesIO(b""), status=302, preload_content=False
        )
        return response


def repository(client: StubClient) -> WiserepSpectrumRepository:
    """Build the repository over a stub transport.

    Args:
        client: The transport to download through.

    Returns:
        A repository pinned to the test host.
    """
    return WiserepSpectrumRepository(settings=SETTINGS, client=client)


class WindowThresholdTests(SimpleTestCase):
    """The usable-window point count decides whether a download is usable."""

    def test_below_threshold_is_refused_as_unusable(self) -> None:
        """Covers AE12: too few in-window points yields no spectrum."""
        client = StubClient([ascii_spectrum(MIN_WINDOW_POINTS - 1)])
        with self.assertRaises(WiserepUnusableSpectrumError) as caught:
            repository(client).get_for_candidate(candidate())
        self.assertIn(str(MIN_WINDOW_POINTS - 1), str(caught.exception))

    def test_points_outside_the_window_do_not_count(self) -> None:
        """A file padded below the window is still judged on what survives."""
        client = StubClient([ascii_spectrum(MIN_WINDOW_POINTS - 1, below_window=500)])
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(candidate())

    def test_exactly_the_threshold_is_accepted(self) -> None:
        """The threshold itself is usable, not refused."""
        client = StubClient([ascii_spectrum(MIN_WINDOW_POINTS)])
        spectrum = repository(client).get_for_candidate(candidate())
        self.assertEqual(len(spectrum.x), MIN_WINDOW_POINTS)


class RealSpectrumTests(SimpleTestCase):
    """The captured 2026puh spectrum reads as usable."""

    def test_captured_file_yields_its_in_window_points(self) -> None:
        """The real file's 1,282 in-window points survive and classify."""
        payload = (FIXTURES / REAL_SPECTRUM_FIXTURE).read_bytes()
        client = StubClient([payload])
        spectrum = repository(client).get_for_candidate(candidate())
        self.assertEqual(len(spectrum.x), REAL_SPECTRUM_IN_WINDOW_POINTS)
        self.assertEqual(len(spectrum.y), REAL_SPECTRUM_IN_WINDOW_POINTS)
        self.assertGreaterEqual(min(spectrum.x), 4000.0)
        self.assertLessEqual(max(spectrum.x), 9000.0)

    def test_captured_export_and_file_populate_every_provenance_field(
        self,
    ) -> None:
        """The unit's verification: export plus file give full provenance."""
        rows = parse_csv_rows(
            extract_csv_text_from_zip(
                (FIXTURES / "2026puh_page0.zip").read_bytes(),
                delimiter="\t",
                max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
            ),
            delimiter="\t",
        )
        chosen = rank_candidates(rows, "2026puh").selected
        self.assertIsNotNone(chosen)

        payload = (FIXTURES / REAL_SPECTRUM_FIXTURE).read_bytes()
        client = StubClient([payload])
        spectrum = repository(client).get_for_candidate(chosen)

        self.assertEqual(client.urls, [chosen.ascii_url])
        self.assertEqual(len(spectrum.x), REAL_SPECTRUM_IN_WINDOW_POINTS)
        self.assertEqual(spectrum.object_designation, chosen.designation)
        self.assertEqual(spectrum.source_spectrum_id, "91887")
        self.assertEqual(spectrum.source_name, SOURCE_NAME_WISEREP)
        self.assertIsNotNone(spectrum.retrieved_at)
        self.assertEqual(spectrum.meta["ascii_url"], chosen.ascii_url)


class ReaderDispatchTests(SimpleTestCase):
    """The download is read by the existing extension-dispatched readers."""

    def test_flm_candidate_reads(self) -> None:
        """A ``.flm`` download parses."""
        client = StubClient([ascii_spectrum(150)])
        spectrum = repository(client).get_for_candidate(
            candidate(ascii_url=f"https://{HOST}/system/files/a.flm")
        )
        self.assertEqual(len(spectrum.x), 150)
        self.assertEqual(spectrum.file_name, "a.flm")

    def test_dat_candidate_reads(self) -> None:
        """A ``.dat`` download parses."""
        client = StubClient([ascii_spectrum(150)])
        spectrum = repository(client).get_for_candidate(
            candidate(ascii_url=f"https://{HOST}/system/files/a.dat")
        )
        self.assertEqual(len(spectrum.x), 150)
        self.assertEqual(spectrum.file_name, "a.dat")

    def test_html_error_body_is_refused_rather_than_parsed(self) -> None:
        """An HTML error page carries no spectrum and is refused."""
        html = (
            b"<html><head><title>500 Internal Server Error</title></head>"
            b"<body><h1>Service unavailable</h1></body></html>"
        )
        client = StubClient([html])
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(candidate())


class RedshiftTests(SimpleTestCase):
    """The candidate's redshift is carried through as recorded."""

    def test_recorded_redshift_is_attached(self) -> None:
        """A recorded redshift reaches the spectrum."""
        client = StubClient([ascii_spectrum(150)])
        spectrum = repository(client).get_for_candidate(candidate(redshift=0.009063))
        self.assertEqual(spectrum.redshift, 0.009063)

    def test_missing_redshift_stays_missing(self) -> None:
        """A blank redshift yields no redshift, never zero."""
        client = StubClient([ascii_spectrum(150)])
        spectrum = repository(client).get_for_candidate(candidate(redshift=None))
        self.assertIsNone(spectrum.redshift)


class DownloadTargetTests(SimpleTestCase):
    """Only the configured host over https may be fetched."""

    def test_offsite_url_is_refused_without_a_request(self) -> None:
        """A URL off the configured host is refused before any download."""
        client = StubClient()
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(
                candidate(ascii_url="https://evil.example.com/a.dat")
            )
        self.assertEqual(client.urls, [])

    def test_plain_http_url_is_refused_without_a_request(self) -> None:
        """An on-host URL over http is refused before any download."""
        client = StubClient()
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(
                candidate(ascii_url=f"http://{HOST}/system/files/a.dat")
            )
        self.assertEqual(client.urls, [])

    def test_url_carrying_credentials_is_refused_without_a_request(self) -> None:
        """An on-host URL carrying credentials is refused before any download."""
        client = StubClient()
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(
                candidate(ascii_url=f"https://user:pass@{HOST}/system/files/a.dat")
            )
        self.assertEqual(client.urls, [])

    def test_offsite_redirect_is_not_followed(self) -> None:
        """A redirect leaving the configured host is refused, not followed."""
        adapter = RedirectAdapter("https://evil.example.com/a.dat")
        session = make_host_pinned_session(allowed_host(SETTINGS))
        session.mount("https://", adapter)
        with self.assertRaises(WiserepUnusableSpectrumError):
            session.get(f"https://{HOST}/system/files/a.dat")
        self.assertEqual(adapter.urls, [f"https://{HOST}/system/files/a.dat"])

    def test_url_on_another_port_is_refused_without_a_request(self) -> None:
        """An on-host https URL on another port reaches a different service."""
        client = StubClient()
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(
                candidate(ascii_url=f"https://{HOST}:8443/system/files/a.dat")
            )
        self.assertEqual(client.urls, [])

    def test_url_naming_the_default_port_explicitly_is_accepted(self) -> None:
        """Spelling out port 443 names the same origin and stays usable."""
        client = StubClient([ascii_spectrum(150)])
        spectrum = repository(client).get_for_candidate(
            candidate(ascii_url=f"https://{HOST}:443/system/files/a.dat")
        )
        self.assertEqual(len(spectrum.x), 150)

    def test_url_with_an_unreadable_port_is_refused_without_a_request(self) -> None:
        """A port that will not parse is refused rather than guessed at."""
        client = StubClient()
        with self.assertRaises(WiserepUnusableSpectrumError):
            repository(client).get_for_candidate(
                candidate(ascii_url=f"https://{HOST}:443a/system/files/a.dat")
            )
        self.assertEqual(client.urls, [])

    def test_redirect_to_another_port_is_not_followed(self) -> None:
        """A same-host redirect changing the port is refused, not followed."""
        adapter = RedirectAdapter(f"https://{HOST}:8443/a.dat")
        session = make_host_pinned_session(
            allowed_host(SETTINGS), allowed_port(SETTINGS)
        )
        session.mount("https://", adapter)
        with self.assertRaises(WiserepUnusableSpectrumError):
            session.get(f"https://{HOST}/system/files/a.dat")
        self.assertEqual(adapter.urls, [f"https://{HOST}/system/files/a.dat"])


class DefaultTransportTargetTests(SimpleTestCase):
    """The transport the repository builds for itself carries the same pin.

    The cases above inject a stub transport, which leaves the constructor's
    own ``WiserepClient`` over a host-pinned session unexercised. These build
    the repository with no client, so the session actually used for downloads
    is the one under test.
    """

    def repository(self) -> WiserepSpectrumRepository:
        """Build a repository over its own default transport.

        Returns:
            A repository pinned to the test origin, holding the client and
            session it built for itself.
        """
        return WiserepSpectrumRepository(settings=TRANSPORT_SETTINGS)

    def mount_redirect(
        self, repo: WiserepSpectrumRepository, location: str
    ) -> RedirectAdapter:
        """Answer every request the repository's own session issues.

        Args:
            repo: The repository whose default session should be intercepted.
            location: The ``Location`` header to answer with.

        Returns:
            The mounted adapter, recording what was requested.
        """
        adapter = RedirectAdapter(location)
        repo._client._session.mount("https://", adapter)
        return adapter

    def test_offsite_redirect_is_refused_on_the_default_download_path(self) -> None:
        """The default path refuses an off-host redirect as unusable."""
        repo = self.repository()
        adapter = self.mount_redirect(repo, "https://evil.example.com/a.dat")
        with self.assertRaises(WiserepUnusableSpectrumError):
            repo.get_for_candidate(candidate())
        self.assertEqual(adapter.urls, [candidate().ascii_url])

    def test_port_changing_redirect_is_refused_on_the_default_download_path(
        self,
    ) -> None:
        """The default path refuses a same-host port change as unusable."""
        repo = self.repository()
        adapter = self.mount_redirect(repo, f"https://{HOST}:8443/a.dat")
        with self.assertRaises(WiserepUnusableSpectrumError):
            repo.get_for_candidate(candidate())
        self.assertEqual(adapter.urls, [candidate().ascii_url])

    def test_offsite_url_is_refused_before_the_default_transport_is_used(
        self,
    ) -> None:
        """An off-host URL is refused before the default session is reached."""
        repo = self.repository()
        adapter = self.mount_redirect(repo, f"https://{HOST}/a.dat")
        with self.assertRaises(WiserepUnusableSpectrumError):
            repo.get_for_candidate(
                candidate(ascii_url="https://evil.example.com/a.dat")
            )
        self.assertEqual(adapter.urls, [])
