"""Production source may not run work off the request thread undeclared.

Django opens a connection per thread and closes only the request thread's, so
work dispatched to another thread that touches the database abandons a Postgres
backend unless something closes it there. The fix is a seam
(:mod:`astrodash.infrastructure.db_threads`) whose two entry points own the
dispatch: ``run_db_work`` closes what its thread opened, and
``run_non_db_work`` carries the caller's declaration that the work reaches no
database and checks it at runtime.

No static check can tell whether a callable eventually reaches the ORM, so this
guard does not try. It inverts the rule instead: a raw dispatch primitive in
production source is an error whatever it dispatches, which means every
dispatch has to go through the seam and say which kind of work it is. That
check is purely syntactic.

The scan reads each file's syntax tree, because a dispatch is an expression and
a line pattern cannot see through an alias or an attribute chain. The line
patterns below run **only** when a file does not parse. That is the one way
this guard departs from :mod:`astrodash.tests.test_no_osc_references`, which
merges its line hits into every file: ``dash_classifier.py`` and
``transformer_classifier.py`` both say "used from async via to_thread" in a
docstring, and a merged line scan would fail on those clean files.

``tests``, ``migrations`` and ``__pycache__`` are excluded, as in the other
posture guards: this module names the forbidden spellings itself, and the
seam's own tests legitimately drive real threads. The seam module is excluded
by path, since it is the one place a dispatch primitive belongs.
"""

import ast
import re
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import List, Optional, Set, Tuple

from django.test import SimpleTestCase

APP_ROOT = Path(__file__).resolve().parent.parent.parent
EXCLUDED_DIR_NAMES = {"tests", "migrations", "__pycache__"}

# The one module allowed to spell a dispatch primitive: it is the seam the rest
# of the tree dispatches through. Relative to APP_ROOT.
SEAM_MODULE = Path("astrodash") / "infrastructure" / "db_threads.py"

# Callables that start work on a thread or process the request did not create.
# Spelled as the attribute or bare name actually called, so `asyncio.to_thread`,
# a bare `to_thread` from a direct import, and an aliased import all resolve.
# This set is closed, and keeping it current is part of adding a dependency:
# a new library that starts threads of its own (a task queue, another ASGI
# toolkit) passes the guard until its spelling is added here and to
# FALLBACK_PATTERN, with a case in GuardPatternsTests.
DISPATCH_CALL_NAMES = frozenset(
    {
        "to_thread",
        "run_in_executor",
        "Thread",
        "Timer",
        "ThreadPoolExecutor",
        "ProcessPoolExecutor",
        "Process",
        "sync_to_async",
        "run_sync",
        "run_in_threadpool",
    }
)

# Modules whose names, however they are aliased on import, are dispatch
# primitives. `async_to_sync` is deliberately absent: the view layer uses it to
# reach an async service, which starts no thread of its own.
DISPATCH_SOURCE_MODULES = {
    "asyncio",
    "threading",
    "multiprocessing",
    "concurrent.futures",
    "asgiref.sync",
    "anyio",
    "anyio.to_thread",
    "starlette.concurrency",
}

LABEL = "undeclared off-thread dispatch"

# Fallback for a file that does not parse. Deliberately not merged into the
# syntax-tree result; see the module docstring.
FALLBACK_PATTERN = re.compile(
    r"\b(?:to_thread|run_in_executor|Thread|Timer|ThreadPoolExecutor"
    r"|ProcessPoolExecutor|Process|sync_to_async|run_sync"
    r"|run_in_threadpool)\s*\("
)


def _is_excluded(path: Path) -> bool:
    """Report whether a path sits outside the scanned production surface.

    Args:
        path: The file being considered, under :data:`APP_ROOT`.

    Returns:
        True when the path is in an excluded directory or is the seam module.
    """
    relative = path.relative_to(APP_ROOT)
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    return relative == SEAM_MODULE


class _DispatchVisitor(ast.NodeVisitor):
    """Collect calls that start work on a thread the request did not create.

    Resolves three spellings: an attribute call such as ``asyncio.to_thread``
    or ``loop.run_in_executor``, a bare call from a direct import such as
    ``to_thread``, and a call through an import alias such as
    ``from asyncio import to_thread as spawn``.
    """

    def __init__(self) -> None:
        """Seed the hit list and the set of local aliases."""
        self.hits: List[int] = []
        self._aliases: Set[str] = set()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record any alias that renames a dispatch primitive.

        Args:
            node: The ``from X import Y as Z`` statement.
        """
        if node.module in DISPATCH_SOURCE_MODULES:
            for alias in node.names:
                if alias.name in DISPATCH_CALL_NAMES and alias.asname:
                    self._aliases.add(alias.asname)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Record a name bound directly to a dispatch primitive.

        ``spawn = asyncio.to_thread`` followed by ``spawn(work)`` would
        otherwise call a primitive through a name this visitor has never seen.

        Args:
            node: The assignment statement.
        """
        if self._is_dispatch(self._callee_name(node.value)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._aliases.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Flag a call whose callee is a dispatch primitive.

        Args:
            node: The call expression.
        """
        if self._is_dispatch(self._callee_name(node.func)):
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def _is_dispatch(self, name: Optional[str]) -> bool:
        """Report whether a callee name is a dispatch primitive here.

        Args:
            name: The name being called, or None when it is not a plain name
                or attribute.

        Returns:
            True when the name is a dispatch primitive or a local alias of one.
        """
        return name is not None and (
            name in DISPATCH_CALL_NAMES or name in self._aliases
        )

    @staticmethod
    def _callee_name(func: ast.expr) -> Optional[str]:
        """Reduce a callee expression to the name being called.

        Args:
            func: The ``func`` of a call node.

        Returns:
            The attribute or bare name called, or None for anything else.
        """
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None


def _scan_tree(text: str) -> Tuple[bool, List[int]]:
    """Scan source through its syntax tree.

    Args:
        text: The source to scan.

    Returns:
        A ``(parsed, lines)`` pair. ``parsed`` is False when no form of the
        source parses, which is the only case the line pattern covers. The
        ``dedent`` retry is there so a single indented snippet, as the pattern
        tests below pass, still parses.
    """
    for candidate in (text, dedent(text)):
        try:
            tree = ast.parse(candidate)
        except (SyntaxError, ValueError):
            continue
        visitor = _DispatchVisitor()
        visitor.visit(tree)
        return True, visitor.hits
    return False, []


def _scan_source(text: str) -> List[Tuple[int, str]]:
    """Report every undeclared dispatch in some source text.

    Args:
        text: The source to scan.

    Returns:
        A list of ``(lineno, line)`` pairs, one per offending line, ordered by
        line number. Empty when the source declares every dispatch it makes.
    """
    lines = text.splitlines()
    parsed, hits = _scan_tree(text)
    if not parsed:
        hits = [
            lineno
            for lineno, line in enumerate(lines, start=1)
            if FALLBACK_PATTERN.search(line)
        ]
    return [
        (lineno, lines[lineno - 1] if 0 < lineno <= len(lines) else "")
        for lineno in sorted(set(hits))
    ]


def _scan_file(path: Path) -> List[Tuple[int, str]]:
    """Scan one file for undeclared dispatches.

    A file this guard cannot read is reported as clean rather than failing the
    suite: the development tree is bind-mounted, so a dangling editor lock
    symlink or a file that is not UTF-8 would otherwise fail this check for a
    reason that has nothing to do with dispatch posture.

    Args:
        path: The file to read and scan.

    Returns:
        The same shape :func:`_scan_source` returns. Empty for an unreadable
        file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _scan_source(text)


class NoUndeclaredDispatchTests(SimpleTestCase):
    """Production source dispatches only through the seam."""

    def test_production_source_declares_every_dispatch(self) -> None:
        """No production module starts off-thread work on its own."""
        offenders: List[str] = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            if _is_excluded(path):
                continue
            relative = path.relative_to(APP_ROOT)
            for lineno, line in _scan_file(path):
                offenders.append(f"  {relative}:{lineno}: {LABEL}: {line.strip()}")

        self.assertFalse(
            offenders,
            "Work that runs off the request thread must go through "
            "astrodash.infrastructure.db_threads, which closes the database "
            "connections a dispatched thread opens. Use run_db_work for work "
            "that touches the database and run_non_db_work for work that does "
            "not:\n" + "\n".join(offenders),
        )


class GuardPatternsTests(SimpleTestCase):
    """The detection itself, so a later edit cannot weaken or broaden it.

    Without these, a pattern could stop catching a real dispatch, or start
    catching prose, and the suite would stay green either way.
    """

    @staticmethod
    def _flags(source: str) -> bool:
        """Report whether the scan flags some source.

        Args:
            source: The source to scan.

        Returns:
            True when the scan found an undeclared dispatch.
        """
        return bool(_scan_source(source))

    def test_flags_the_dotted_spelling(self) -> None:
        """The ordinary `asyncio.to_thread` call is caught."""
        self.assertTrue(self._flags("await asyncio.to_thread(work, arg)"))

    def test_flags_a_bare_direct_import(self) -> None:
        """A direct import makes the call bare, and it is still caught."""
        source = dedent(
            """
            from asyncio import to_thread

            async def run():
                return await to_thread(work)
            """
        )
        self.assertTrue(self._flags(source))

    def test_flags_an_aliased_import(self) -> None:
        """Renaming the primitive on import does not hide it."""
        source = dedent(
            """
            from asyncio import to_thread as spawn

            async def run():
                return await spawn(work)
            """
        )
        self.assertTrue(self._flags(source))

    def test_flags_every_other_dispatch_form(self) -> None:
        """The forms that do not exist in the tree today are caught too."""
        for call in (
            "threading.Thread(target=work).start()",
            "Thread(target=work).start()",
            "threading.Timer(1.0, work).start()",
            "ThreadPoolExecutor(max_workers=1).submit(work)",
            "concurrent.futures.ProcessPoolExecutor().submit(work)",
            "multiprocessing.Process(target=work).start()",
            "loop.run_in_executor(None, work)",
            "await sync_to_async(work)()",
            "await anyio.to_thread.run_sync(work)",
            "await run_in_threadpool(work)",
        ):
            with self.subTest(call=call):
                self.assertTrue(self._flags(call))

    def test_flags_a_name_assigned_a_dispatch_primitive(self) -> None:
        """Binding the primitive to a local name does not hide it.

        This is the evasion an import-alias check alone misses: the import is
        ordinary and the rename happens in an assignment.
        """
        source = dedent(
            """
            import asyncio

            spawn = asyncio.to_thread

            async def run():
                return await spawn(work)
            """
        )
        self.assertTrue(self._flags(source))

    def test_an_unreadable_file_is_reported_clean(self) -> None:
        """A file this guard cannot read does not fail the suite.

        The development tree is bind-mounted, so an editor lock symlink or a
        file that is not UTF-8 would otherwise fail a check that has nothing to
        do with dispatch posture.
        """
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "dangling.py"
            missing.symlink_to(Path(directory) / "nothing-here.py")
            self.assertEqual(_scan_file(missing), [])

            not_utf8 = Path(directory) / "latin.py"
            not_utf8.write_bytes(
                b"# \xff\xfe not utf-8\nawait asyncio.to_thread(work)\n"
            )
            self.assertEqual(_scan_file(not_utf8), [])

    def test_a_nul_byte_falls_back_to_the_line_pattern(self) -> None:
        """Source ast.parse rejects outright is still scanned.

        Which exception that is has moved between Python versions, so the scan
        catches both and this case asserts only that the source is rejected.
        """
        source = "x = 1\x00\nawait asyncio.to_thread(work)\n"
        with self.assertRaises((SyntaxError, ValueError)):
            ast.parse(source)
        self.assertTrue(self._flags(source))

    def test_does_not_flag_the_seam(self) -> None:
        """A call through either entry point is the declared path."""
        self.assertFalse(self._flags("records = await run_db_work(self.load, name)"))
        self.assertFalse(self._flags("value = await run_non_db_work(self.compute)"))

    def test_does_not_flag_the_async_to_sync_bridge(self) -> None:
        """The view layer's bridge starts no thread of its own."""
        self.assertFalse(self._flags("result = async_to_sync(service.fetch)(name)"))

    def test_does_not_flag_a_primitive_named_in_prose(self) -> None:
        """A docstring or comment mentioning a primitive is not a dispatch."""
        source = dedent(
            '''
            def classify_sync(self, spectrum):
                """Synchronous CPU-bound classification used from async via to_thread."""
                return self._run(spectrum)
            '''
        )
        self.assertFalse(self._flags(source))
        self.assertFalse(self._flags("# ThreadPoolExecutor was considered here"))

    def test_does_not_flag_the_two_classifiers_that_mention_to_thread(self) -> None:
        """Pinned against the real files, not just a snippet of them.

        These two carry the primitive's name in a docstring, so a line-based
        scan merged into every file would fail the suite on clean code.
        """
        for name in ("dash_classifier.py", "transformer_classifier.py"):
            with self.subTest(module=name):
                path = (
                    APP_ROOT
                    / "astrodash"
                    / "infrastructure"
                    / "ml"
                    / "classifiers"
                    / name
                )
                self.assertEqual(_scan_file(path), [])

    def test_falls_back_to_line_patterns_when_a_file_does_not_parse(self) -> None:
        """Unparseable source is scanned by pattern rather than skipped."""
        broken = "def oops(:\n    await asyncio.to_thread(work)\n"
        with self.assertRaises(SyntaxError):
            ast.parse(broken)
        self.assertTrue(self._flags(broken))

    def test_the_seam_module_is_the_only_exempt_path(self) -> None:
        """The exemption is one named module, not a growing list."""
        seam = APP_ROOT / SEAM_MODULE
        self.assertTrue(seam.exists(), "the seam module moved; update SEAM_MODULE")
        self.assertTrue(_is_excluded(seam))

        other: Set[Path] = {
            APP_ROOT / "astrodash" / "domain" / "services" / "model_service.py",
            APP_ROOT / "astrodash" / "domain" / "services" / "spectrum_service.py",
        }
        for path in other:
            with self.subTest(path=path.name):
                self.assertFalse(_is_excluded(path))
