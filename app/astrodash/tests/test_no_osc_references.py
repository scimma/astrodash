"""Regression guards: the retired catalogue is gone and TLS stays verified.

Three things this change removed can be reintroduced by a single line, and
none of them would fail any other test:

* a request to ``api.astrocats.space``, the retired Open Supernova Catalog
  host, whose certificate expired (R2, AE14);
* ``verify=False`` on an outbound request, which is the posture the catalogue
  client depended on (R15, AE9);
* a module-level ``urllib3.disable_warnings(InsecureRequestWarning)``, which
  is process-wide and would hide an insecure call from anyone reading the logs
  (R15, AE9).

The scan covers production source under ``app/`` and excludes ``tests/``,
``migrations/`` and ``__pycache__/``, following
``test_no_version_literals.py``: this module states the forbidden strings
itself, so a scan including tests would flag its own assertions, and the
``osc_ref`` column name in the initial migration is a historical fact of the
schema rather than a live catalogue reference.

The catalogue host and the warning suppression are names, so a line-based
pattern judges them exactly. A disabled ``verify`` is an expression, and
there are many ways to spell the same posture: ``verify = 0``, a value taken
from a variable or a ``{"verify": False}`` mapping, an argument list split
across lines, ``session.verify = False``, or an ``ssl`` context built with
verification off. So that check reads the file's syntax tree instead
(:func:`_scan_tls_posture`), and accepts only an explicit ``verify=True``.
The regex stays as the fallback for a file that does not parse.

``GuardPatternsTests`` proves the patterns still fire on a reintroduced line,
in each of those spellings, and stay quiet on the host-pinning module whose
docstrings discuss verification -- so a future edit cannot weaken a pattern,
or broaden one into a false positive, without breaking the suite.
"""

import ast
import re
from pathlib import Path
from textwrap import dedent
from typing import Iterable, List, Optional, Tuple

from django.test import SimpleTestCase

#: The retired catalogue's API host. Matched loosely on the project name so
#: that a different subdomain of it is caught too.
CATALOGUE_HOST_PATTERN = re.compile(r"astrocats", re.IGNORECASE)

#: Certificate verification turned off on an outbound request, in the one
#: spelling a line-based scan can judge safely. The syntax-aware scan in
#: :func:`_scan_tls_posture` is what catches the equivalent spellings; this
#: pattern remains as the fallback for a file that does not parse, where no
#: syntax tree is available.
VERIFY_DISABLED_PATTERN = re.compile(r"verify\s*=\s*False")

#: Suppression of urllib3's insecure-request warning, in either the call that
#: installs the filter or the warning class it names.
WARNING_SUPPRESSION_PATTERN = re.compile(r"disable_warnings|InsecureRequestWarning")

PATTERNS = (
    ("retired catalogue host", CATALOGUE_HOST_PATTERN),
    ("disabled certificate verification", VERIFY_DISABLED_PATTERN),
    ("suppressed insecure-request warning", WARNING_SUPPRESSION_PATTERN),
)

APP_ROOT = Path(__file__).resolve().parent.parent.parent
EXCLUDED_DIR_NAMES = {"tests", "migrations", "__pycache__"}


def _is_excluded(path: Path) -> bool:
    """Decide whether a file is outside the scanned production source.

    Args:
        path: The file to judge.

    Returns:
        ``True`` when the file lives under an excluded directory.
    """
    return any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(APP_ROOT).parts)


#: The keyword argument, mapping key and session attribute that all carry
#: ``requests``' certificate-verification flag.
VERIFY_NAME = "verify"

#: Names from the ``ssl`` module that disable verification outright, whether
#: called (``ssl._create_unverified_context()``) or passed as a value
#: (``ssl.CERT_NONE``).
INSECURE_SSL_NAMES = frozenset({"_create_unverified_context", "CERT_NONE"})

#: The ``SSLContext`` attribute that drops hostname checking.
HOSTNAME_CHECK_NAME = "check_hostname"

#: What the syntax-aware scan reports, phrased like the entries in
#: :data:`PATTERNS`.
VERIFY_DISABLED_LABEL = "disabled certificate verification"


def _is_true_literal(node: ast.AST) -> bool:
    """Decide whether a node is the literal ``True``.

    Anything else -- ``False``, ``0``, ``None``, ``""``, a name, an
    attribute, a call -- is treated as possibly-falsy, so only an explicit
    ``verify=True`` is accepted.

    Args:
        node: The syntax node standing for the value.

    Returns:
        ``True`` when the node is the constant ``True``.
    """
    return isinstance(node, ast.Constant) and node.value is True


def _names_verify(node: ast.AST) -> bool:
    """Decide whether a node names the verification flag.

    Args:
        node: An assignment target or a mapping key.

    Returns:
        ``True`` for ``verify``, ``obj.verify`` and the string ``"verify"``.
    """
    if isinstance(node, ast.Name):
        return node.id == VERIFY_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == VERIFY_NAME
    return isinstance(node, ast.Constant) and node.value == VERIFY_NAME


def _names_hostname_check(node: ast.AST) -> bool:
    """Decide whether a node names the hostname-check flag.

    Args:
        node: An assignment target or a keyword argument name holder.

    Returns:
        ``True`` for ``check_hostname`` and ``obj.check_hostname``.
    """
    if isinstance(node, ast.Name):
        return node.id == HOSTNAME_CHECK_NAME
    return isinstance(node, ast.Attribute) and node.attr == HOSTNAME_CHECK_NAME


class _TlsPostureVisitor(ast.NodeVisitor):
    """Collect every expression that weakens certificate verification.

    Flags a ``verify`` keyword argument, mapping entry or attribute
    assignment whose value is not the literal ``True``; the ``ssl`` spellings
    in :data:`INSECURE_SSL_NAMES`; and a ``check_hostname`` turned off. Only
    an explicit ``True`` is accepted, so a value reached through a variable,
    a setting or a call is reported rather than trusted.
    """

    def __init__(self) -> None:
        """Start with no hits."""
        self.hits: List[Tuple[str, int]] = []

    def _flag(self, node: ast.AST) -> None:
        """Record one offending node.

        Args:
            node: The node to report, used for its line number.
        """
        self.hits.append((VERIFY_DISABLED_LABEL, getattr(node, "lineno", 1)))

    def _check_mapping(self, node: Optional[ast.AST]) -> None:
        """Flag a ``{"verify": ...}`` entry in a mapping literal.

        Covers both a dict passed as an argument and one splatted with
        ``**``; a mapping built elsewhere cannot be judged here and is left
        to the keyword-argument check at the call site.

        Args:
            node: The mapping expression, or ``None``.
        """
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            if key is not None and _names_verify(key) and not _is_true_literal(value):
                self._flag(value)

    def visit_Call(self, node: ast.Call) -> None:
        """Check a call's keyword arguments and splatted mappings.

        Args:
            node: The call being visited.
        """
        for keyword in node.keywords:
            if keyword.arg is None:
                self._check_mapping(keyword.value)
            elif keyword.arg == VERIFY_NAME and not _is_true_literal(keyword.value):
                self._flag(keyword.value)
            elif keyword.arg == HOSTNAME_CHECK_NAME and not _is_true_literal(
                keyword.value
            ):
                self._flag(keyword.value)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """Check a mapping literal wherever it appears.

        Args:
            node: The mapping being visited.
        """
        self._check_mapping(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check an attribute reference for an insecure ``ssl`` name.

        Args:
            node: The attribute being visited.
        """
        if node.attr in INSECURE_SSL_NAMES:
            self._flag(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Check a bare name for an insecure ``ssl`` name imported directly.

        Args:
            node: The name being visited.
        """
        if node.id in INSECURE_SSL_NAMES:
            self._flag(node)
        self.generic_visit(node)

    def _check_assignment(self, targets: Iterable[ast.AST], value: ast.AST) -> None:
        """Flag an assignment that turns a verification flag off.

        Args:
            targets: The assignment's targets.
            value: The assigned value.
        """
        if _is_true_literal(value):
            return
        for target in targets:
            if _names_verify(target) or _names_hostname_check(target):
                self._flag(value)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check ``session.verify = ...`` and friends.

        Args:
            node: The assignment being visited.
        """
        self._check_assignment(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check an annotated assignment of a verification flag.

        Args:
            node: The annotated assignment being visited.
        """
        if node.value is not None:
            self._check_assignment([node.target], node.value)
        self.generic_visit(node)


def _scan_tls_posture(text: str) -> List[Tuple[str, int]]:
    """Scan source text for a weakened TLS posture, using its syntax tree.

    Parsing is what makes the scan indifferent to spelling: whitespace around
    the ``=``, a falsy value other than ``False``, a value reached through a
    variable or a mapping, an assignment split across lines, and
    ``Session.verify`` all reduce to the same tree shape.

    Args:
        text: The source to scan. A single line is accepted, and is dedented
            before parsing so that an indented line still parses.

    Returns:
        A list of ``(what, lineno)`` tuples. Empty when the source is clean,
        and also when it does not parse, in which case :data:`PATTERNS`
        remains the only check applied.
    """
    for candidate in (text, dedent(text)):
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        visitor = _TlsPostureVisitor()
        visitor.visit(tree)
        return visitor.hits
    return []


def _scan_source(text: str) -> List[Tuple[str, int, str]]:
    """Scan source text for forbidden references and a weakened TLS posture.

    Args:
        text: The source to scan.

    Returns:
        A list of ``(what, lineno, line)`` tuples, one per offending line,
        ordered by line number. Empty when the source is clean.
    """
    lines = text.splitlines()
    hits: List[Tuple[str, int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        for what, pattern in PATTERNS:
            if pattern.search(line):
                hits.append((what, lineno, line))
    for what, lineno in _scan_tls_posture(text):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if (what, lineno, line) not in hits:
            hits.append((what, lineno, line))
    return sorted(hits, key=lambda hit: (hit[1], hit[0]))


def _scan_file(path: Path) -> List[Tuple[str, int, str]]:
    """Scan one ``.py`` file for forbidden references.

    Args:
        path: The file to scan.

    Returns:
        A list of ``(what, lineno, line)`` tuples, one per offending line.
        Empty when the file is clean.
    """
    return _scan_source(path.read_text(encoding="utf-8"))


class NoCatalogueOrInsecureRequestTests(SimpleTestCase):
    """Production source must not reach the catalogue or weaken TLS."""

    def test_production_source_is_clean(self) -> None:
        """Covers AE14 and AE9 over the whole application source tree."""
        offenders: List[str] = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            if _is_excluded(path):
                continue
            for what, lineno, line in _scan_file(path):
                rel = path.relative_to(APP_ROOT)
                offenders.append(f"  {rel}:{lineno}: {what}: {line.strip()}")

        self.assertFalse(
            offenders,
            "Found a retired-catalogue reference or a weakened TLS posture in "
            "production source. The by-name path reads WISeREP with "
            "certificate verification left at its default, and nothing may "
            "suppress urllib3's insecure-request warning process-wide:\n"
            + "\n".join(offenders),
        )


class GuardPatternsTests(SimpleTestCase):
    """The patterns fire on the lines this change removed."""

    @staticmethod
    def _flags(source: str) -> bool:
        """Report whether the guards fire on a snippet.

        Args:
            source: One source line, or a few lines.

        Returns:
            ``True`` when the snippet would be reported as an offender.
        """
        return bool(_scan_source(source))

    def test_flags_catalogue_api_url(self) -> None:
        self.assertTrue(self._flags('    osc_api_url = "https://api.astrocats.space"'))

    def test_flags_catalogue_request(self) -> None:
        self.assertTrue(
            self._flags('response = requests.get("https://api.astrocats.space/2002er")')
        )

    def test_flags_disabled_verification(self) -> None:
        self.assertTrue(self._flags("response = requests.get(url, verify=False)"))

    def test_flags_spaced_disabled_verification(self) -> None:
        self.assertTrue(self._flags("response = requests.get(url, verify = False)"))

    def test_flags_falsy_verification_values(self) -> None:
        """A value other than the ``False`` literal is still disabled."""
        for value in ("0", "None", '""', "[]"):
            with self.subTest(value=value):
                self.assertTrue(
                    self._flags(f"response = requests.get(url, verify={value})")
                )

    def test_flags_verification_from_a_variable(self) -> None:
        """A flag reached through a variable cannot be read as verified."""
        self.assertTrue(self._flags("response = requests.get(url, verify=insecure)"))
        self.assertTrue(
            self._flags("response = requests.get(url, verify=settings.tls_verify)")
        )

    def test_flags_verification_in_a_mapping(self) -> None:
        """A dict literal and a splatted dict carry the flag just as well."""
        self.assertTrue(
            self._flags('response = requests.get(url, **{"verify": False})')
        )
        self.assertTrue(self._flags("kwargs = {'verify': False}"))

    def test_flags_session_attribute_assignment(self) -> None:
        """``Session.verify`` disables verification for every request on it."""
        self.assertTrue(self._flags("session.verify = False"))
        self.assertTrue(self._flags("session.verify = os.environ.get('TLS_VERIFY')"))

    def test_flags_assignment_split_across_lines(self) -> None:
        """A line-based scan misses this; the syntax-aware one does not."""
        self.assertTrue(
            self._flags("response = requests.get(\n    url,\n    verify=False,\n)")
        )

    def test_flags_insecure_ssl_context(self) -> None:
        """The ``ssl`` spellings of the same posture are caught too."""
        self.assertTrue(self._flags("context = ssl._create_unverified_context()"))
        self.assertTrue(self._flags("context.verify_mode = ssl.CERT_NONE"))
        self.assertTrue(self._flags("context.check_hostname = False"))

    def test_flags_warning_suppression(self) -> None:
        self.assertTrue(
            self._flags(
                "urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)"
            )
        )

    def test_does_not_flag_verified_request(self) -> None:
        self.assertFalse(self._flags("response = session.get(url, timeout=timeouts)"))

    def test_does_not_flag_wiserep_host(self) -> None:
        self.assertFalse(self._flags('wiserep_base_url = "https://www.wiserep.org"'))

    def test_does_not_flag_verification_left_on(self) -> None:
        """An explicit ``True`` is the one accepted value."""
        self.assertFalse(self._flags("response = requests.get(url, verify=True)"))
        self.assertFalse(self._flags("session.verify = True"))
        self.assertFalse(self._flags('kwargs = {"verify": True}'))

    def test_does_not_flag_the_host_pinning_module(self) -> None:
        """The module the by-name path actually uses must stay clean.

        Its docstrings talk about certificate verification and its code
        assigns other session attributes, which is exactly the shape a
        broadened pattern is most likely to misread.
        """
        pinning = (
            APP_ROOT
            / "astrodash"
            / "infrastructure"
            / "storage"
            / "wiserep_spectrum_repository.py"
        )
        self.assertEqual(_scan_file(pinning), [])

    def test_does_not_flag_an_unrelated_verify_name(self) -> None:
        """A function whose name merely begins with ``verify`` is fine."""
        self.assertFalse(self._flags("verify_data_integrity(download=False)"))
        self.assertFalse(self._flags("self.assertTrue(session.verify)"))
