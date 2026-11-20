"""Write usage events to InfluxDB over HTTPS, best effort.

The instance speaks the 1.x HTTP interface: ``POST /write`` with the database,
an optional retention policy and the timestamp precision as query parameters,
basic auth, and, from outside SCiMMA's network, a client certificate the proxy
requires.

A write never raises into its caller and is never retried. Each one is bounded
by a connect and a read timeout. A connection error, a timeout, an
authentication failure or a server error starts a pause during which writes
are skipped without a network call, so a slow or absent InfluxDB costs a
worker at most one bounded write per pause. A 400 means InfluxDB rejected the
payload itself; that is logged and starts no pause, because the next payload
may be fine.

Log lines give a count of events and the kind of failure, never the response
body, the event content or a credential. Certificate verification is left at
the library default; nothing here disables it.
"""

import logging
import time
from typing import Callable, Optional, Sequence, Tuple

import requests

from astrodash.infrastructure.usage import USAGE_LOGGER_NAME

logger = logging.getLogger(USAGE_LOGGER_NAME)

WRITE_PATH = "/write"


class InfluxClient:
    """A best-effort writer for one InfluxDB database."""

    def __init__(
        self,
        *,
        url: str,
        database: str,
        username: str,
        password: str,
        retention_policy: Optional[str],
        cert_pair: Optional[Tuple[str, str]],
        connect_timeout: float,
        read_timeout: float,
        pause_seconds: float,
        session: Optional[requests.Session] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build the client.

        Args:
            url: Base URL of the InfluxDB HTTP interface, without a trailing
                slash.
            database: Database to write to.
            username: Basic-auth username.
            password: Basic-auth password.
            retention_policy: Retention policy to write under, or ``None`` for
                the database default.
            cert_pair: Client certificate and key paths, or ``None``.
            connect_timeout: Seconds allowed to connect.
            read_timeout: Seconds allowed between bytes of the response.
            pause_seconds: Seconds to skip writes after a failure.
            session: HTTP session; a new ``requests.Session`` when omitted.
            clock: Monotonic clock, injectable for tests.
        """
        self._url = url
        self._database = database
        self._auth = (username, password)
        self._retention_policy = retention_policy
        self._cert_pair = cert_pair
        self._timeout = (connect_timeout, read_timeout)
        self._pause_seconds = pause_seconds
        self._session = session or requests.Session()
        self._clock = clock
        self._paused_until = 0.0

    def _pause(self) -> None:
        self._paused_until = self._clock() + self._pause_seconds

    def write(self, lines: Sequence[str]) -> bool:
        """Send every line in one request.

        Args:
            lines: Line-protocol lines, one per event.

        Returns:
            True when InfluxDB accepted the write or there was nothing to
            write; False when the events were dropped, for any reason.
        """
        count = len(lines)
        if not count:
            return True
        if self._clock() < self._paused_until:
            logger.warning(
                "Dropped %d usage events: writes are paused after a failure", count
            )
            return False

        params = {"db": self._database, "precision": "ns"}
        if self._retention_policy:
            params["rp"] = self._retention_policy
        try:
            response = self._session.post(
                self._url + WRITE_PATH,
                params=params,
                data="\n".join(lines).encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
                auth=self._auth,
                cert=self._cert_pair,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._pause()
            logger.warning(
                "Dropped %d usage events: InfluxDB unreachable (%s)",
                count,
                type(exc).__name__,
            )
            return False

        status = response.status_code
        if 200 <= status < 300:
            return True
        if status == 400:
            logger.warning(
                "InfluxDB rejected %d usage events (HTTP 400); not pausing", count
            )
            return False
        self._pause()
        logger.warning(
            "Dropped %d usage events: InfluxDB answered HTTP %d", count, status
        )
        return False
