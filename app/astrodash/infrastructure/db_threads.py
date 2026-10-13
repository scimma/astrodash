"""Running work off the request thread without abandoning database connections.

Django's connection handler is keyed per thread and per context, and
``asgiref.sync.async_to_sync`` -- how the view layer reaches these async
services -- runs each call's event loop on a new thread, and with it a fresh
default executor and a fresh ``contextvars`` context. A connection opened on a
dispatched thread therefore belongs to a thread and a context that no request
lifecycle ever cleans up: it survives the work returning, the thread dying and
ordinary reference counting, and comes back only when CPython's cycle
collector next runs. A worker process consequently holds a number of open
Postgres backends governed by garbage-collection timing rather than by load.

Two entry points live here, and both perform their own dispatch so that a
call site awaits the seam rather than spelling a dispatch primitive itself:

* :func:`run_db_work` runs work that reaches the database and closes that
  thread's connections when it finishes.
* :func:`run_non_db_work` runs work the caller declares reaches no database.

The declaration is the only evidence that work is non-database, and no
syntactic check can confirm it, so :func:`run_non_db_work` verifies it: a
connection open on the dispatched thread means the declaration was wrong, and
the connection is closed and the offending callable logged rather than left to
leak quietly. Closing rather than raising keeps a misdeclaration from turning a
silent leak into a failed request. When in doubt, choose
:func:`run_db_work`: work that reaches the database on only one branch, or
through a queryset evaluated later, is database work, and the cost of being
wrong in that direction is one connection close that was not needed.

Closing only ever happens on the thread this module dispatched to. The caller's
own connections are never touched, because closing every connection visible in
a thread that may hold an open atomic block is a worse outcome than the leak
this module exists to prevent.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, List, TypeVar

from django.db import connections

from astrodash.config.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def _aliases_with_open_connections() -> List[str]:
    """Report which database aliases hold an open connection on this thread.

    Only wrappers this thread already created are inspected, so a thread that
    touched no database does no work here and gains no wrapper it never had.

    Returns:
        The aliases whose wrapper currently holds an open connection.
    """
    return [
        wrapper.alias
        for wrapper in connections.all(initialized_only=True)
        if wrapper.connection is not None
    ]


def _describe(work: Callable[..., Any]) -> str:
    """Name a callable well enough to locate the call site that declared it.

    Args:
        work: The callable a caller handed to one of the entry points.

    Returns:
        A ``module.qualname`` label, falling back to ``repr`` for a callable
        that carries neither.
    """
    module = getattr(work, "__module__", None)
    qualname = getattr(work, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(work)


def _close_quietly(work: Callable[..., Any]) -> None:
    """Close this thread's connections without masking the work's outcome.

    Both entry points close from a ``finally``, where a raising close would
    replace whatever the work returned or raised and leave the caller
    classifying the wrong failure. Django's ``close()`` can surface a backend
    error when the socket is already broken, so the close error is logged and
    swallowed: the work's own outcome is the one the caller needs, and the
    thread is about to end regardless.

    Args:
        work: The callable whose dispatch is being cleaned up, named in the log
            so a close failure can be traced to a call site.
    """
    try:
        connections.close_all()
    except Exception:  # noqa: BLE001 - a cleanup error must not mask the work
        logger.exception(
            "closing the connections opened by %s failed; the work's own "
            "result stands and this thread is ending",
            _describe(work),
        )


async def run_db_work(work: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run database work on a dispatched thread and close its connections.

    Args:
        work: The callable to run, which may touch the database.
        *args: Positional arguments for ``work``.
        **kwargs: Keyword arguments for ``work``.

    Returns:
        Whatever ``work`` returns.

    Raises:
        Exception: Whatever ``work`` raises, after the connections it opened
            on the dispatched thread have been closed.
    """

    def _run_and_close() -> T:
        """Run the work, then close this thread's connections."""
        try:
            return work(*args, **kwargs)
        finally:
            _close_quietly(work)

    return await asyncio.to_thread(_run_and_close)


async def run_non_db_work(work: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run work declared to reach no database, and check that declaration.

    Args:
        work: The callable to run, which the caller declares touches no
            database.
        *args: Positional arguments for ``work``.
        **kwargs: Keyword arguments for ``work``.

    Returns:
        Whatever ``work`` returns.

    Raises:
        Exception: Whatever ``work`` raises, after any connection it opened in
            spite of its declaration has been closed.
    """

    def _run_and_check() -> T:
        """Run the work, then verify it opened no connection."""
        try:
            return work(*args, **kwargs)
        finally:
            opened = _aliases_with_open_connections()
            if opened:
                logger.error(
                    "%s was dispatched as non-database work but opened a "
                    "connection on %s; the connection is being closed, but the "
                    "call site should run through run_db_work instead",
                    _describe(work),
                    ", ".join(opened),
                )
                _close_quietly(work)

    return await asyncio.to_thread(_run_and_check)
