"""Usage events encode to valid InfluxDB 1.x line protocol.

Each field's type comes from a fixed table rather than from the value,
because InfluxDB fixes a field's type on its first write and rejects any later
point that sends another type for it.
"""

from django.test import SimpleTestCase

from astrodash.infrastructure.usage.line_protocol import (
    MEASUREMENT,
    Point,
    TimestampSource,
    encode_point,
)

TS = 1_700_000_000_000_000_000


def encode(tags=None, **fields):
    return encode_point(
        Point(tags=tags or {"event": "classification"}, fields=fields, timestamp_ns=TS)
    )


class EscapingTests(SimpleTestCase):
    def test_tag_value_with_space_comma_and_equals_is_escaped(self):
        line = encode({"environment": "a b,c=d"}, count=1)
        self.assertEqual(line, f"{MEASUREMENT},environment=a\\ b\\,c\\=d count=1i {TS}")

    def test_string_field_with_quote_and_backslash_is_escaped(self):
        line = encode(object_name='SN "x" \\ y')
        self.assertEqual(
            line,
            f'{MEASUREMENT},event=classification object_name="SN \\"x\\" \\\\ y" {TS}',
        )

    def test_string_field_with_newline_stays_on_one_line(self):
        line = encode(filename="a\nb\r\nc")
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertIn('filename="a b  c"', line)

    def test_non_ascii_object_name_survives(self):
        line = encode(object_name="SN 2011fé")
        self.assertIn('object_name="SN 2011fé"', line)
        line.encode("utf-8")

    def test_tags_are_sorted_and_empty_tags_omitted(self):
        line = encode(
            {"surface": "ui", "event": "classification", "visitor": ""}, count=1
        )
        self.assertEqual(
            line, f"{MEASUREMENT},event=classification,surface=ui count=1i {TS}"
        )


class FieldTypeTests(SimpleTestCase):
    def test_declared_types_are_encoded(self):
        line = encode(count=3, redshift=0.25, batch=True, model_type="dash")
        fields = line.split(" ")[1]
        self.assertEqual(fields, 'batch=true,count=3i,model_type="dash",redshift=0.25')

    def test_integer_and_float_for_one_numeric_field_both_encode_as_float(self):
        self.assertIn("smoothing=0.0 ", encode(smoothing=0))
        self.assertIn("smoothing=0.5 ", encode(smoothing=0.5))

    def test_numeric_string_is_coerced_to_its_declared_type(self):
        self.assertIn("redshift=0.03 ", encode(redshift="0.03"))
        self.assertIn("count=2i ", encode(count="2"))

    def test_string_field_given_a_number_encodes_as_string(self):
        self.assertIn('source_spectrum_id="1234"', encode(source_spectrum_id=1234))

    def test_boolean_strings_are_coerced(self):
        self.assertIn("known_z=true", encode(known_z="on"))
        self.assertIn("known_z=false", encode(known_z="false"))

    def test_value_that_cannot_be_coerced_is_omitted(self):
        line = encode(redshift="not-a-number", count=1)
        self.assertEqual(line, f"{MEASUREMENT},event=classification count=1i {TS}")

    def test_non_finite_float_is_omitted(self):
        line = encode(redshift=float("nan"), count=1)
        self.assertNotIn("redshift", line)

    def test_empty_value_is_omitted(self):
        line = encode(object_name="", filename=None, count=1)
        self.assertEqual(line, f"{MEASUREMENT},event=classification count=1i {TS}")

    def test_undeclared_field_is_omitted(self):
        line = encode(not_a_declared_field="x", count=1)
        self.assertNotIn("not_a_declared_field", line)

    def test_point_with_no_usable_field_encodes_to_nothing(self):
        self.assertIsNone(encode(redshift="nope"))


class TimestampTests(SimpleTestCase):
    def test_timestamps_are_strictly_increasing(self):
        source = TimestampSource()
        stamps = [source.next_ns() for _ in range(1000)]
        self.assertEqual(len(set(stamps)), 1000)
        self.assertEqual(stamps, sorted(stamps))

    def test_a_clock_that_stands_still_still_yields_increasing_stamps(self):
        source = TimestampSource(clock_ns=lambda: TS)
        self.assertEqual([source.next_ns() for _ in range(3)], [TS, TS + 1, TS + 2])
