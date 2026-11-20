"""Whether usage recording is on, read from the ``ASTRODASH_USAGE_*`` settings.

Recording is on only when the InfluxDB URL, database, username, password and
environment tag are all set. A client certificate is optional, but a
certificate path without a key path (or the reverse), or a configured path
whose file is absent, turns recording off. Anything partial turns recording
off and names the variables to fix. A configured value is never put into a
reason or a log line, because the username and password are credentials and
the URL and paths are not worth the risk of a mistake.
"""

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from astrodash.config.settings import Settings, get_settings
from astrodash.infrastructure.usage import USAGE_LOGGER_NAME  # noqa: F401

URL_VAR = "ASTRODASH_USAGE_INFLUX_URL"
DATABASE_VAR = "ASTRODASH_USAGE_INFLUX_DATABASE"
USERNAME_VAR = "ASTRODASH_USAGE_INFLUX_USERNAME"
PASSWORD_VAR = "ASTRODASH_USAGE_INFLUX_PASSWORD"
ENVIRONMENT_VAR = "ASTRODASH_USAGE_ENVIRONMENT"
CERT_PATH_VAR = "ASTRODASH_USAGE_INFLUX_CERT_PATH"
KEY_PATH_VAR = "ASTRODASH_USAGE_INFLUX_KEY_PATH"

NOT_CONFIGURED = "not configured"


@dataclass(frozen=True)
class UsageConfig:
    """Everything the writer needs once recording is on."""

    url: str
    database: str
    username: str
    password: str
    environment: str
    retention_policy: Optional[str]
    cert_pair: Optional[Tuple[str, str]]
    connect_timeout: float
    read_timeout: float
    pause_seconds: float
    visitor_id_cap_per_hour: int

    def __repr__(self) -> str:
        # The dataclass default would print the username and password.
        return (
            f"UsageConfig(environment={self.environment!r}, "
            f"database={self.database!r})"
        )


@dataclass(frozen=True)
class RecordingState:
    """Whether recording is on, and if not, why.

    Attributes:
        enabled: True when every required value is present.
        reason: A short reason recording is off, naming variables only.
        missing: The names of the variables that are absent or unusable.
        config: The writer configuration when enabled, else ``None``.
    """

    enabled: bool
    reason: str
    missing: Tuple[str, ...]
    config: Optional[UsageConfig]

    @property
    def misconfigured(self) -> bool:
        """True when some usage variables are set but recording is still off."""
        return not self.enabled and self.reason != NOT_CONFIGURED


def _clean(value: Optional[str]) -> Optional[str]:
    """Treat an unset, empty or whitespace-only value as unset."""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def recording_state(settings: Optional[Settings] = None) -> RecordingState:
    """Read the usage settings and decide whether recording is on.

    Args:
        settings: Settings to read; a fresh ``Settings`` when omitted.

    Returns:
        The recording state. It never carries a configured value in its
        reason, only variable names.
    """
    settings = settings or get_settings()
    required = {
        URL_VAR: _clean(settings.usage_influx_url),
        DATABASE_VAR: _clean(settings.usage_influx_database),
        USERNAME_VAR: _clean(settings.usage_influx_username),
        PASSWORD_VAR: _clean(settings.usage_influx_password),
        ENVIRONMENT_VAR: _clean(settings.usage_environment),
    }
    cert_path = _clean(settings.usage_influx_cert_path)
    key_path = _clean(settings.usage_influx_key_path)

    if not any(required.values()) and not cert_path and not key_path:
        return RecordingState(False, NOT_CONFIGURED, (), None)

    missing = [name for name, value in required.items() if value is None]
    if cert_path and not key_path:
        missing.append(KEY_PATH_VAR)
    elif key_path and not cert_path:
        missing.append(CERT_PATH_VAR)
    else:
        for name, path in ((CERT_PATH_VAR, cert_path), (KEY_PATH_VAR, key_path)):
            if path and not os.path.isfile(path):
                missing.append(name)

    if missing:
        reason = "missing or unusable " + ", ".join(missing)
        return RecordingState(False, reason, tuple(missing), None)

    config = UsageConfig(
        url=required[URL_VAR].rstrip("/"),
        database=required[DATABASE_VAR],
        username=required[USERNAME_VAR],
        password=required[PASSWORD_VAR],
        environment=required[ENVIRONMENT_VAR],
        retention_policy=_clean(settings.usage_influx_retention_policy),
        cert_pair=(cert_path, key_path) if cert_path else None,
        connect_timeout=settings.usage_connect_timeout,
        read_timeout=settings.usage_read_timeout,
        pause_seconds=settings.usage_pause_seconds,
        visitor_id_cap_per_hour=settings.usage_visitor_id_cap_per_hour,
    )
    return RecordingState(True, "", (), config)


def describe(state: RecordingState) -> str:
    """Render the one startup line for a recording state.

    Args:
        state: The state to describe.

    Returns:
        A line naming the environment and database when on, or the reason
        when off. It carries no credential.
    """
    if state.enabled:
        return (
            f"Usage recording is on: environment={state.config.environment} "
            f"database={state.config.database}"
        )
    return f"Usage recording is off: {state.reason}"
