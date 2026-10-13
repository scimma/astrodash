"""Non-database dispatches reach their work and come back with its result.

The ten dispatches that declare themselves non-database are transparent call
shapes, so what matters is that each one still hands the right callable the
right arguments and returns its value unchanged. Nothing covered them before:
no test drives a classifier's async wrapper or a template-analysis method, so
these call sites had no behavioural coverage at all.

The template-analysis paths are coverable here because the service takes its
handler by injection. The five classifier dispatches are not: they need a
loaded TorchScript model from the external data mount, which the suite has no
access to, so they stay import-verified and lean on the posture guard.

``SimpleTestCase`` is enough -- the point of these paths is that they reach no
database, and the seam's own check confirms it.
"""

from typing import Any, Dict, List, Tuple

from asgiref.sync import async_to_sync
from django.db import connections
from django.test import SimpleTestCase

from astrodash.domain.services.template_analysis_service import (
    TemplateAnalysisService,
)


class _RecordingTemplateHandler:
    """A template handler that records how the service called it."""

    def __init__(self, templates: Dict[str, Any]) -> None:
        """Store the templates to serve and prepare the call log.

        Args:
            templates: The mapping ``get_all_templates`` should return.
        """
        self._templates = templates
        self.validate_calls: List[Tuple[str, str]] = []
        self.get_all_calls = 0

    def get_all_templates(self) -> Dict[str, Any]:
        """Return the canned templates and count the call.

        Returns:
            The mapping this handler was built with.
        """
        self.get_all_calls += 1
        return self._templates

    def validate_template(self, sn_type: str, age_bin: str) -> bool:
        """Record the arguments and accept anything in the canned templates.

        Args:
            sn_type: The supernova type the service asked about.
            age_bin: The age bin the service asked about.

        Returns:
            Whether the pair appears in the canned templates.
        """
        self.validate_calls.append((sn_type, age_bin))
        return age_bin in self._templates.get(sn_type, {})


class TemplateAnalysisDispatchTests(SimpleTestCase):
    """The template-analysis dispatches carry their work and results intact."""

    def setUp(self) -> None:
        """Build a service over a recording handler."""
        self.handler = _RecordingTemplateHandler(
            {"Ia-norm": {"-2 to 2": object()}, "II-P": {"4 to 8": object()}}
        )
        self.service = TemplateAnalysisService(self.handler)

    def test_analysis_options_come_back_through_the_dispatch(self) -> None:
        """The declared dispatch reaches the handler and returns its data."""
        options = async_to_sync(self.service.get_analysis_options)()

        self.assertEqual(self.handler.get_all_calls, 1)
        self.assertCountEqual(options["sn_types"], ["Ia-norm", "II-P"])

    def test_template_statistics_come_back_through_the_dispatch(self) -> None:
        """Statistics are computed from what the dispatch returned."""
        statistics = async_to_sync(self.service.get_template_statistics)()

        self.assertEqual(statistics["total_sn_types"], 2)
        self.assertEqual(statistics["total_age_bins"], 2)

    def test_validation_forwards_both_arguments(self) -> None:
        """Both arguments survive the dispatch in the right order."""
        valid = async_to_sync(self.service.validate_template_request)(
            "Ia-norm", "-2 to 2"
        )
        invalid = async_to_sync(self.service.validate_template_request)(
            "Ia-norm", "40 to 50"
        )

        self.assertTrue(valid)
        self.assertFalse(invalid)
        self.assertEqual(
            self.handler.validate_calls,
            [("Ia-norm", "-2 to 2"), ("Ia-norm", "40 to 50")],
        )

    def test_the_declaration_is_honest(self) -> None:
        """These paths open no connection, which is what they declare.

        The seam closes and logs a connection opened by work declared
        non-database, so a quiet run is the evidence that the declaration on
        these call sites is true.
        """
        with self.assertNoLogs("astrodash.infrastructure.db_threads", "ERROR"):
            async_to_sync(self.service.get_analysis_options)()

        self.assertIsNone(connections["default"].connection)
