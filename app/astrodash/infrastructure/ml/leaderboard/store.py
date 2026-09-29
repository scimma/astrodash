"""Paths and score-file I/O for the monthly model leaderboard."""

from __future__ import annotations

import json
import re
from calendar import month_abbr, month_name, monthrange
from pathlib import Path
from typing import Any, Optional

PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Score files are small, versioned, and read by the running page, so they live
# with the code.
SCORES_DIR = PACKAGE_ROOT / "data" / "leaderboard"

# Challenge datasets are not. They are tens of megabytes per month, arrive
# monthly, and are read only by the offline scoring step, so they live on the
# external data mount like every other dataset this project uses. See
# app/wiserep_scrape/README.md.
CHALLENGE_SUBDIR = "wiserep_challenge"


def challenge_data_root() -> Path:
    """Root of the monthly challenge datasets on the external data mount."""
    from astrodash.config.settings import get_settings

    return Path(get_settings().data_dir) / CHALLENGE_SUBDIR


def month_label(year_month: str) -> str:
    year, month = _split_year_month(year_month)
    return f"{month_name[month]} {year}"


def eval_window(year_month: str) -> str:
    year, month = _split_year_month(year_month)
    last = monthrange(year, month)[1]
    return f"{month_abbr[month]} 1 – {month_abbr[month]} {last}, {year}"


def next_month(year_month: str) -> str:
    year, month = _split_year_month(year_month)
    if month == 12:
        return month_label(f"{year + 1:04d}-01")
    return month_label(f"{year:04d}-{month + 1:02d}")


def challenge_dir(year_month: str) -> Path:
    return challenge_data_root() / year_month


_MONTH_DIR_RE = re.compile(r"\d{4}-\d{2}")

SNAPSHOT_FILENAME = "snapshot.json"


def load_snapshot(year_month: str) -> Optional[dict[str, Any]]:
    """Read a dataset's provenance record, written by the scrape.

    A WISeREP search window keeps gaining spectra after the month closes, so a
    month's contents mean nothing without the date they were collected. Absent
    for datasets scraped before the scraper recorded it.
    """
    path = challenge_dir(year_month) / SNAPSHOT_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def scores_path(year_month: str) -> Path:
    return SCORES_DIR / f"{year_month}.json"


def load_scores(year_month: str) -> Optional[dict[str, Any]]:
    path = scores_path(year_month)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_scores(payload: dict[str, Any]) -> Path:
    year_month = payload["month"]
    path = scores_path(year_month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def available_months() -> list[str]:
    """Months that have a score file or a scraped challenge directory."""
    found: set[str] = set()
    if SCORES_DIR.is_dir():
        for path in SCORES_DIR.glob("????-??.json"):
            found.add(path.stem)
    root = challenge_data_root()
    if root.is_dir():
        for path in root.iterdir():
            # Only YYYY-MM directories are months. The data mount is a shared
            # volume an operator can leave anything on, and an unvalidated name
            # reached month_label() and 500'd the page.
            if not _MONTH_DIR_RE.fullmatch(path.name):
                continue
            if path.is_dir() and (path / "metadata.csv").is_file():
                found.add(path.name)
    return sorted(found, reverse=True)


def previous_month(year_month: str, months: Optional[list[str]] = None) -> Optional[str]:
    ordered = months if months is not None else available_months()
    later = [m for m in ordered if m < year_month]
    return max(later) if later else None


def _split_year_month(year_month: str) -> tuple[int, int]:
    year_s, month_s = year_month.split("-", 1)
    year, month = int(year_s), int(month_s)
    if month < 1 or month > 12:
        raise ValueError(f"Invalid month {year_month!r}")
    return year, month
