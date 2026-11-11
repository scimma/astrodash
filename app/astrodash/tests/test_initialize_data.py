import json
import os
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from entrypoints import initialize_data


class StaleFileLogLevelTests(SimpleTestCase):
    def setUp(self) -> None:
        self.data_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_dir.cleanup)
        with open(os.path.join(self.data_dir.name, "stale.npy"), "wb") as fh:
            fh.write(b"old contents")
        manifest = [
            {"path": "stale.npy", "version_id": "v2", "etag": "new", "size": 12}
        ]
        self.manifest_path = os.path.join(self.data_dir.name, "astrodash-data.json")
        with open(self.manifest_path, "w") as fh:
            json.dump(manifest, fh)

        real_join = os.path.join

        def join(*parts: str) -> str:
            if parts[-1] == "astrodash-data.json":
                return self.manifest_path
            return real_join(*parts)

        store = mock.MagicMock()
        store.client = object()
        store.etag_compare.side_effect = [False, True]
        for patcher in (
            mock.patch.object(
                initialize_data, "ASTRODASH_DATA_DIR", self.data_dir.name
            ),
            mock.patch.object(initialize_data, "ObjectStore", return_value=store),
            mock.patch.object(initialize_data.os.path, "join", side_effect=join),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stale_file_is_a_warning_not_an_error(self) -> None:
        with self.assertLogs(initialize_data.logger, level="DEBUG") as logs:
            initialize_data.verify_data_integrity(download=True)

        levels = {record.levelname for record in logs.records}
        self.assertNotIn("ERROR", levels, logs.output)
        self.assertTrue(
            any(
                r.levelname == "WARNING" and "out of date" in r.getMessage()
                for r in logs.records
            ),
            logs.output,
        )
        self.assertTrue(
            any("passes integrity check" in r.getMessage() for r in logs.records),
            logs.output,
        )
