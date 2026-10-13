"""The seam that runs work off the request thread without leaking connections.

Django opens a connection per thread and closes only the request thread's, and
``async_to_sync`` builds a fresh event loop, executor and context for every
call, so a connection opened on a dispatched thread belongs to a thread and a
context no request lifecycle cleans up. These cases pin the two guarantees the
seam exists to make: database work closes what it opened, on the thread that
opened it, and work declared as reaching no database is checked rather than
trusted.

They subclass ``TransactionTestCase`` because the work runs on a real worker
thread, which holds its own connection and would not see an uncommitted
transaction opened on the main thread.
"""

import asyncio
import threading
from typing import Any, Dict, List

from asgiref.sync import async_to_sync
from django.db import connections
from django.test import TransactionTestCase

from astrodash.infrastructure import db_threads
from astrodash.models import SpectrumRecord


class DatabaseWorkTests(TransactionTestCase):
    """``run_db_work`` dispatches the work and closes that thread's connection."""

    def test_work_runs_off_the_callers_thread(self) -> None:
        """The seam dispatches rather than running in the caller's thread."""
        observed: Dict[str, Any] = {}
        caller = threading.current_thread()

        def work() -> str:
            """Touch the database and record where this ran."""
            SpectrumRecord.objects.exists()
            observed["thread"] = threading.current_thread()
            return "done"

        result = async_to_sync(db_threads.run_db_work)(work)

        self.assertEqual(result, "done")
        self.assertIsNot(
            observed["thread"],
            caller,
            "the seam ran the work in the caller's thread instead of dispatching it",
        )

    def test_the_dispatched_threads_connection_is_closed(self) -> None:
        """The connection the dispatched thread opened does not outlive the work."""
        observed: Dict[str, Any] = {}

        def work() -> None:
            """Open this thread's connection and hand the wrapper back."""
            SpectrumRecord.objects.exists()
            wrapper = connections["default"]
            observed["open_during"] = wrapper.connection is not None
            observed["wrapper"] = wrapper

        async_to_sync(db_threads.run_db_work)(work)

        self.assertTrue(
            observed["open_during"], "the work never opened a connection to close"
        )
        self.assertIsNone(
            observed["wrapper"].connection,
            "the dispatched thread's connection outlived the work that opened it",
        )

    def test_the_callers_own_connection_is_left_alone(self) -> None:
        """Closing happens on the dispatched thread, never the caller's.

        This is what makes the guarantee structural: a seam that closed in the
        caller's thread could land inside an open atomic block.
        """
        SpectrumRecord.objects.exists()
        caller_wrapper = connections["default"]
        self.assertIsNotNone(
            caller_wrapper.connection, "the test's own connection was not open"
        )

        async_to_sync(db_threads.run_db_work)(SpectrumRecord.objects.exists)

        self.assertIsNotNone(
            caller_wrapper.connection,
            "the seam closed the caller's connection as well as the worker's",
        )

    def test_a_raising_call_still_closes_the_connection(self) -> None:
        """An exception propagates and the connection is closed anyway."""
        observed: Dict[str, Any] = {}

        def work() -> None:
            """Open a connection, then fail."""
            SpectrumRecord.objects.exists()
            observed["wrapper"] = connections["default"]
            raise RuntimeError("work failed after the connection was opened")

        with self.assertRaises(RuntimeError):
            async_to_sync(db_threads.run_db_work)(work)

        self.assertIsNone(
            observed["wrapper"].connection,
            "a failing call left its connection open",
        )

    def test_arguments_and_return_value_pass_through(self) -> None:
        """The seam forwards positional and keyword arguments untouched."""

        def work(first: int, second: int, *, third: int) -> int:
            """Return something derived from every argument."""
            return first * 100 + second * 10 + third

        result = async_to_sync(db_threads.run_db_work)(work, 1, 2, third=3)

        self.assertEqual(result, 123)


class NonDatabaseWorkTests(TransactionTestCase):
    """``run_non_db_work`` dispatches, and checks the declaration it was given."""

    def test_work_runs_and_returns_its_value(self) -> None:
        """Declared non-database work is dispatched and its value returned."""
        observed: Dict[str, Any] = {}
        caller = threading.current_thread()

        def work(value: str) -> str:
            """Do no database work at all."""
            observed["thread"] = threading.current_thread()
            return value.upper()

        result = async_to_sync(db_threads.run_non_db_work)(work, "quiet")

        self.assertEqual(result, "QUIET")
        self.assertIsNot(observed["thread"], caller, "the work was not dispatched")

    def test_honest_non_database_work_opens_no_connection(self) -> None:
        """Work that touches nothing leaves no connection behind."""
        observed: Dict[str, Any] = {}

        def work() -> None:
            """Record the wrapper without ever using it."""
            observed["wrapper"] = connections["default"]

        async_to_sync(db_threads.run_non_db_work)(work)

        self.assertIsNone(
            observed["wrapper"].connection,
            "non-database work opened a connection of its own",
        )

    def test_a_misdeclared_call_is_detected_and_closed(self) -> None:
        """Work that reaches the database despite its declaration is caught.

        The declaration is the only evidence that work is non-database, and a
        syntactic guard cannot check it. So the seam checks: a connection open
        on the dispatched thread means the declaration was wrong, and the leak
        is closed rather than left for the garbage collector.
        """
        observed: Dict[str, Any] = {}

        def lying_work() -> None:
            """Declared non-database, but it queries."""
            SpectrumRecord.objects.exists()
            observed["wrapper"] = connections["default"]

        with self.assertLogs("astrodash.infrastructure.db_threads", "ERROR") as logs:
            async_to_sync(db_threads.run_non_db_work)(lying_work)

        self.assertIsNone(
            observed["wrapper"].connection,
            "a misdeclared dispatch left its connection open",
        )
        self.assertTrue(
            any("lying_work" in message for message in logs.output),
            f"the error did not name the offending callable: {logs.output}",
        )

    def test_a_misdeclared_raising_call_is_also_detected(self) -> None:
        """The check runs even when the declared work fails."""
        observed: Dict[str, Any] = {}

        def lying_work() -> None:
            """Query, then fail."""
            SpectrumRecord.objects.exists()
            observed["wrapper"] = connections["default"]
            raise RuntimeError("failed after querying")

        with self.assertLogs("astrodash.infrastructure.db_threads", "ERROR"):
            with self.assertRaises(RuntimeError):
                async_to_sync(db_threads.run_non_db_work)(lying_work)

        self.assertIsNone(
            observed["wrapper"].connection,
            "a misdeclared failing dispatch left its connection open",
        )


class SeamSurfaceTests(TransactionTestCase):
    """The module exposes exactly the two declared entry points.

    The posture guard exempts this module by path, so a third dispatching
    helper added here would be unguarded. Pinning the public surface keeps that
    exemption narrow.
    """

    def test_only_the_two_entry_points_are_public(self) -> None:
        """Nothing public but the two entry points lives in this module."""
        public: List[str] = sorted(
            name
            for name, value in vars(db_threads).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", "") == db_threads.__name__
        )

        self.assertEqual(public, ["run_db_work", "run_non_db_work"])

    def test_both_entry_points_are_awaitable(self) -> None:
        """Call sites await the seam rather than dispatching themselves."""
        self.assertTrue(asyncio.iscoroutinefunction(db_threads.run_db_work))
        self.assertTrue(asyncio.iscoroutinefunction(db_threads.run_non_db_work))
