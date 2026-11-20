import logging

from django.apps import AppConfig
from django.conf import settings


def _announce_usage_recording(app_settings) -> None:
    """Log one line saying whether usage recording is on, and why not if off.

    A partial configuration is a warning naming the variables to fix; an
    entirely unset configuration, as in local development and CI, is
    informational. Neither line carries a configured value.

    Args:
        app_settings: The loaded ``astrodash.config.settings.Settings``.
    """
    from astrodash.core.usage.config import (
        USAGE_LOGGER_NAME,
        describe,
        recording_state,
    )

    state = recording_state(app_settings)
    level = logging.WARNING if state.misconfigured else logging.INFO
    logging.getLogger(USAGE_LOGGER_NAME).log(level, describe(state))


class AstroDashConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrodash"
    verbose_name = "AstroDash Integration"

    def ready(self) -> None:
        """Announce the active ``APP_VERSION`` and fail closed on a misconfigured gate.

        One INFO line at gunicorn boot so operators can confirm what's
        running from pod logs without exec'ing into the container.
        Django may call ``ready()`` more than once in some test setups;
        a duplicated log line is harmless.

        The gate-configuration check runs here rather than at registry import
        because ``ready()`` runs for the server *and* every management command:
        a deployment whose registry contains a gated model but lacks the gate's
        configuration fails in the init container, rather than starting and
        serving that model without a credential prompt.

        The declared-surface check runs here for the same reason: the registry's
        import-time validation cannot see the web layer's surface map without
        inverting the layer boundary, and without a runtime caller a surface id
        with no presentation would surface as a broken tab rather than a
        refused startup.

        The settings check runs here for the same fail-in-the-init-container
        reason. Now that the ``ASTRODASH_*`` variables actually bind, a typo'd
        configMap value raises ``ValidationError`` the first time something
        builds ``Settings`` -- which, without this, is the first user request,
        on a pod that already reported Ready. Constructing it once at startup
        turns that into a failed rollout.

        It also logs whether usage recording is on. That check opens no
        connection, so it is safe in init containers and management commands.

        Raises:
            ValueError: If a model in the registry requires a credential while
                any part of the gate configuration is unset, blank, or left at
                a committed default, or if a model declares an empty, unknown,
                or classification-less result surface list.
            pydantic.ValidationError: If any ``ASTRODASH_*`` variable holds a
                value its field rejects.
        """
        from astrodash.config.logging import get_logger
        from astrodash.config.settings import get_settings
        from astrodash.core.gate_config import gate_configuration
        from astrodash.infrastructure.ml import model_registry
        from astrodash.surfaces import known_surface_ids

        get_logger(__name__).info(
            "AstroDash starting with APP_VERSION=%s", settings.APP_VERSION
        )

        app_settings = get_settings()
        _announce_usage_recording(app_settings)

        model_registry.validate_gate_configuration(
            model_registry.MODELS, gate_configuration()
        )
        model_registry.validate_surfaces(model_registry.MODELS, known_surface_ids())
