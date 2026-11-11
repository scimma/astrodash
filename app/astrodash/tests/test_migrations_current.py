"""Regression guard: every model change has a migration."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class MigrationsCurrentTests(TestCase):
    def test_models_have_no_unmigrated_changes(self) -> None:
        out = StringIO()
        try:
            call_command(
                "makemigrations", "--check", "--dry-run", stdout=out, stderr=out
            )
        except SystemExit:
            self.fail(
                "Models have changes with no migration; run makemigrations.\n"
                + out.getvalue()
            )
