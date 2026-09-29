"""Assemble the leaderboard page context from the registry and score files."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from astrodash.infrastructure.ml.leaderboard.dataset import load_challenge
from astrodash.infrastructure.ml.leaderboard.store import (
    available_months,
    challenge_dir,
    load_snapshot,
    eval_window,
    load_scores,
    month_label,
    next_month,
    previous_month,
)
from astrodash.infrastructure.ml.model_registry import listed_definitions


def _delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 1)


def _standings_sort_key(row: dict[str, Any]) -> tuple:
    """Rank by accuracy descending, then ROC descending, then title."""
    roc = row.get("roc")
    roc_key = float(roc) if roc is not None else float("-inf")
    return (-float(row["accuracy"]), -roc_key, row["model"])


def build_leaderboard_context(selected_month: Optional[str] = None) -> dict[str, Any]:
    months = available_months()
    if selected_month and selected_month in months:
        year_month = selected_month
    elif months:
        year_month = months[0]
    else:
        # Nothing scored yet. Show the current month rather than a literal,
        # which would present a fixed past month as if it were the standings.
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")

    scores = load_scores(year_month)
    prev = previous_month(year_month, months)
    prev_scores = load_scores(prev) if prev else None
    prev_by_id = {}
    if prev_scores:
        prev_by_id = {
            row["id"]: row for row in prev_scores.get("models") or [] if row.get("id")
        }

    scored_by_id = {}
    if scores:
        scored_by_id = {
            row["id"]: row for row in scores.get("models") or [] if row.get("id")
        }

    definitions = listed_definitions()
    rankings = []
    for definition in definitions:
        row = scored_by_id.get(definition.id, {})
        prev_row = prev_by_id.get(definition.id, {})
        rankings.append(
            {
                "id": definition.id,
                "model": definition.title,
                "color": definition.color,
                "accuracy": row.get("accuracy"),
                "roc": row.get("roc"),
                "precision": row.get("precision"),
                "recall": row.get("recall"),
                "n_scored": row.get("n_scored"),
                "delta": _delta(row.get("accuracy"), prev_row.get("accuracy")),
            }
        )

    scored = [r for r in rankings if r["accuracy"] is not None]
    unscored = [r for r in rankings if r["accuracy"] is None]
    scored.sort(key=_standings_sort_key)
    for index, row in enumerate(scored, start=1):
        row["rank"] = index
    for row in unscored:
        row["rank"] = None
    rankings = scored + unscored

    spectra_count = None
    if scores and scores.get("spectra_count") is not None:
        spectra_count = scores["spectra_count"]
    else:
        data_dir = challenge_dir(year_month)
        if (data_dir / "metadata.csv").is_file():
            try:
                spectra_count = len(load_challenge(data_dir))
            except OSError:
                spectra_count = None

    status = "Finalized" if scores else "Pending"
    # Prefer the date recorded in the score file; fall back to the dataset's own
    # sidecar so a month scored before this was stamped still shows provenance.
    scraped_at = (scores or {}).get("scraped_at") or (
        load_snapshot(year_month) or {}
    ).get("scraped_at")
    challenge = {
        "month_label": (scores or {}).get("month_label") or month_label(year_month),
        "status": (scores or {}).get("status") or status,
        "scraped_at": scraped_at,
        "spectra_count": spectra_count,
        "eval_window": (scores or {}).get("eval_window") or eval_window(year_month),
        "next_challenge": next_month(year_month),
        "blind_set_note": (
            "Models scored on spectra newly uploaded to WISeREP during the eval window."
        ),
    }

    month_choices = months or [year_month]
    return {
        "challenge": challenge,
        "available_months": [
            {"value": m, "label": month_label(m)} for m in month_choices
        ],
        "selected_month": year_month,
        "rankings": rankings,
        "is_mock": scores is None,
    }
