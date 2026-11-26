import csv
import json
import tempfile
import unittest
from pathlib import Path

from twins_pipeline.catalog import check_catalog, load, update_from_csv, export_csv


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.json'
        self.original = {'sn2011fe|2011-08-25 00:00:00': [-3.2, None, None, None, None, None]}
        self.source.write_text(json.dumps(self.original))

    def test_validate(self):
        self.assertEqual(check_catalog(load(self.source)), 1)

    def test_reject_four_field_legacy(self):
        with self.assertRaises(ValueError):
            check_catalog({'sn2011fe|2011-08-25 00:00:00': [1, 2, 3, 4]})

    def test_reject_nan(self):
        with self.assertRaises(ValueError):
            check_catalog({'sn2011fe|2011-08-25 00:00:00': [float('nan'), 1, 2, 3, 4, 5]})

    def test_update_preserves_unmentioned_fields(self):
        csvpath = self.root / 'edits.csv'
        with csvpath.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['observation_key', 'value_json'])
            w.writerow([next(iter(self.original)), json.dumps([-2.1, None, None, None, None, None])])
        output = self.root / 'updated.json'
        self.assertEqual(update_from_csv(self.source, csvpath, output), (1, 1))
        self.assertEqual(load(self.source), self.original)
        self.assertEqual(load(output)[next(iter(self.original))][0], -2.1)

    def test_unknown_key_not_silently_added(self):
        csvpath = self.root / 'edits.csv'
        with csvpath.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['observation_key', 'value_json'])
            w.writerow(['snother|2020-01-01', '[0, null, null, null, null, null]'])
        with self.assertRaises(ValueError):
            update_from_csv(self.source, csvpath, self.root / 'updated.json')

    def test_export_roundtrip(self):
        output = self.root / 'export.csv'
        self.assertEqual(export_csv(self.source, output), 1)
        self.assertIn('observation_key', output.read_text())


if __name__ == '__main__':
    unittest.main()
