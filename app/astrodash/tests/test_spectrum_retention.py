"""Retention of spectra keyed by object designation and source spectrum id."""

import json
import os
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from astrodash.domain.models.spectrum import Spectrum
from astrodash.infrastructure import django_repositories
from astrodash.infrastructure.django_repositories import (
    DjangoCandidateSetRepository,
    DjangoSpectrumRepository,
)
from astrodash.infrastructure.storage.file_spectrum_repository import (
    FileSpectrumRepository,
)
from astrodash.models import (
    SOURCE_NAME_CATALOGUE,
    SOURCE_NAME_WISEREP,
    SpectrumCandidateSetRecord,
    SpectrumRecord,
)

WAVELENGTHS = [4000.0, 4500.0, 5000.0, 5500.0]
FLUXES = [0.8, 1.0, 0.9, 0.7]


def _spectrum(**overrides):
    """Build a WISeREP-sourced domain spectrum for a test.

    Args:
        **overrides: Fields to override on the spectrum.

    Returns:
        The spectrum.
    """
    fields = {
        "x": list(WAVELENGTHS),
        "y": list(FLUXES),
        "redshift": 0.0085,
        "object_designation": "SN2002er",
        "source_name": SOURCE_NAME_WISEREP,
        "source_spectrum_id": "12345",
        "retrieved_at": timezone.now(),
        "file_name": "sn2002er_20020827.ascii",
    }
    fields.update(overrides)
    return Spectrum(**fields)


class SpectrumRetentionTests(TestCase):
    """The database repository retains one row per object epoch."""

    def setUp(self):
        self.repo = DjangoSpectrumRepository()

    def test_retained_spectrum_stores_designation_spectrum_id_and_retrieval_time(self):
        retrieved_at = timezone.now() - timedelta(hours=3)
        saved = self.repo.save(_spectrum(retrieved_at=retrieved_at))

        record = SpectrumRecord.objects.get(id=saved.id)
        self.assertEqual(record.object_designation, "SN2002er")
        self.assertEqual(record.source_name, SOURCE_NAME_WISEREP)
        self.assertEqual(record.source_spectrum_id, "12345")
        self.assertEqual(record.created_at, retrieved_at)

        read_back = self.repo.get_by_id(saved.id)
        self.assertEqual(read_back.object_designation, "SN2002er")
        self.assertEqual(read_back.source_spectrum_id, "12345")
        self.assertEqual(read_back.retrieved_at, retrieved_at)

    def test_retained_spectrum_keeps_the_source_object_identifier(self):
        """The object identifier travels in ``meta`` and survives retention."""
        saved = self.repo.save(_spectrum(meta={"object_id": "1631"}))

        found = self.repo.get_by_designation_and_spectrum_id("SN2002er", "12345")

        self.assertEqual(found.id, saved.id)
        self.assertEqual(found.meta["object_id"], "1631")

    def test_two_epochs_of_one_designation_both_persist(self):
        first = self.repo.save(_spectrum(source_spectrum_id="12345"))
        second = self.repo.save(_spectrum(source_spectrum_id="67890", y=[0.1] * 4))

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            SpectrumRecord.objects.filter(object_designation="SN2002er").count(), 2
        )
        self.assertEqual(self.repo.get_by_id(first.id).source_spectrum_id, "12345")
        self.assertEqual(self.repo.get_by_id(second.id).source_spectrum_id, "67890")

    def test_lookup_by_designation_and_spectrum_id_returns_the_named_epoch(self):
        earlier = timezone.now() - timedelta(days=2)
        self.repo.save(_spectrum(source_spectrum_id="12345", retrieved_at=earlier))
        self.repo.save(_spectrum(source_spectrum_id="67890", y=[0.1] * 4))

        found = self.repo.get_by_designation_and_spectrum_id("SN2002er", "12345")

        self.assertEqual(found.source_spectrum_id, "12345")
        self.assertEqual(found.retrieved_at, earlier)

    def test_second_write_of_one_epoch_updates_rather_than_duplicating(self):
        first = self.repo.save(_spectrum(source_spectrum_id="12345"))
        second = self.repo.save(
            _spectrum(source_spectrum_id="12345", redshift=0.0092, y=[0.5] * 4)
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            SpectrumRecord.objects.filter(
                object_designation="SN2002er", source_spectrum_id="12345"
            ).count(),
            1,
        )
        found = self.repo.get_by_designation_and_spectrum_id("SN2002er", "12345")
        self.assertEqual(found.redshift, 0.0092)
        self.assertEqual(found.y, [0.5] * 4)

    def test_legacy_catalogue_row_is_never_returned_by_the_epoch_lookup(self):
        SpectrumRecord.objects.create(
            id="legacy-row",
            object_designation="osc-sn2002er-0",
            source_name=SOURCE_NAME_CATALOGUE,
            source_spectrum_id=None,
            file_name="sn2002er.json",
            x=list(WAVELENGTHS),
            y=list(FLUXES),
            redshift=0.0085,
            meta={"source": "osc"},
        )

        self.assertIsNone(
            self.repo.get_by_designation_and_spectrum_id("osc-sn2002er-0", "12345")
        )
        self.assertIsNone(
            self.repo.get_by_designation_and_spectrum_id("osc-sn2002er-0", None)
        )
        self.assertIsNone(
            self.repo.get_by_designation_and_spectrum_id("osc-sn2002er-0", "")
        )

    def test_single_argument_lookup_returns_the_most_recently_written_row(self):
        earlier = timezone.now() - timedelta(days=2)
        self.repo.save(_spectrum(source_spectrum_id="12345", retrieved_at=earlier))
        latest = self.repo.save(_spectrum(source_spectrum_id="67890"))

        found = self.repo.get_by_osc_ref("SN2002er")

        self.assertEqual(found.id, latest.id)
        self.assertIsNone(self.repo.get_by_osc_ref(""))


class ConcurrentFirstFetchTests(TransactionTestCase):
    """Two first fetches of one epoch do not collide on the unique key.

    ``save`` reads before it writes, so two workers fetching one epoch for the
    first time can both find no row, both mint a primary key and both insert.
    Migration 0003 makes (object_designation, source_spectrum_id) unique, so
    the loser's insert raises ``IntegrityError``; unhandled, it reached the
    researcher as "this spectrum is unusable".

    The race is driven by blinding the pre-write read rather than by running
    real threads: that is the one interleaving that matters, and reproducing it
    with threads would turn a deterministic case into a timing-dependent one.
    """

    def test_the_loser_updates_the_winner_row_instead_of_failing(self):
        repo = DjangoSpectrumRepository()
        winner = repo.save(_spectrum(source_spectrum_id="12345"))

        real_latest_for = django_repositories._latest_for
        reads = []

        def blind_first_read(object_designation, source_spectrum_id):
            """Answer the first read as if the winner's row did not exist yet.

            Args:
                object_designation: Designation of the object.
                source_spectrum_id: The source's identifier for the spectrum.

            Returns:
                None on the first call, the real row afterwards.
            """
            reads.append(object_designation)
            if len(reads) == 1:
                return None
            return real_latest_for(object_designation, source_spectrum_id)

        with mock.patch.object(django_repositories, "_latest_for", blind_first_read):
            loser = repo.save(
                _spectrum(source_spectrum_id="12345", redshift=0.0092, y=[0.2] * 4)
            )

        self.assertEqual(loser.id, winner.id, "a second row was minted")
        self.assertEqual(
            SpectrumRecord.objects.filter(
                object_designation="SN2002er", source_spectrum_id="12345"
            ).count(),
            1,
        )
        found = repo.get_by_designation_and_spectrum_id("SN2002er", "12345")
        self.assertEqual(found.redshift, 0.0092)
        self.assertEqual(found.y, [0.2] * 4)

    def test_an_unkeyed_spectrum_still_saves(self):
        """An upload carries neither identifier and takes the plain path."""
        repo = DjangoSpectrumRepository()
        saved = repo.save(
            _spectrum(
                object_designation=None, source_spectrum_id=None, source_name=None
            )
        )

        self.assertIsNotNone(repo.get_by_id(saved.id))


class CandidateSetRetentionTests(TestCase):
    """A resolved candidate set is stored durably under its submitted name."""

    def setUp(self):
        self.repo = DjangoCandidateSetRepository()
        self.candidates = [
            {
                "source_spectrum_id": "12345",
                "observation_date": "2002-08-27",
                "instrument": "FAST",
                "quality": "high",
                "redshift": 0.0085,
                "download_url": "https://www.wiserep.org/spectra/12345/ascii",
            },
            {
                "source_spectrum_id": "67890",
                "observation_date": "2002-09-05",
                "instrument": "LRIS",
                "quality": "medium",
                "redshift": 0.0085,
                "download_url": "https://www.wiserep.org/spectra/67890/ascii",
            },
        ]

    def test_stored_candidate_set_round_trips(self):
        resolved_at = timezone.now() - timedelta(minutes=45)
        self.repo.save("sn2002er", self.candidates, resolved_at=resolved_at)

        stored = self.repo.get("sn2002er")

        self.assertEqual(stored["candidates"], self.candidates)
        self.assertEqual(stored["resolved_at"], resolved_at)
        self.assertEqual(
            [entry["source_spectrum_id"] for entry in stored["candidates"]],
            ["12345", "67890"],
        )
        self.assertIsNone(self.repo.get("sn1999aa"))

    def test_second_resolution_replaces_the_stored_set(self):
        self.repo.save("sn2002er", self.candidates)
        replacement = [self.candidates[1]]

        self.repo.save("sn2002er", replacement)

        self.assertEqual(
            SpectrumCandidateSetRecord.objects.filter(
                normalized_name="sn2002er"
            ).count(),
            1,
        )
        self.assertEqual(self.repo.get("sn2002er")["candidates"], replacement)


class UploadedFileStoreRoundTripTests(SimpleTestCase):
    """The JSON file store on the upload path survives the field rename."""

    def test_uploaded_spectrum_round_trips_through_the_json_file_store(self):
        with TemporaryDirectory() as tmp:
            repo = FileSpectrumRepository(config=SimpleNamespace(storage_dir=tmp))
            spectrum = Spectrum(
                x=list(WAVELENGTHS),
                y=list(FLUXES),
                redshift=0.01,
                file_name="uploaded.dat",
                meta={"source": "upload"},
            )

            saved = repo.save(spectrum)
            read_back = repo.get_by_id(saved.id)

            self.assertEqual(read_back.file_name, "uploaded.dat")
            self.assertEqual(read_back.x, list(WAVELENGTHS))
            self.assertEqual(read_back.y, list(FLUXES))
            self.assertIsNone(read_back.object_designation)

            path = os.path.join(tmp, "spectra", f"{saved.id}.json")
            with open(path) as handle:
                on_disk = json.load(handle)
            self.assertIn("object_designation", on_disk)
            self.assertNotIn("osc_ref", on_disk)


class SpectrumSourceIdentityMigrationTests(TransactionTestCase):
    """Migration 0003 applies to populated data and reverses."""

    migrate_from = [("astrodash", "0002_load_fixtures")]
    migrate_to = [("astrodash", "0003_spectrum_source_identity")]
    legacy_id = "legacy-osc-row"
    undesignated_id = "legacy-upload-row"

    def tearDown(self):
        self._migrate(self.migrate_to)
        SpectrumRecord.objects.filter(
            id__in=[self.legacy_id, self.undesignated_id]
        ).delete()

    @staticmethod
    def _migrate(targets):
        """Run the migration executor to the given targets.

        Args:
            targets: Migration targets to migrate to.

        Returns:
            None.
        """
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    @staticmethod
    def _historical_model(targets, name):
        """Return a model as it stands at the given migration targets.

        Args:
            targets: Migration targets describing the state.
            name: Model name within the astrodash app.

        Returns:
            The historical model class.
        """
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps.get_model("astrodash", name)

    @staticmethod
    def _columns(table):
        """Return the column names of a table.

        Args:
            table: Table name.

        Returns:
            The set of column names.
        """
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table)
        return {column.name for column in description}

    def test_pre_existing_rows_are_marked_catalogue_sourced_with_reference_intact(self):
        self._migrate(self.migrate_from)
        old_record = self._historical_model(self.migrate_from, "SpectrumRecord")
        old_record.objects.create(
            id=self.legacy_id,
            osc_ref="osc-sn2002er-0",
            file_name="sn2002er.json",
            x=list(WAVELENGTHS),
            y=list(FLUXES),
            redshift=0.0085,
            meta={"source": "osc"},
        )

        self._migrate(self.migrate_to)

        record = SpectrumRecord.objects.get(id=self.legacy_id)
        self.assertEqual(record.object_designation, "osc-sn2002er-0")
        self.assertEqual(record.source_name, SOURCE_NAME_CATALOGUE)
        self.assertIsNone(record.source_spectrum_id)
        self.assertIsNone(
            DjangoSpectrumRepository().get_by_designation_and_spectrum_id(
                "osc-sn2002er-0", "12345"
            )
        )

    def test_rows_without_a_designation_are_left_unmarked(self):
        self._migrate(self.migrate_from)
        old_record = self._historical_model(self.migrate_from, "SpectrumRecord")
        old_record.objects.create(
            id=self.undesignated_id,
            osc_ref=None,
            file_name="uploaded.dat",
            x=list(WAVELENGTHS),
            y=list(FLUXES),
            redshift=0.01,
            meta={"source": "upload"},
        )

        self._migrate(self.migrate_to)

        record = SpectrumRecord.objects.get(id=self.undesignated_id)
        self.assertIsNone(record.object_designation)
        self.assertIsNone(record.source_name)
        self.assertEqual(
            SpectrumRecord.objects.filter(
                object_designation__isnull=True, source_name__isnull=False
            ).count(),
            0,
        )

    def test_migration_is_reversible(self):
        self._migrate(self.migrate_from)

        columns = self._columns("astrodash_spectra")
        self.assertIn("osc_ref", columns)
        self.assertNotIn("object_designation", columns)
        self.assertNotIn("source_spectrum_id", columns)
        self.assertNotIn(
            "astrodash_spectrum_candidate_sets", connection.introspection.table_names()
        )

        self._migrate(self.migrate_to)

        columns = self._columns("astrodash_spectra")
        self.assertIn("object_designation", columns)
        self.assertIn("source_name", columns)
        self.assertIn("source_spectrum_id", columns)
        self.assertNotIn("osc_ref", columns)
        self.assertIn(
            "astrodash_spectrum_candidate_sets", connection.introspection.table_names()
        )
