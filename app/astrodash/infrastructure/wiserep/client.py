"""Bounded HTTP transport for WISeREP's structured spectrum export.

WISeREP serves ``/search/spectra?format=tsv`` as a zip holding one
tab-separated table. The export is hard-capped at 50 rows per response and
``num_page`` cannot raise that cap, so a single response is an arbitrary
subset of the matches for a designation; further rows come from ``page=1``
upward. Row order carries no meaning, so ranking can only run once every page
has been read. This module walks the pages to exhaustion and hands the
concatenated rows to the caller.

Exhaustion has to be bounded, because ``requests`` applies its read timeout
per read rather than across a response: a slow upstream that keeps trickling
bytes holds a worker indefinitely while never breaching any single-request
timeout. Deployment runs two gunicorn workers behind a 300-second limit, so
two stalled lookups are an outage. Every outbound request here is therefore
bounded four ways -- a connect and read timeout per request, an absolute
deadline across the whole pagination, a cap on the number of pages, and a cap
on the bytes read from any one response -- and the spectrum download carries
its own absolute deadline for the same reason. The retry waits ``urllib3``
takes inside the adapter are clamped to that same deadline, because
``Retry-After`` is uncapped upstream and the wait happens where none of the
checks below can see it.

A shared minimum interval between outbound requests is held in the Django
cache so that concurrent workers throttle together, matching the one-second
spacing the offline scraper under ``app/wiserep_scrape/`` already applies. The
slim profiles run without Redis, so an unavailable cache leaves the interval
unenforced rather than failing the lookup.

Certificate verification is left at the library default; nothing here disables
it.
"""

from __future__ import annotations

import logging
import time
import zipfile
from contextvars import ContextVar

import requests
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from astrodash.config.settings import Settings, get_settings
from astrodash.core.exceptions import ExternalServiceException
from astrodash.infrastructure.wiserep.parsing import (
    SPEC_ID_ALIASES,
    MAX_UNCOMPRESSED_BYTES,
    USER_AGENT,
    extract_csv_text_from_zip,
    find_column,
    parse_csv_rows,
)

logger = logging.getLogger(__name__)

# WISeREP's export hard-caps a response at this many rows. Measured: asking
# for num_page=200 returns output byte-identical to num_page=50. A response
# carrying exactly this many rows therefore means "there may be more", and one
# carrying fewer means the designation is exhausted.
EXPORT_PAGE_ROW_CAP = 50

# The export path, appended to the configured base URL.
SEARCH_PATH = "/search/spectra"

# The export's delimiter, and the ``format`` value that produces it.
_EXPORT_FORMAT = "tsv"
_EXPORT_DELIMITER = "\t"

# Cache key holding the wall-clock time of the last outbound WISeREP request,
# shared by every worker that can reach the cache.
_LAST_REQUEST_CACHE_KEY = "wiserep:last-request-time"

# Read responses in pieces so the byte cap and the download deadline are
# enforced partway through rather than after the whole body has arrived.
_STREAM_CHUNK_BYTES = 64 * 1024

# Known spellings of the column that identifies one spectrum. Used to tell a
# repeated row from a new one; columns are matched by name, never position.


class WiserepUnreachableError(ExternalServiceException):
    """Raised when WISeREP could not be read within its bounds.

    Covers a refused or failed connection, an HTTP error status, a per-request
    timeout, a response that exceeds the byte cap, and elapsed time past the
    pagination or download deadline. It is deliberately distinct from a
    designation that resolved to nothing.
    """

    def __init__(self, message: str) -> None:
        """Build the failure.

        Args:
            message: What was being attempted and which bound was breached.
        """
        super().__init__("WISeREP", message)


class WiserepIncompleteExportError(ExternalServiceException):
    """Raised when pagination hit the page cap with rows still outstanding.

    Ranking is only correct over every eligible row for a designation, so a
    truncated set is refused rather than returned.
    """

    def __init__(self, message: str) -> None:
        """Build the failure.

        Args:
            message: Which designation was being resolved and where it stopped.
        """
        super().__init__("WISeREP", message)


# Ceiling on any single wait the retry policy performs when no operation
# deadline is in scope. ``urllib3`` does not cap ``Retry-After``, so a WISeREP
# that answers 429 with an hour would otherwise park a worker for an hour
# inside ``HTTPAdapter.send``, where none of this module's deadlines can see
# it.
MAX_RETRY_WAIT_SECONDS = 30.0

# The absolute monotonic time the request in progress must finish by, set by
# :meth:`WiserepClient._get_bounded` around each GET so that retry waits taken
# inside the adapter are bounded by the same budget as the body read. A
# context variable rather than an attribute of the retry policy, because
# ``urllib3`` clones the policy on every attempt and the session is shared.
_request_deadline: ContextVar[float | None] = ContextVar(
    "wiserep_request_deadline", default=None
)


class _DeadlineAwareRetry(Retry):
    """A retry policy whose waits cannot outlive the caller's deadline.

    ``Retry.sleep`` runs inside ``HTTPAdapter.send``, before any of this
    module's own deadline checks are reached, and ``urllib3`` honours an
    uncapped ``Retry-After``. This policy clamps every wait to whatever budget
    :data:`_request_deadline` has left and refuses to retry once that budget is
    gone.
    """

    def sleep(self, response: object | None = None) -> None:
        """Wait before the next attempt, clamped to the remaining budget.

        Args:
            response: The response that asked for the retry, when the retry was
                triggered by a status rather than by a transport error.

        Raises:
            WiserepUnreachableError: When the operation's deadline has already
                elapsed, so no further attempt can be made within it.
        """
        wait: float | None = None
        if self.respect_retry_after_header and response is not None:
            wait = self.get_retry_after(response)  # type: ignore[arg-type]
        if wait is None:
            wait = self.get_backoff_time()

        deadline = _request_deadline.get()
        if deadline is None:
            if wait > 0:
                time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WiserepUnreachableError(
                "WISeREP asked for another attempt after the deadline for this "
                "request had already elapsed"
            )
        if wait > 0:
            time.sleep(min(wait, remaining, MAX_RETRY_WAIT_SECONDS))


def make_session(user_agent: str = USER_AGENT) -> requests.Session:
    """Build a session that identifies itself and retries transient failures.

    Mirrors the adapter mount the offline scraper uses, with a shorter retry
    budget because the request path runs under an absolute deadline, and with a
    retry policy that clamps its waits to that deadline rather than to whatever
    ``Retry-After`` asks for.

    Args:
        user_agent: The ``User-Agent`` to send. Defaults to the descriptive
            identity shared with the scraper.

    Returns:
        A session with the retry adapter mounted and certificate verification
        left at its default.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/zip,text/tab-separated-values;q=0.9,*/*;q=0.8",
        }
    )

    retries = _DeadlineAwareRetry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _row_identity(row: dict[str, str], spec_id_column: str | None) -> str:
    """Reduce an export row to a value that tells it from a different row.

    Args:
        row: One parsed export row.
        spec_id_column: The header that holds the spectrum identifier, as the
            export spelled it, or ``None`` when the export carries no such
            column.

    Returns:
        The spectrum identifier when one is present, otherwise a rendering of
        the whole row.
    """
    if spec_id_column:
        value = row.get(spec_id_column, "")
        if value:
            return value
    return repr(sorted(row.items()))


class WiserepClient:
    """Reads WISeREP's export and spectrum files under explicit bounds."""

    def __init__(
        self,
        settings: Settings | None = None,
        session: requests.Session | None = None,
        max_bytes: int = MAX_UNCOMPRESSED_BYTES,
    ) -> None:
        """Build the client.

        Args:
            settings: The application settings carrying the base URL, the
                timeouts, the deadlines, the page cap and the shared request
                interval. Loaded from the environment when omitted.
            session: A session to issue requests through. One is built by
                :func:`make_session` when omitted.
            max_bytes: The most bytes to read from any one response, and the
                ceiling the export member is allowed to expand to. Defaults to
                the ceiling the parsing module already applies.
        """
        self._settings = settings or get_settings()
        self._session = session or make_session()
        self._max_bytes = max_bytes

    @property
    def search_url(self) -> str:
        """The export endpoint this client reads.

        Returns:
            The configured base URL joined to the export path.
        """
        return f"{self._settings.wiserep_base_url.rstrip('/')}{SEARCH_PATH}"

    def search_spectra_rows(self, designation: str) -> list[dict[str, str]]:
        """Read every export row WISeREP holds for a designation.

        Walks ``page`` upward from zero, stopping when a page returns fewer
        rows than the export's cap or when a page adds nothing that earlier
        pages did not already carry. A page that repeats rows while also
        carrying unseen ones continues pagination.

        Args:
            designation: The object designation to search for, passed to
                WISeREP as ``name``.

        Returns:
            One dictionary per distinct row, keyed by the column names the
            export sent, in the order the pages returned them.

        Raises:
            WiserepUnreachableError: When a request failed, returned an error
                status, exceeded its timeout or the byte cap, or when the
                pagination deadline elapsed with pages still outstanding. The
                deadline covers the whole walk, not each page, so the total
                time spent here cannot exceed
                ``wiserep_total_deadline_seconds``.
            WiserepIncompleteExportError: When the page cap was reached while
                rows were still outstanding, so the set would be partial.
        """
        deadline_seconds = self._settings.wiserep_total_deadline_seconds
        page_cap = self._settings.wiserep_page_cap
        deadline = time.monotonic() + deadline_seconds

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        page = 0

        while True:
            if page >= page_cap:
                raise WiserepIncompleteExportError(
                    f"export for '{designation}' still had rows outstanding at "
                    f"the {page_cap}-page cap; refusing a partial set"
                )
            remaining = deadline - time.monotonic()
            if page > 0 and remaining <= 0:
                raise WiserepUnreachableError(
                    f"export for '{designation}' exceeded the "
                    f"{deadline_seconds}-second deadline after {page} page(s)"
                )

            page_rows = self._fetch_page(designation, page, remaining)
            spec_id_column = (
                find_column(page_rows[0].keys(), SPEC_ID_ALIASES) if page_rows else None
            )

            unseen = 0
            for row in page_rows:
                identity = _row_identity(row, spec_id_column)
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(row)
                unseen += 1

            logger.debug(
                "WISeREP export page %d for %r: %d row(s), %d new",
                page,
                designation,
                len(page_rows),
                unseen,
            )

            if len(page_rows) < EXPORT_PAGE_ROW_CAP:
                break
            if unseen == 0:
                break
            page += 1

        logger.info(
            "WISeREP export for %r: %d distinct row(s) over %d page(s)",
            designation,
            len(rows),
            page + 1,
        )
        return rows

    def download_spectrum(self, url: str) -> bytes:
        """Download one spectrum file under an absolute deadline.

        Args:
            url: The fully-qualified download URL the export carried in its
                ``Ascii file`` column.

        Returns:
            The file's bytes.

        Raises:
            WiserepUnreachableError: When the request failed, returned an error
                status, exceeded its timeout, exceeded the byte cap, or kept
                trickling bytes past the download deadline.
        """
        return self._get_bounded(
            url,
            params=None,
            deadline_seconds=self._settings.wiserep_download_deadline_seconds,
            what=f"spectrum download from {url}",
        )

    def _fetch_page(
        self, designation: str, page: int, deadline_seconds: float
    ) -> list[dict[str, str]]:
        """Read and parse one page of the export.

        Args:
            designation: The designation being searched for.
            page: The zero-based page index.
            deadline_seconds: What is left of the whole walk's budget, which is
                all this page is allowed to take.

        Returns:
            The page's parsed rows.

        Raises:
            WiserepUnreachableError: When the request failed, returned an error
                status, exceeded its timeout or the byte cap, or when the
                payload was not a readable export.
        """
        payload = self._get_bounded(
            self.search_url,
            params={
                "name": designation,
                "format": _EXPORT_FORMAT,
                "public": "yes",
                "num_page": str(EXPORT_PAGE_ROW_CAP),
                "page": str(page),
            },
            deadline_seconds=deadline_seconds,
            what=f"export page {page} for '{designation}'",
        )

        try:
            text = extract_csv_text_from_zip(
                payload,
                delimiter=_EXPORT_DELIMITER,
                max_uncompressed_bytes=self._max_bytes,
            )
        except (RuntimeError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise WiserepUnreachableError(
                f"export page {page} for '{designation}' was not a readable "
                f"export: {exc}"
            ) from exc

        return parse_csv_rows(text, delimiter=_EXPORT_DELIMITER)

    def _get_bounded(
        self,
        url: str,
        params: dict[str, str] | None,
        deadline_seconds: float,
        what: str,
    ) -> bytes:
        """Issue one GET and stream its body under every configured bound.

        Args:
            url: The URL to read.
            params: Query parameters, or ``None`` for a bare URL.
            deadline_seconds: The budget this GET has to finish in, measured
                from the moment the request is issued. It bounds the retry
                waits taken inside the adapter as well as the body read, and
                caps the per-request read timeout.
            what: A phrase naming the attempt, used in failure messages.

        Returns:
            The response body.

        Raises:
            WiserepUnreachableError: When the request failed, returned an error
                status, exceeded its timeout or the byte cap, or kept arriving
                past ``deadline_seconds``.
        """
        self._respect_min_request_interval()

        started = time.monotonic()
        timeout = (
            self._settings.wiserep_connect_timeout,
            min(self._settings.wiserep_read_timeout, max(deadline_seconds, 0.0)),
        )
        token = _request_deadline.set(started + deadline_seconds)

        try:
            with self._session.get(
                url, params=params, timeout=timeout, stream=True
            ) as response:
                response.raise_for_status()
                return self._read_bounded_body(
                    response, started, deadline_seconds, what
                )
        except requests.RequestException as exc:
            raise WiserepUnreachableError(f"{what} failed: {exc}") from exc
        finally:
            _request_deadline.reset(token)

    def _read_bounded_body(
        self,
        response: requests.Response,
        started: float,
        deadline_seconds: float,
        what: str,
    ) -> bytes:
        """Stream a response body, abandoning it past a deadline or byte cap.

        Args:
            response: The open streamed response.
            started: The monotonic time the request was issued.
            deadline_seconds: The absolute time the body is allowed to take.
            what: A phrase naming the attempt, used in failure messages.

        Returns:
            The body's bytes.

        Raises:
            WiserepUnreachableError: When the body passes the byte cap or the
                deadline. The response is abandoned rather than read to the
                end.
        """
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > self._max_bytes:
                raise WiserepUnreachableError(
                    f"{what} exceeded the {self._max_bytes}-byte response cap"
                )
            if time.monotonic() - started >= deadline_seconds:
                raise WiserepUnreachableError(
                    f"{what} was still arriving after the "
                    f"{deadline_seconds}-second deadline"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _respect_min_request_interval(self) -> None:
        """Space this request from the last one any worker issued.

        The last request's wall-clock time lives in the Django cache so that
        concurrent workers throttle together. The slim profiles run without
        Redis, so a cache that cannot be read or written leaves the interval
        unenforced and the lookup proceeds.
        """
        interval = self._settings.wiserep_min_request_interval_seconds
        if interval <= 0:
            return

        try:
            last = cache.get(_LAST_REQUEST_CACHE_KEY)
            if last is not None:
                wait = interval - (time.time() - float(last))
                if wait > 0:
                    time.sleep(wait)
            cache.set(
                _LAST_REQUEST_CACHE_KEY,
                time.time(),
                timeout=int(max(interval * 10, 60)),
            )
        except Exception as exc:  # noqa: BLE001 - the interval is best-effort
            logger.warning(
                "WISeREP request interval unenforced; cache unavailable: %s", exc
            )
