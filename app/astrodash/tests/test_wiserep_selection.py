"""Tests for WISeREP eligibility and ranking.

The rows here are written inline rather than read from a captured export, so
these tests stay independent of the fixture files another unit owns. Their
shape, spellings and values are taken from real captured exports for
``SN 2002er`` (35 rows, one object), the ``name=2002`` search (50 rows, ten
distinct objects) and ``SN 2023ixf``.

Nothing here performs a request.
"""

from django.test import SimpleTestCase

from astrodash.infrastructure.wiserep.selection import (
    RefusalReason,
    normalize_designation,
    rank_candidates,
)

# Every column the selection reads, spelled as WISeREP spells it.
COLUMNS = (
    "Obj. ID",
    "IAU name",
    "Internal name/s",
    "Obj. Type",
    "Redshift",
    "Spec. ID",
    "Obs-date",
    "JD",
    "Telescope",
    "Instrument",
    "Public",
    "Ascii file",
    "Spec. type",
    "Spec. quality",
)


def row(**overrides: str) -> dict[str, str]:
    """Build one parsed export row.

    Args:
        **overrides: Column values to set, keyed by WISeREP's column names.

    Returns:
        A row carrying every column the selection reads, defaulted to a
        public ``Object`` spectrum of ``SN 2002er``.
    """
    values = {
        "Obj. ID": "1052",
        "IAU name": "SN 2002er",
        "Internal name/s": "",
        "Obj. Type": "SN Ia",
        "Redshift": "0.009063",
        "Spec. ID": "15319",
        "Obs-date": "2002-08-26 00:00:00.00",
        "JD": "2452512.5",
        "Telescope": "INT-2.5m",
        "Instrument": "IDS",
        "Public": "Y",
        "Ascii file": "https://www.wiserep.org/system/files/uploaded/a.dat",
        "Spec. type": "Object",
        "Spec. quality": "",
    }
    values.update(overrides)
    return {column: values[column] for column in COLUMNS}


def epoch_rows(count: int) -> list[dict[str, str]]:
    """Build consecutive nightly epochs of one object, latest row first.

    Args:
        count: How many epochs to build.

    Returns:
        Rows whose Julian dates descend, so a ranking that returned the input
        order would fail.
    """
    return [
        row(
            **{
                "Spec. ID": str(15319 + index),
                "JD": str(2452512.5 + index),
                "Obs-date": f"2002-08-{26 + index:02d} 00:00:00.00",
            }
        )
        for index in reversed(range(count))
    ]


class NormalizeDesignationTests(SimpleTestCase):
    """The key submitted names and WISeREP designations compare under."""

    def test_prefix_and_spacing_collapse(self) -> None:
        for name in ("2002er", "SN2002er", "AT2002er", "SN 2002er", "sn 2002ER"):
            with self.subTest(name=name):
                self.assertEqual(normalize_designation(name), "2002er")

    def test_survey_designation_keeps_its_letters(self) -> None:
        self.assertEqual(normalize_designation("ATLAS26hqe"), "atlas26hqe")


class RankingTests(SimpleTestCase):
    """Ranking one resolved object's eligible spectra (R6)."""

    def test_thirty_five_eligible_rows_rank_to_one_selection(self) -> None:
        """Covers AE1."""
        result = rank_candidates(epoch_rows(35), "2002er")

        self.assertIsNone(result.refusal)
        self.assertEqual(len(result.candidates), 35)
        self.assertIsNotNone(result.selected)
        self.assertEqual(result.selected.observation_date, "2002-08-26 00:00:00.00")
        self.assertEqual(result.selected.spectrum_id, "15319")
        self.assertEqual(result.selected.telescope, "INT-2.5m")
        self.assertEqual(result.selected.instrument, "IDS")
        self.assertEqual(result.selected.redshift, 0.009063)
        self.assertEqual(
            result.selected.ascii_url,
            "https://www.wiserep.org/system/files/uploaded/a.dat",
        )

    def test_quality_breaks_a_julian_date_tie(self) -> None:
        rows = [
            row(**{"Spec. ID": "90001", "Spec. quality": "Medium"}),
            row(**{"Spec. ID": "90002", "Spec. quality": "High"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual(
            [candidate.quality for candidate in result.candidates], ["High", "Medium"]
        )
        self.assertEqual(result.selected.spectrum_id, "90002")

    def test_identifier_orders_a_full_tie_deterministically(self) -> None:
        rows = [
            row(**{"Spec. ID": "90002", "Spec. quality": "High"}),
            row(**{"Spec. ID": "90001", "Spec. quality": "High"}),
        ]

        first = rank_candidates(rows, "2002er")
        again = rank_candidates(rows, "2002er")
        reversed_input = rank_candidates(list(reversed(rows)), "2002er")

        order = ["90001", "90002"]
        for result in (first, again, reversed_input):
            self.assertEqual(
                [candidate.spectrum_id for candidate in result.candidates], order
            )


class EligibilityTests(SimpleTestCase):
    """Which rows may be classified at all (R5, R20)."""

    def test_sky_spectrum_with_the_earliest_date_is_excluded(self) -> None:
        """Covers AE11."""
        rows = [
            row(**{"Spec. ID": "90001", "JD": "2452500.5", "Spec. type": "Sky"}),
            row(**{"Spec. ID": "90002", "JD": "2452512.5", "Spec. type": "Object"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertIsNone(result.refusal)
        self.assertEqual([c.spectrum_id for c in result.candidates], ["90002"])

    def test_non_public_row_is_excluded_even_when_earliest(self) -> None:
        rows = [
            row(**{"Spec. ID": "90001", "JD": "2452500.5", "Public": "N"}),
            row(**{"Spec. ID": "90002", "JD": "2452512.5", "Public": "Y"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual([c.spectrum_id for c in result.candidates], ["90002"])

    def test_unparseable_julian_date_is_excluded(self) -> None:
        rows = [
            row(**{"Spec. ID": "90001", "JD": ""}),
            row(**{"Spec. ID": "90002", "JD": "not-a-date"}),
            row(**{"Spec. ID": "90003", "JD": "2452512.5"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual([c.spectrum_id for c in result.candidates], ["90003"])

    def test_row_without_a_spectrum_identifier_is_excluded(self) -> None:
        """A row with no ``Spec. ID`` cannot be retained, so it cannot be chosen."""
        rows = [
            row(**{"Spec. ID": "", "JD": "2452500.5"}),
            row(**{"Spec. ID": "90002", "JD": "2452512.5"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertIsNone(result.refusal)
        self.assertEqual([c.spectrum_id for c in result.candidates], ["90002"])

    def test_only_rows_without_an_identifier_refuse(self) -> None:
        rows = [
            row(**{"Spec. ID": "", "JD": "2452500.5"}),
            row(**{"Spec. ID": " ", "JD": "2452512.5"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual(result.refusal, RefusalReason.NO_ELIGIBLE_SPECTRUM)
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.selected)

    def test_no_eligible_row_refuses_rather_than_succeeding_empty(self) -> None:
        rows = [
            row(**{"Spec. ID": "90001", "Spec. type": "Host"}),
            row(**{"Spec. ID": "90002", "Public": "N"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual(result.refusal, RefusalReason.NO_ELIGIBLE_SPECTRUM)
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.selected)


class ObjectIdentifierTests(SimpleTestCase):
    """WISeREP's object identifier, which addresses the object's own page."""

    def test_candidate_carries_the_object_identifier(self) -> None:
        result = rank_candidates([row(**{"Obj. ID": "1631"})], "2002er")

        self.assertEqual(result.selected.object_id, "1631")

    def test_export_without_the_column_leaves_it_empty(self) -> None:
        bare = {key: value for key, value in row().items() if key != "Obj. ID"}

        result = rank_candidates([bare], "2002er")

        self.assertIsNone(result.refusal)
        self.assertEqual(result.selected.spectrum_id, "15319")
        self.assertEqual(result.selected.object_id, "")


class SubstringMatchTests(SimpleTestCase):
    """WISeREP matches names as substrings, so the rows must be filtered."""

    def _mixed_object_rows(self) -> list[dict[str, str]]:
        """Rows for the ten distinct objects a ``name=2002`` search returns.

        Returns:
            One row per object, with the designations, types and redshifts of
            the captured export.
        """
        captured = [
            ("SN 2025efb", "SN Ib/c", "0.019524"),
            ("SN 2025asm", "SN Ia", "0.01"),
            ("SN 2024ablh", "SN II", "0.004376"),
            ("SN 2002kg", "", "0.0008"),
            ("SN 2002kf", "SN Ia", "0.0193"),
            ("SN 2002jz", "SN Ib/c", "0.00516"),
            ("SN 2002jy", "SN Ia", "0.0196"),
            ("SN 2002jx", "SN II", "0.8446"),
            ("SN 2002jw", "SN Ia", "0.3575"),
            ("SN 2002jv", "SN Ib", "0.2203"),
        ]
        return [
            row(
                **{
                    "IAU name": designation,
                    "Obj. Type": object_type,
                    "Redshift": redshift,
                    "Spec. ID": str(21583 + index),
                    "JD": str(2452615.5 + index),
                }
            )
            for index, (designation, object_type, redshift) in enumerate(captured)
        ]

    def test_rows_spanning_many_objects_refuse_as_ambiguous(self) -> None:
        """Covers AE10.

        The plan's example names seven objects; the captured ``name=2002``
        export this is built from returns ten, so ten is what is asserted.
        """
        result = rank_candidates(self._mixed_object_rows(), "2002")

        self.assertEqual(result.refusal, RefusalReason.AMBIGUOUS_NAME)
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.selected)
        self.assertEqual(len(result.objects), 10)
        self.assertEqual(
            [
                (obj.designation, obj.object_type, obj.redshift)
                for obj in result.objects
            ][:3],
            [
                ("SN 2025efb", "SN Ib/c", 0.019524),
                ("SN 2025asm", "SN Ia", 0.01),
                ("SN 2024ablh", "SN II", 0.004376),
            ],
        )

    def test_exact_name_keeps_only_its_own_rows(self) -> None:
        rows = self._mixed_object_rows()
        rows.append(
            row(
                **{
                    "IAU name": "SN 2002jy",
                    "Obj. Type": "SN Ia",
                    "Redshift": "0.0196",
                    "Spec. ID": "21600",
                    "JD": "2452700.5",
                }
            )
        )

        result = rank_candidates(rows, "2002jy")

        self.assertIsNone(result.refusal)
        self.assertEqual(
            [candidate.designation for candidate in result.candidates],
            ["SN 2002jy", "SN 2002jy"],
        )
        self.assertEqual(result.selected.spectrum_id, "21589")

    def test_internal_designation_resolves_its_own_object(self) -> None:
        rows = [
            row(
                **{
                    "IAU name": "SN 2025efb",
                    "Internal name/s": "ZTF25aapenec, GOTO25bkj, ATLAS25cqj",
                    "Spec. ID": "87136",
                    "JD": "2460821.9347801",
                }
            ),
            row(**{"IAU name": "SN 2002jy", "Spec. ID": "21583", "JD": "2452615.5"}),
        ]

        result = rank_candidates(rows, "GOTO25bkj")

        self.assertIsNone(result.refusal)
        self.assertEqual([c.spectrum_id for c in result.candidates], ["87136"])

    def test_absent_designation_refuses_rather_than_resolving_a_neighbour(self) -> None:
        rows = [
            row(**{"IAU name": "SN 2002erx", "Spec. ID": "90001", "JD": "2452512.5"}),
        ]

        result = rank_candidates(rows, "2002er")

        self.assertEqual(result.refusal, RefusalReason.NO_ELIGIBLE_SPECTRUM)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.objects, ())
