"""Tests for the offline WISeREP monthly scrape.

The scraper is operator tooling, not part of the request path, but it is the
only producer of the monthly challenge dataset the leaderboard scores against,
so a silent change in its output is a silent change in published standings.

These live here rather than beside the script because CI runs
``manage.py test astrodash.tests users.tests`` (see
``app/entrypoints/docker-entrypoint.app.sh``). A pytest-style module under
``app/wiserep_scrape/`` is collected by nothing and executed by nothing --
pytest is not installed in the image -- so it reads as coverage while
guarding nothing.

Covered here: the WISeREP export shapes the scraper parses (zip-of-CSV, bare
CSV, HTML table), residual-0 duplicate identity, and the filename stability
that makes a re-run idempotent. Network behaviour is not covered; nothing
here performs a request.
"""

import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

APP_DIR = Path(__file__).resolve().parents[2]
if str(APP_DIR) not in sys.path:  # pragma: no cover - import shim
    sys.path.insert(0, str(APP_DIR))

from wiserep_scrape.wiserep_monthly_scrape import (  # noqa: E402
    USER_AGENT,
    write_snapshot,
    extract_csv_text_from_zip,
    find_column,
    identify_search_columns,
    is_zip_payload,
    make_session,
    parse_csv_rows,
    parse_html_table_rows,
    spectra_residual,
    spectrum_identity_key,
    stable_output_filename,
)

CSV_TEXT = (
    '"Obj. ID","IAU name","Spec. ID","Creation date"\n'
    '"1","SN 2026mza","91460","2026-05-31 22:30:05"\n'
)


class ExportShapeTests(SimpleTestCase):
    """WISeREP serves the same search as a zip, a bare CSV, or HTML."""

    def _zip_payload(self, text=CSV_TEXT, name="wiserep_spectra.csv"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr(name, text)
        return buf.getvalue()

    def test_zip_csv_export_exposes_creation_date(self):
        payload = self._zip_payload()
        self.assertTrue(is_zip_payload(payload, "application/zip"))
        rows = parse_csv_rows(extract_csv_text_from_zip(payload))
        columns = identify_search_columns(rows)
        self.assertEqual(columns["creation"], "Creation date")
        self.assertEqual(columns["iau"], "IAU name")
        self.assertEqual(columns["spec_id"], "Spec. ID")

    def test_bare_csv_is_not_mistaken_for_a_zip(self):
        self.assertFalse(is_zip_payload(CSV_TEXT.encode("utf-8"), "text/csv"))

    def test_column_lookup_tolerates_wiserep_spellings(self):
        """WISeREP has shipped several spellings of the creation column."""
        for spelling in ("Creation Date (UT)", "Creation Date", "Creation date"):
            with self.subTest(spelling=spelling):
                rows = parse_csv_rows(f'"IAU name","{spelling}"\n"SN 1","2026-05-01"\n')
                self.assertEqual(
                    find_column(
                        rows[0].keys(),
                        ("Creation Date (UT)", "Creation Date", "Creation date"),
                        ("creation", "date"),
                    ),
                    spelling,
                )

    def test_html_table_export_is_parsed(self):
        html = """
        <table>
          <tr><th>IAU name</th><th>Spec. ID</th><th>Creation Date (UT)</th></tr>
          <tr><td>SN 2026mza</td><td>91460</td><td>2026-05-31 22:30:05</td></tr>
        </table>
        """
        rows = parse_html_table_rows(html)
        self.assertEqual(len(rows), 1)
        columns = identify_search_columns(rows)
        self.assertEqual(columns["iau"], "IAU name")
        self.assertEqual(columns["spec_id"], "Spec. ID")


class DuplicateIdentityTests(SimpleTestCase):
    """Residual-0 duplicates: the same flux uploaded more than once."""

    WAVE = [4000.0, 4001.0, 4002.0]
    FLUX = [1.0, 2.0, 3.0]

    def test_identical_spectra_have_zero_residual_and_one_key(self):
        self.assertEqual(
            spectra_residual(self.WAVE, self.FLUX, list(self.WAVE), list(self.FLUX)),
            0.0,
        )
        self.assertEqual(
            spectrum_identity_key(self.WAVE, self.FLUX),
            spectrum_identity_key(list(self.WAVE), list(self.FLUX)),
        )

    def test_differing_flux_is_not_a_duplicate(self):
        other = [1.0, 2.0, 3.5]
        self.assertGreater(spectra_residual(self.WAVE, self.FLUX, self.WAVE, other), 0)
        self.assertNotEqual(
            spectrum_identity_key(self.WAVE, self.FLUX),
            spectrum_identity_key(self.WAVE, other),
        )

    def test_different_wavelength_grid_is_never_a_duplicate(self):
        """Same flux on a different grid is a different observation."""
        shifted = [5000.0, 5001.0, 5002.0]
        self.assertEqual(
            spectra_residual(self.WAVE, self.FLUX, shifted, self.FLUX), float("inf")
        )
        self.assertNotEqual(
            spectrum_identity_key(self.WAVE, self.FLUX),
            spectrum_identity_key(shifted, self.FLUX),
        )

    def test_length_mismatch_is_never_a_duplicate(self):
        self.assertEqual(
            spectra_residual(self.WAVE, self.FLUX, self.WAVE[:2], self.FLUX[:2]),
            float("inf"),
        )


class IdempotencyTests(SimpleTestCase):
    """A re-run must land on the same filenames or it re-downloads everything."""

    def test_same_inputs_give_the_same_filename(self):
        args = ("SN 2026mza", "91460", "https://example.invalid/a", "spec.ascii")
        self.assertEqual(stable_output_filename(*args), stable_output_filename(*args))

    def test_filename_carries_iau_and_spec_id(self):
        name = stable_output_filename(
            "SN 2026mza", "91460", "https://example.invalid/a", "spec.ascii"
        )
        self.assertIn("2026mza", name)
        self.assertIn("91460", name)

    def test_distinct_spectra_of_one_object_do_not_collide(self):
        first = stable_output_filename(
            "SN 2026mza", "91460", "https://example.invalid/a", "spec.ascii"
        )
        second = stable_output_filename(
            "SN 2026mza", "91461", "https://example.invalid/b", "spec.ascii"
        )
        self.assertNotEqual(first, second)

    def test_missing_spec_id_falls_back_to_a_stable_url_hash(self):
        args = ("SN 2026mza", "", "https://example.invalid/a", "spec.ascii")
        self.assertEqual(stable_output_filename(*args), stable_output_filename(*args))
        self.assertNotEqual(
            stable_output_filename(*args),
            stable_output_filename(
                "SN 2026mza", "", "https://example.invalid/b", "spec.ascii"
            ),
        )


class ClientIdentityTests(SimpleTestCase):
    """The agent must not claim a TNS registration it does not hold."""

    def test_default_agent_carries_no_tns_marker(self):
        self.assertNotIn("tns_marker", USER_AGENT)
        self.assertNotIn("tns_id", USER_AGENT)
        self.assertIn("AstroDASH", USER_AGENT)

    def test_session_sends_the_default_agent(self):
        self.assertEqual(make_session().headers["User-Agent"], USER_AGENT)

    def test_agent_is_overridable_for_a_registered_bot_id(self):
        custom = 'tns_marker{"tns_id":1234,"type":"bot","name":"AstroDASH"}'
        self.assertEqual(make_session(custom).headers["User-Agent"], custom)


class SnapshotRecordTests(SimpleTestCase):
    """A dataset records when it was collected.

    A WISeREP window keeps gaining spectra after the month closes, so a month's
    contents are only meaningful with a collection date attached. Without it,
    standings scored from the dataset cannot be reproduced by anyone.
    """

    def _write(self, tmp, metadata):
        return write_snapshot(
            Path(tmp),
            start=date(2026, 7, 1),
            end=date(2026, 7, 31),
            metadata=metadata,
        )

    def test_snapshot_records_window_and_counts(self):
        # The scraper carries metadata as {filename: row}, not a list of rows.
        # An earlier version of this test passed a list, so it agreed with the
        # helper's wrong assumption instead of the caller's real shape.
        rows = {
            "a.dat": {"iau": "2026aaa", "filename": "a.dat"},
            "b.dat": {"iau": "2026aaa", "filename": "b.dat"},
            "c.dat": {"iau": "2026bbb", "filename": "c.dat"},
        }
        with TemporaryDirectory() as tmp:
            payload = json.loads(self._write(tmp, rows).read_text())
        self.assertEqual(payload["window_start"], "2026-07-01")
        self.assertEqual(payload["window_end"], "2026-07-31")
        self.assertEqual(payload["spectra"], 3)
        self.assertEqual(payload["objects"], 2)
        self.assertTrue(payload["scraped_at"].startswith("20"))

    def test_blank_object_names_are_not_counted(self):
        rows = {
            "a.dat": {"iau": "", "filename": "a.dat"},
            "b.dat": {"iau": "2026aaa", "filename": "b.dat"},
        }
        with TemporaryDirectory() as tmp:
            payload = json.loads(self._write(tmp, rows).read_text())
        self.assertEqual(payload["objects"], 1)

    def test_snapshot_lands_beside_the_metadata(self):
        with TemporaryDirectory() as tmp:
            path = self._write(tmp, {})
            self.assertEqual(path.name, "snapshot.json")
            self.assertEqual(path.parent, Path(tmp))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
