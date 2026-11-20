"""Usage recording turns on only when its whole configuration is present.

The recording state is read from the ``ASTRODASH_USAGE_*`` variables. A partial
configuration turns recording off and names the variables that are missing,
never a configured value, because several of those values are credentials.
"""

import logging
import os
import tempfile
from unittest import mock

from django.apps import apps
from django.conf import settings as django_settings
from django.test import SimpleTestCase

from astrodash.core.usage.config import USAGE_LOGGER_NAME, recording_state

SECRET_PASSWORD = "pw-do-not-log-7f3a"
SECRET_USERNAME = "user-do-not-log-91c2"

FULL_ENV = {
    "ASTRODASH_USAGE_INFLUX_URL": "https://influx.example.org:8086",
    "ASTRODASH_USAGE_INFLUX_DATABASE": "astrodash_usage",
    "ASTRODASH_USAGE_INFLUX_USERNAME": SECRET_USERNAME,
    "ASTRODASH_USAGE_INFLUX_PASSWORD": SECRET_PASSWORD,
    "ASTRODASH_USAGE_ENVIRONMENT": "dev",
}

ALL_USAGE_VARS = list(FULL_ENV) + [
    "ASTRODASH_USAGE_INFLUX_RETENTION_POLICY",
    "ASTRODASH_USAGE_INFLUX_CERT_PATH",
    "ASTRODASH_USAGE_INFLUX_KEY_PATH",
]


def _env(**overrides):
    """Build a patched environment with every usage variable controlled.

    Variables absent from the result are removed from the process
    environment, so a value set by the container's env file cannot leak in.
    """
    env = {name: "" for name in ALL_USAGE_VARS}
    env.update(overrides)
    return env


class RecordingStateTests(SimpleTestCase):
    def _state(self, env):
        with mock.patch.dict(os.environ, env, clear=False):
            return recording_state()

    def test_all_required_variables_set_turns_recording_on(self):
        state = self._state(_env(**FULL_ENV))
        self.assertTrue(state.enabled)
        self.assertEqual(state.config.environment, "dev")
        self.assertEqual(state.config.database, "astrodash_usage")
        self.assertEqual(state.missing, ())

    def test_nothing_set_is_off_and_not_configured(self):
        state = self._state(_env())
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, "not configured")
        self.assertFalse(state.misconfigured)
        self.assertIsNone(state.config)

    def test_missing_password_is_off_and_named(self):
        env = _env(**FULL_ENV)
        env["ASTRODASH_USAGE_INFLUX_PASSWORD"] = ""
        state = self._state(env)
        self.assertFalse(state.enabled)
        self.assertTrue(state.misconfigured)
        self.assertEqual(state.missing, ("ASTRODASH_USAGE_INFLUX_PASSWORD",))
        self.assertNotIn(SECRET_USERNAME, state.reason)

    def test_missing_environment_tag_is_off(self):
        env = _env(**FULL_ENV)
        env["ASTRODASH_USAGE_ENVIRONMENT"] = ""
        state = self._state(env)
        self.assertFalse(state.enabled)
        self.assertIn("ASTRODASH_USAGE_ENVIRONMENT", state.missing)

    def test_whitespace_only_value_counts_as_unset(self):
        env = _env(**FULL_ENV)
        env["ASTRODASH_USAGE_INFLUX_DATABASE"] = "   "
        state = self._state(env)
        self.assertFalse(state.enabled)
        self.assertEqual(state.missing, ("ASTRODASH_USAGE_INFLUX_DATABASE",))

    def test_certificate_without_key_is_off(self):
        with tempfile.NamedTemporaryFile() as cert:
            state = self._state(
                _env(**FULL_ENV, ASTRODASH_USAGE_INFLUX_CERT_PATH=cert.name)
            )
        self.assertFalse(state.enabled)
        self.assertEqual(state.missing, ("ASTRODASH_USAGE_INFLUX_KEY_PATH",))

    def test_key_without_certificate_is_off(self):
        with tempfile.NamedTemporaryFile() as key:
            state = self._state(
                _env(**FULL_ENV, ASTRODASH_USAGE_INFLUX_KEY_PATH=key.name)
            )
        self.assertFalse(state.enabled)
        self.assertEqual(state.missing, ("ASTRODASH_USAGE_INFLUX_CERT_PATH",))

    def test_certificate_pair_present_is_on_and_carried(self):
        with tempfile.NamedTemporaryFile() as cert, tempfile.NamedTemporaryFile() as key:
            state = self._state(
                _env(
                    **FULL_ENV,
                    ASTRODASH_USAGE_INFLUX_CERT_PATH=cert.name,
                    ASTRODASH_USAGE_INFLUX_KEY_PATH=key.name,
                )
            )
            self.assertTrue(state.enabled)
            self.assertEqual(state.config.cert_pair, (cert.name, key.name))

    def test_configured_certificate_file_missing_is_off_and_named(self):
        with tempfile.NamedTemporaryFile() as key:
            state = self._state(
                _env(
                    **FULL_ENV,
                    ASTRODASH_USAGE_INFLUX_CERT_PATH="/nonexistent/usage-client.crt",
                    ASTRODASH_USAGE_INFLUX_KEY_PATH=key.name,
                )
            )
        self.assertFalse(state.enabled)
        self.assertEqual(state.missing, ("ASTRODASH_USAGE_INFLUX_CERT_PATH",))

    def test_no_certificate_configured_has_no_pair(self):
        state = self._state(_env(**FULL_ENV))
        self.assertIsNone(state.config.cert_pair)


class UsageLoggerConfigurationTests(SimpleTestCase):
    def test_django_logging_gives_usage_logger_info(self):
        """The configured level, not one a test raised for itself."""
        self.assertEqual(
            django_settings.LOGGING["loggers"][USAGE_LOGGER_NAME]["level"], "INFO"
        )
        self.assertEqual(
            logging.getLogger(USAGE_LOGGER_NAME).getEffectiveLevel(), logging.INFO
        )


class StartupLineTests(SimpleTestCase):
    def _ready_logs(self, env):
        config = apps.get_app_config("astrodash")
        with mock.patch.dict(os.environ, env, clear=False), self.assertLogs(
            USAGE_LOGGER_NAME, level="INFO"
        ) as logs:
            config.ready()
        return logs

    def test_ready_logs_recording_on(self):
        logs = self._ready_logs(_env(**FULL_ENV))
        line = "\n".join(logs.output)
        self.assertIn("Usage recording is on", line)
        self.assertIn("environment=dev", line)
        self.assertIn("database=astrodash_usage", line)
        self.assertNotIn(SECRET_PASSWORD, line)
        self.assertNotIn(SECRET_USERNAME, line)

    def test_ready_logs_recording_off_when_not_configured(self):
        logs = self._ready_logs(_env())
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelno, logging.INFO)
        self.assertIn("Usage recording is off: not configured", logs.output[0])

    def test_ready_warns_with_missing_names_and_no_values(self):
        env = _env(**FULL_ENV)
        env["ASTRODASH_USAGE_INFLUX_PASSWORD"] = ""
        logs = self._ready_logs(env)
        self.assertEqual(logs.records[0].levelno, logging.WARNING)
        self.assertIn("ASTRODASH_USAGE_INFLUX_PASSWORD", logs.output[0])
        self.assertNotIn(SECRET_USERNAME, logs.output[0])
        self.assertNotIn("influx.example.org", logs.output[0])
