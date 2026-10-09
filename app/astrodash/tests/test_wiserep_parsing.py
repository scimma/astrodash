"""Tests for the shared WISeREP export parsing.

``astrodash.infrastructure.wiserep.parsing`` is read by two callers: the
request path and the offline scraper under ``app/wiserep_scrape/``. What is
covered here is the part both depend on -- which member of the zip export is
read, under which delimiter, how far it is allowed to expand, and that a
column is found by name rather than by position.

Nothing here performs a request.
"""

import io
import sys
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from astrodash.infrastructure.wiserep.parsing import (
    extract_csv_text_from_zip,
    find_column,
    parse_csv_rows,
)

APP_DIR = Path(__file__).resolve().parents[2]
if str(APP_DIR) not in sys.path:  # pragma: no cover - import shim
    sys.path.insert(0, str(APP_DIR))

from wiserep_scrape import wiserep_monthly_scrape  # noqa: E402

CSV_TEXT = (
    '"Obj. ID","IAU name","Spec. ID","Ascii file"\n'
    '"1","SN 2002er","91460","https://www.wiserep.org/sites/default/files/a.ascii"\n'
)

TSV_TEXT = (
    "Obj. ID\tIAU name\tSpec. ID\tAscii file\n"
    "1\tSN 2002er\t91460\thttps://www.wiserep.org/sites/default/files/a.ascii\n"
)

# A header in the shape of WISeREP's ``format=tsv`` export: 46 columns wide.
# The seven columns the application resolves carry their exact upstream
# spellings as recorded in the plan's Sources; the others stand in for the rest
# of the export's width, which is the reason a column is matched by name and
# never by position.
WISEREP_TSV_HEADER = [
    "Obj. ID",
    "IAU name",
    "Internal name",
    "RA",
    "DEC",
    "Obj. Type",
    "Redshift",
    "Host name",
    "Host redshift",
    "Reporting group/s",
    "Discovery data source/s",
    "Discovery date (UT)",
    "Discovery mag",
    "Spec. ID",
    "Ascii file",
    "FITS file",
    "Obs-date",
    "JD",
    "Instrument",
    "Telescope",
    "Observer",
    "Reducer",
    "Spec. type",
    "Spec. quality",
    "Spec. class",
    "Phase (days)",
    "Phase reference",
    "Public",
    "End prop. period",
    "Creation date",
    "Last modified",
    "Contrib",
    "Groups owning",
    "Related files",
    "Exp-time",
    "Airmass",
    "Slit",
    "Aperture",
    "Dichroic",
    "Grism",
    "Grating",
    "Blaze",
    "Assoc. groups",
    "Spec. remarks",
    "Obj. remarks",
    "WISeREP link",
]


def zip_payload(members: dict[str, str]) -> bytes:
    """Build a zip export carrying the given members.

    Args:
        members: Member name mapped to its text content.

    Returns:
        The zip archive as bytes, as WISeREP serves it.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buf.getvalue()


class _CountingReader:
    """A zip member reader that records how much it has decompressed."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        self.total += len(chunk)
        return chunk

    def __enter__(self) -> "_CountingReader":
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info) -> bool:
        return self._inner.__exit__(*exc_info)


class ZipMemberSelectionTests(SimpleTestCase):
    """WISeREP serves one delimited member, and its extension varies."""

    def test_tsv_member_is_extracted_and_parsed_with_tabs(self):
        payload = zip_payload({"wiserep_spectra.tsv": TSV_TEXT})
        rows = parse_csv_rows(
            extract_csv_text_from_zip(payload, delimiter="\t"), delimiter="\t"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["IAU name"], "SN 2002er")
        self.assertEqual(rows[0]["Spec. ID"], "91460")

    def test_csv_member_parses_as_before(self):
        payload = zip_payload({"wiserep_spectra.csv": CSV_TEXT})
        rows = parse_csv_rows(extract_csv_text_from_zip(payload))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["IAU name"], "SN 2002er")

    def test_both_members_present_prefers_the_requested_delimiter(self):
        payload = zip_payload(
            {"wiserep_spectra.csv": CSV_TEXT, "wiserep_spectra.tsv": TSV_TEXT}
        )
        self.assertEqual(
            extract_csv_text_from_zip(payload, delimiter="\t").splitlines()[0],
            TSV_TEXT.splitlines()[0],
        )
        self.assertEqual(
            extract_csv_text_from_zip(payload, delimiter=",").splitlines()[0],
            CSV_TEXT.splitlines()[0],
        )

    def test_no_delimited_member_names_what_was_found(self):
        payload = zip_payload({"readme.txt": "nothing here", "notes.pdf": "x"})
        with self.assertRaises(RuntimeError) as ctx:
            extract_csv_text_from_zip(payload, delimiter="\t")
        message = str(ctx.exception)
        self.assertIn("readme.txt", message)
        self.assertIn("notes.pdf", message)


class DelimiterTests(SimpleTestCase):
    """A tab-delimited export carries commas inside its fields."""

    def test_comma_in_a_tab_delimited_field_is_not_a_field_break(self):
        text = (
            "IAU name\tSpec. remarks\tSpec. ID\n"
            "SN 2002er\tTypical Ia, near maximum\t91460\n"
        )
        rows = parse_csv_rows(text, delimiter="\t")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Spec. remarks"], "Typical Ia, near maximum")
        self.assertEqual(rows[0]["Spec. ID"], "91460")


class ColumnResolutionTests(SimpleTestCase):
    """Every column the application reads is found by name in the real header."""

    def test_header_is_the_full_export_width(self):
        self.assertEqual(len(WISEREP_TSV_HEADER), 46)

    def test_every_depended_on_column_resolves(self):
        cases = {
            "Spec. ID": (("Spec. ID", "Spec ID"), ("spec", "id")),
            "Obs-date": (("Obs-date", "Obs. date", "Obs Date"), ("obs", "date")),
            "JD": (("JD",), ()),
            "Ascii file": (("Ascii file", "ASCII file"), ("ascii",)),
            "Spec. type": (("Spec. type", "Spec Type"), ("spec", "type")),
            "Spec. quality": (("Spec. quality", "Spec Quality"), ("spec", "quality")),
            "Public": (("Public",), ()),
        }
        for expected, (preferred, contains) in cases.items():
            with self.subTest(column=expected):
                self.assertEqual(
                    find_column(WISEREP_TSV_HEADER, preferred, contains),
                    expected,
                )


class ExpansionLimitTests(SimpleTestCase):
    """A small zip can carry a very large member."""

    def test_member_past_the_limit_is_refused_before_full_decompression(self):
        body = "a" * (4 * 1024 * 1024)
        payload = zip_payload({"wiserep_spectra.csv": body})
        self.assertLess(len(payload), 64 * 1024)

        readers: list[_CountingReader] = []
        real_open = zipfile.ZipFile.open

        def tracking_open(archive, name, *args, **kwargs):
            reader = _CountingReader(real_open(archive, name, *args, **kwargs))
            readers.append(reader)
            return reader

        with mock.patch.object(zipfile.ZipFile, "open", tracking_open):
            with self.assertRaises(RuntimeError) as ctx:
                extract_csv_text_from_zip(payload, max_uncompressed_bytes=1024)

        self.assertIn("wiserep_spectra.csv", str(ctx.exception))
        self.assertEqual(len(readers), 1)
        self.assertLess(readers[0].total, len(body))

    def test_member_within_the_limit_is_read(self):
        payload = zip_payload({"wiserep_spectra.csv": CSV_TEXT})
        self.assertEqual(
            extract_csv_text_from_zip(payload, max_uncompressed_bytes=1024), CSV_TEXT
        )


class ScraperReExportTests(SimpleTestCase):
    """The offline scraper reads this module rather than its own copy."""

    def test_scraper_names_are_the_shared_implementations(self):
        from astrodash.infrastructure.wiserep import parsing

        for name in (
            "USER_AGENT",
            "compact",
            "normalize_col",
            "find_column",
            "parse_csv_rows",
            "extract_csv_text_from_zip",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(wiserep_monthly_scrape, name), getattr(parsing, name)
                )
