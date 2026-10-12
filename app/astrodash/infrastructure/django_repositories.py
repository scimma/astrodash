"""Django ORM backed repositories used by the AstroDash adapters."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List

from django.db import IntegrityError, transaction
from django.utils import timezone

from astrodash.config.logging import get_logger
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.models.user_model import UserModel
from astrodash.domain.repositories.spectrum_repository import (
    RetainedSpectrumStore,
    SpectrumRepository,
)
from astrodash.domain.repositories.model_repository import ModelRepository
from astrodash.models import (
    SpectrumCandidateSetRecord,
    SpectrumRecord,
    UserModelRecord,
)

logger = get_logger(__name__)


def _latest_for(object_designation: str, source_spectrum_id: str):
    """Return the most recently written record for one epoch of one object.

    Migration 0003 makes the pair unique when neither part is NULL, so this
    normally matches at most one row; the ordering is kept because rows written
    before source identities existed carry NULL and do not take part in that
    constraint.

    Args:
        object_designation: Designation of the object.
        source_spectrum_id: The source's identifier for the spectrum.

    Returns:
        The matching SpectrumRecord, or None.
    """
    return (
        SpectrumRecord.objects.filter(
            object_designation=object_designation,
            source_spectrum_id=source_spectrum_id,
        )
        .order_by("-created_at")
        .first()
    )


class DjangoSpectrumRepository(SpectrumRepository, RetainedSpectrumStore):
    """Persist spectra using the AstroDash Django database."""

    def save(self, spectrum: Spectrum) -> Spectrum:
        """Persist a spectrum, keyed by designation plus source spectrum id.

        A spectrum carrying both an object designation and a source spectrum
        identifier updates the row already retained for that pair, so repeated
        retrievals of one epoch do not accumulate rows while distinct epochs of
        the same object coexist.

        That lookup and the write that follows it are two statements, so two
        first fetches of one epoch can both read no row, both mint a primary
        key and both insert. Migration 0003 makes the pair unique, so the loser
        raises ``IntegrityError``; it is caught here and the winner's row is
        updated instead, because an unhandled one would surface to the
        researcher as an unusable spectrum.

        Args:
            spectrum: The spectrum to persist.

        Returns:
            The spectrum, with its id populated -- the winner's id when a
            concurrent writer inserted the row for this epoch first.
        """
        keyed = bool(spectrum.object_designation and spectrum.source_spectrum_id)
        if keyed:
            retained = _latest_for(
                spectrum.object_designation, spectrum.source_spectrum_id
            )
            if retained is not None:
                spectrum.id = str(retained.id)
        if spectrum.id is None:
            spectrum.id = str(uuid.uuid4())
        defaults = {
            "object_designation": spectrum.object_designation,
            "source_name": spectrum.source_name,
            "source_spectrum_id": spectrum.source_spectrum_id,
            "file_name": spectrum.file_name,
            "x": spectrum.x,
            "y": spectrum.y,
            "redshift": spectrum.redshift,
            "meta": spectrum.meta or {},
        }
        if spectrum.retrieved_at is not None:
            defaults["created_at"] = spectrum.retrieved_at
        if not keyed:
            SpectrumRecord.objects.update_or_create(id=spectrum.id, defaults=defaults)
            return spectrum

        # The savepoint matters as much as the catch: on PostgreSQL a failed
        # statement poisons the surrounding transaction, so the re-read below
        # would fail too without one.
        try:
            with transaction.atomic():
                SpectrumRecord.objects.update_or_create(
                    id=spectrum.id, defaults=defaults
                )
        except IntegrityError:
            winner = _latest_for(
                spectrum.object_designation, spectrum.source_spectrum_id
            )
            if winner is None:
                raise
            logger.info(
                "Concurrent retention of %r spectrum %s resolved onto row %s",
                spectrum.object_designation,
                spectrum.source_spectrum_id,
                winner.id,
            )
            spectrum.id = str(winner.id)
            with transaction.atomic():
                SpectrumRecord.objects.filter(id=spectrum.id).update(**defaults)
        return spectrum

    def get_by_id(self, spectrum_id: str) -> Optional[Spectrum]:
        """Retrieve a spectrum by its storage identifier.

        Args:
            spectrum_id: The storage identifier.

        Returns:
            The spectrum if a row holds that identifier, None otherwise.
        """
        try:
            record = SpectrumRecord.objects.get(id=spectrum_id)
        except SpectrumRecord.DoesNotExist:
            return None
        return self._to_domain(record)

    def get_by_osc_ref(self, object_designation: str) -> Optional[Spectrum]:
        """Retrieve the most recently written spectrum for a designation.

        Args:
            object_designation: The object designation to look up.

        Returns:
            The most recently written spectrum for that designation, or None.
        """
        if not object_designation:
            return None
        try:
            record = SpectrumRecord.objects.filter(
                object_designation=object_designation
            ).latest("created_at")
        except SpectrumRecord.DoesNotExist:
            return None
        return self._to_domain(record)

    def get_by_designation_and_spectrum_id(
        self, object_designation: str, source_spectrum_id: str
    ) -> Optional[Spectrum]:
        """Retrieve the retained spectrum for one epoch of one object.

        Rows written before source identities were recorded carry no source
        spectrum identifier, so they are never returned here.

        Args:
            object_designation: Designation of the object.
            source_spectrum_id: The source's identifier for the spectrum.

        Returns:
            The retained spectrum for that pair, or None.
        """
        if not object_designation or not source_spectrum_id:
            return None
        record = _latest_for(object_designation, source_spectrum_id)
        if record is None:
            return None
        return self._to_domain(record)

    def get_from_file(self, file):  # pragma: no cover - delegated to FileSpectrumRepository
        raise NotImplementedError("Django repository does not parse files")

    @staticmethod
    def _to_domain(record: SpectrumRecord) -> Spectrum:
        """Map a stored record onto the domain spectrum.

        Args:
            record: The stored spectrum record.

        Returns:
            The equivalent domain spectrum.
        """
        return Spectrum(
            id=str(record.id),
            object_designation=record.object_designation,
            source_name=record.source_name,
            source_spectrum_id=record.source_spectrum_id,
            retrieved_at=record.created_at,
            file_name=record.file_name,
            x=record.x,
            y=record.y,
            redshift=record.redshift,
            meta=record.meta or {},
        )


class DjangoCandidateSetRepository:
    """Persist resolved candidate sets using the AstroDash Django database."""

    def save(
        self,
        normalized_name: str,
        candidates: List[Dict[str, Any]],
        resolved_at: Optional[datetime] = None,
    ) -> None:
        """Store the ranked candidate set resolved for a submitted name.

        Storing a set for a name that already has one replaces it, so a
        re-resolution does not accumulate duplicates.

        Args:
            normalized_name: The submitted object name, already normalized by
                the caller.
            candidates: The ranked candidates, each a mapping carrying
                ``source_spectrum_id``, ``observation_date``, ``instrument``,
                ``quality``, ``redshift`` and ``download_url``.
            resolved_at: Time the set was resolved; defaults to now.

        Returns:
            None.
        """
        SpectrumCandidateSetRecord.objects.update_or_create(
            normalized_name=normalized_name,
            defaults={
                "candidates": candidates,
                "resolved_at": resolved_at or timezone.now(),
            },
        )

    def get(self, normalized_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve the candidate set stored for a submitted name.

        Args:
            normalized_name: The submitted object name, already normalized by
                the caller.

        Returns:
            A mapping with ``candidates`` and ``resolved_at``, or None when no
            set is stored for that name.
        """
        if not normalized_name:
            return None
        try:
            record = SpectrumCandidateSetRecord.objects.get(
                normalized_name=normalized_name
            )
        except SpectrumCandidateSetRecord.DoesNotExist:
            return None
        return {"candidates": record.candidates, "resolved_at": record.resolved_at}


class DjangoModelRepository(ModelRepository):
    """Persist user uploaded models using Django ORM."""

    def save(self, model: UserModel) -> UserModel:
        defaults = {
            "name": model.name,
            "description": model.description,
            "owner": model.owner,
            "model_path": model.model_path,
            "class_mapping_path": model.class_mapping_path,
            "input_shape_path": model.input_shape_path,
            "meta": model.meta or {},
        }
        if not model.id:
            model.id = str(uuid.uuid4())
            UserModelRecord.objects.create(id=model.id, **defaults)
        else:
            UserModelRecord.objects.update_or_create(id=model.id, defaults=defaults)
        return model

    def get_by_id(self, model_id: str) -> Optional[UserModel]:
        try:
            record = UserModelRecord.objects.get(id=model_id)
        except UserModelRecord.DoesNotExist:
            return None
        return self._to_domain(record)

    def list_all(self) -> List[UserModel]:
        return [self._to_domain(obj) for obj in UserModelRecord.objects.all()]

    def delete(self, model_id: str) -> None:
        UserModelRecord.objects.filter(id=model_id).delete()

    def get_by_owner(self, owner: str) -> List[UserModel]:
        return [self._to_domain(obj) for obj in UserModelRecord.objects.filter(owner=owner)]

    @staticmethod
    def _to_domain(record: UserModelRecord) -> UserModel:
        return UserModel(
            id=str(record.id),
            name=record.name,
            description=record.description,
            owner=record.owner,
            model_path=record.model_path,
            class_mapping_path=record.class_mapping_path,
            input_shape_path=record.input_shape_path,
            meta=record.meta or {},
            created_at=record.created_at,
        )
