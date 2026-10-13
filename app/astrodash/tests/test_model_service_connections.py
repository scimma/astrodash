"""The model service's thread-dispatched database work closes its connections.

Every public method of :class:`~astrodash.domain.services.model_service.ModelService`
that reaches storage does so off the request thread, and Django's connection
handler is keyed per thread and per context. A connection opened on a
dispatched thread therefore belongs to a thread and a context that no request
lifecycle cleans up, so it survives the work returning and the thread dying,
and comes back only when CPython's cycle collector next runs.

Nothing in the suite drove these dispatches against a real database before:
every other case that touches this service replaces it with a mock, so the
nine dispatch sites had no coverage at all. These cases construct the real
service over the real :class:`DjangoModelRepository` and drive it through its
public methods, one case per method that dispatches. Model storage is stubbed,
because the subject is connection hygiene on the dispatch and not file
storage.

Two observables are recorded inside the dispatched work itself, because a
thread-local connection handler is unreachable from the test thread once the
dispatched thread goes idle:

* the connection wrapper, asserted closed after the method returns; and
* whether a connection was already open when the dispatched work was entered.

Both are needed to detect a bypass at any one site. The event loop that
``async_to_sync`` builds has a single default executor, so two dispatches made
by one method usually land on the same thread. A later routed dispatch would
then close the connection an earlier bypassed one abandoned, and the
after-the-fact check alone would pass while observing nothing. The entry check
catches exactly that case: dispatched work must never find a connection
already open on its thread. Between them, reverting any one of the nine sites
to a raw dispatch fails the case that drives its method -- on the entry check
if a later routed dispatch shares the thread, and on the wrapper check
otherwise.

These cases subclass ``TransactionTestCase`` and so commit: the work runs on a
real worker thread holding its own connection, which would not see an
uncommitted transaction opened on the main thread.
"""

import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from asgiref.sync import async_to_sync
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import TransactionTestCase

from astrodash.domain.models.user_model import UserModel
from astrodash.domain.services.model_service import ModelService
from astrodash.infrastructure.django_repositories import DjangoModelRepository
from astrodash.models import UserModelRecord

OWNER = "owner@example.org"


@dataclass
class DispatchObservation:
    """What one dispatched repository call saw about its own thread.

    Attributes:
        name: The repository method that ran.
        thread: The thread the call ran on.
        wrapper: That thread's connection wrapper, kept referenced so the
            cycle collector cannot close the connection behind the assertion.
        open_during: Whether the call had a connection open when it finished.
        open_on_entry: Whether a connection was already open when the call
            was entered, which means an earlier dispatch on this thread left
            one behind.
    """

    name: str
    thread: threading.Thread
    wrapper: BaseDatabaseWrapper
    open_during: bool
    open_on_entry: bool


class RecordingModelRepository(DjangoModelRepository):
    """The real repository, reporting what each dispatched call's thread held.

    Every method runs its real Django ORM implementation, so each call opens a
    connection exactly as it does in production. The recording happens inside
    the dispatched work, which is the only place the thread's connection
    handler can be reached.
    """

    def __init__(self, fail_after_querying: Optional[Set[str]] = None) -> None:
        """Set up the recorder.

        Args:
            fail_after_querying: Method names that should run their real query
                and then raise, to drive the failing-call case.
        """
        self.calls: List[DispatchObservation] = []
        self.fail_after_querying: Set[str] = set(fail_after_querying or ())

    def _record(self, name: str, work: Any, *args: Any) -> Any:
        """Run one repository method and record its thread's connection state.

        Args:
            name: The repository method being run.
            work: The real implementation to call.
            *args: Arguments for the real implementation.

        Returns:
            Whatever the real implementation returns.

        Raises:
            RuntimeError: If this method was configured to fail after querying.
        """
        open_on_entry = connections["default"].connection is not None
        try:
            result = work(*args)
            if name in self.fail_after_querying:
                raise RuntimeError(f"{name} failed after opening a connection")
            return result
        finally:
            wrapper = connections["default"]
            self.calls.append(
                DispatchObservation(
                    name=name,
                    thread=threading.current_thread(),
                    wrapper=wrapper,
                    open_during=wrapper.connection is not None,
                    open_on_entry=open_on_entry,
                )
            )

    def save(self, model: UserModel) -> UserModel:
        """Record, then persist the model for real."""
        return self._record("save", super().save, model)

    def get_by_id(self, model_id: str) -> Optional[UserModel]:
        """Record, then read the model for real."""
        return self._record("get_by_id", super().get_by_id, model_id)

    def list_all(self) -> List[UserModel]:
        """Record, then list every model for real."""
        return self._record("list_all", super().list_all)

    def delete(self, model_id: str) -> None:
        """Record, then delete the model for real."""
        return self._record("delete", super().delete, model_id)

    def get_by_owner(self, owner: str) -> List[UserModel]:
        """Record, then read the owner's models for real."""
        return self._record("get_by_owner", super().get_by_owner, owner)


class StubModelStorage:
    """Stands in for file storage so the dispatch is the only subject."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.cleaned: List[str] = []
        self.updated: List[str] = []

    def save_model_files(self, model_id: str, **kwargs: Any) -> Dict[str, str]:
        """Report the paths an upload would have written.

        Args:
            model_id: The model the upload is for.
            **kwargs: The content and metadata, which this stub discards.

        Returns:
            A path for each artifact the upload records.
        """
        return {
            "model": f"/stub/{model_id}.pt",
            "class_mapping": f"/stub/{model_id}.classes.json",
            "input_shape": f"/stub/{model_id}.input_shape.json",
            "metadata": f"/stub/{model_id}.meta.json",
        }

    def get_model_path(self, model_id: str) -> str:
        """Return a path that does not exist, so forward-pass validation fails.

        Args:
            model_id: The model whose path is wanted.

        Returns:
            A path under a directory this stub never creates.
        """
        return f"/stub/{model_id}.pt"

    def model_exists(self, model_id: str) -> bool:
        """Report the model's files as present.

        Args:
            model_id: The model being checked.

        Returns:
            Always ``True``.
        """
        return True

    def cleanup_model_files(self, model_id: str) -> None:
        """Record a cleanup instead of deleting anything.

        Args:
            model_id: The model whose files would have been removed.
        """
        self.cleaned.append(model_id)

    def update_metadata(self, model_id: str, updates: Dict[str, Any]) -> None:
        """Record a metadata update instead of writing one.

        Args:
            model_id: The model whose metadata would have changed.
            updates: The fields that would have been written.
        """
        self.updated.append(model_id)


class ModelServiceConnectionTests(TransactionTestCase):
    """Each dispatching public method leaves no connection open behind it."""

    def setUp(self) -> None:
        """Build the real service over the recording repository."""
        self.repo = RecordingModelRepository()
        self.storage = StubModelStorage()
        self.service = ModelService(self.repo, model_storage=self.storage)

    def seed_model(self) -> str:
        """Store one model row for the read, delete and update paths.

        Returns:
            The identifier of the stored model.
        """
        model_id = str(uuid.uuid4())
        UserModelRecord.objects.create(
            id=model_id,
            name="seeded",
            description="seeded model",
            owner=OWNER,
            model_path=f"/stub/{model_id}.pt",
            class_mapping_path=f"/stub/{model_id}.classes.json",
            input_shape_path=f"/stub/{model_id}.input_shape.json",
            meta={},
        )
        return model_id

    def assert_dispatches_were_clean(self, expected: Sequence[str]) -> None:
        """Assert the dispatched calls ran and left no connection open.

        Args:
            expected: The repository methods the driven service method is
                expected to have dispatched, in order.
        """
        self.assertEqual(
            [call.name for call in self.repo.calls],
            list(expected),
            "the service did not dispatch the repository calls this case covers",
        )
        caller = threading.current_thread()
        for call in self.repo.calls:
            self.assertIsNot(
                call.thread,
                caller,
                f"{call.name} ran in the caller's thread instead of being dispatched",
            )
            self.assertTrue(
                call.open_during,
                f"{call.name} never opened a connection, so this case proves nothing",
            )
            self.assertFalse(
                call.open_on_entry,
                f"{call.name} was dispatched onto a thread that already held an "
                "open connection, so an earlier dispatch bypassed the seam",
            )
            self.assertIsNone(
                call.wrapper.connection,
                f"the connection {call.name} opened outlived the work on its thread",
            )

    def test_upload_model_leaves_no_open_connection(self) -> None:
        """``upload_model`` persists the new model and closes behind itself."""
        async_to_sync(self.service.upload_model)(
            model_content=b"not a real model",
            filename="model.pth",
            class_mapping_str='{"Ia": 0}',
            input_shape_str="[1, 1024]",
            name="uploaded",
            description="an uploaded model",
            owner=OWNER,
        )

        self.assert_dispatches_were_clean(["save"])

    def test_save_model_leaves_no_open_connection(self) -> None:
        """``save_model`` checks for a duplicate name and then saves."""
        model = UserModel(
            id=str(uuid.uuid4()),
            name="saved",
            owner=OWNER,
            model_path="/stub/saved.pt",
            class_mapping_path="/stub/saved.classes.json",
            input_shape_path="/stub/saved.input_shape.json",
        )

        async_to_sync(self.service.save_model)(model)

        self.assert_dispatches_were_clean(["get_by_owner", "save"])

    def test_get_model_leaves_no_open_connection(self) -> None:
        """``get_model`` reads one model off the request thread."""
        model_id = self.seed_model()

        async_to_sync(self.service.get_model)(model_id)

        self.assert_dispatches_were_clean(["get_by_id"])

    def test_list_models_leaves_no_open_connection(self) -> None:
        """``list_models`` lists every model off the request thread."""
        self.seed_model()

        async_to_sync(self.service.list_models)()

        self.assert_dispatches_were_clean(["list_all"])

    def test_delete_model_leaves_no_open_connection(self) -> None:
        """``delete_model`` reads the model and then deletes it."""
        model_id = self.seed_model()

        async_to_sync(self.service.delete_model)(model_id)

        self.assert_dispatches_were_clean(["get_by_id", "delete"])

    def test_list_models_by_owner_leaves_no_open_connection(self) -> None:
        """``list_models_by_owner`` filters by owner off the request thread."""
        self.seed_model()

        async_to_sync(self.service.list_models_by_owner)(OWNER)

        self.assert_dispatches_were_clean(["get_by_owner"])

    def test_update_model_metadata_leaves_no_open_connection(self) -> None:
        """``update_model_metadata`` reads the model and then saves it."""
        model_id = self.seed_model()

        async_to_sync(self.service.update_model_metadata)(
            model_id, {"description": "renamed"}
        )

        self.assert_dispatches_were_clean(["get_by_id", "save"])

    def test_a_failing_repository_call_leaves_no_open_connection(self) -> None:
        """A repository call that raises still closes what it opened."""
        self.repo.fail_after_querying.add("list_all")
        self.seed_model()

        with self.assertRaises(RuntimeError):
            async_to_sync(self.service.list_models)()

        self.assert_dispatches_were_clean(["list_all"])
