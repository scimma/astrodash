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

``GuardPatternsTests`` proves the patterns still fire on a reintroduced line,
so a future edit cannot weaken a pattern without breaking the suite.
"""

import re
from pathlib import Path
from typing import List, Tuple

from django.test import SimpleTestCase

#: The retired catalogue's API host. Matched loosely on the project name so
#: that a different subdomain of it is caught too.
CATALOGUE_HOST_PATTERN = re.compile(r"astrocats", re.IGNORECASE)

#: Certificate verification turned off on an outbound request.
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


def _scan_file(path: Path) -> List[Tuple[str, int, str]]:
    """Scan one ``.py`` file for forbidden references.

    Args:
        path: The file to scan.

    Returns:
        A list of ``(what, lineno, line)`` tuples, one per offending line.
        Empty when the file is clean.
    """
    hits: List[Tuple[str, int, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for what, pattern in PATTERNS:
            if pattern.search(line):
                hits.append((what, lineno, line))
    return hits


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
    def _flags(line: str) -> bool:
        """Report whether any guard pattern matches a line.

        Args:
            line: One source line.

        Returns:
            ``True`` when the line would be reported as an offender.
        """
        return any(pattern.search(line) for _, pattern in PATTERNS)

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
