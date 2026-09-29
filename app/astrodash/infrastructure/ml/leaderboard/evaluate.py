"""Score listed classifiers on a monthly WISeREP challenge and write JSON.

Usage, from the ``app/`` directory inside the container (scoring needs the
classifiers, torch, and the model weights on the data mount):

    python -m astrodash.infrastructure.ml.leaderboard.evaluate --month 2026-07

The month's dataset is read from ``{ASTRODASH_DATA_DIR}/wiserep_challenge/``;
``--data`` overrides that. See ``app/wiserep_scrape/README.md`` for how to pull
a month onto the mount.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

from astrodash.config.logging import get_logger
from astrodash.config.settings import get_settings
from astrodash.infrastructure.ml.leaderboard.dataset import (
    ChallengeSpectrum,
    load_challenge,
)
from astrodash.infrastructure.ml.leaderboard.metrics import score_predictions
from astrodash.infrastructure.ml.leaderboard.store import (
    challenge_dir,
    eval_window,
    month_label,
    write_scores,
)
from astrodash.infrastructure.ml.leaderboard.taxonomy import (
    CANONICAL_CLASSES,
    canonicalize,
    remap_probabilities,
)
from astrodash.infrastructure.ml.model_factory import ModelFactory
from astrodash.infrastructure.ml.model_registry import (
    REDSHIFT_INPUT_REQUIRED,
    ModelDefinition,
    listed_definitions,
)

logger = get_logger(__name__)


def load_snapshot_for(data_dir: Path) -> Optional[dict]:
    """Read the snapshot sidecar from an explicit dataset directory."""
    path = Path(data_dir) / "snapshot.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _probability_vector(result: dict) -> Optional[list[float]]:
    raw = result.get("class_probabilities")
    if not isinstance(raw, dict) or not raw:
        pred = canonicalize((result.get("best_match") or {}).get("type"))
        if pred is None:
            return None
        return [1.0 if name == pred else 0.0 for name in CANONICAL_CLASSES]
    remapped = remap_probabilities(raw)
    vector = [remapped[name] for name in CANONICAL_CLASSES]
    if not all(math.isfinite(value) for value in vector) or sum(vector) <= 0:
        # roc_auc_score rejects the whole array if any element is NaN, so one
        # bad spectrum would silently cost the model its ROC for the entire
        # month -- which is what "roc": null meant for the transformer. Drop
        # the spectrum instead; the caller counts it as skipped.
        return None
    return vector


def score_definition(
    definition: ModelDefinition,
    rows: list[ChallengeSpectrum],
    classifier,
) -> dict:
    requires_z = definition.redshift_input == REDSHIFT_INPUT_REQUIRED
    y_true: list[str] = []
    y_pred: list[str] = []
    y_proba: list[list[float]] = []
    skipped = 0

    for row in rows:
        if requires_z and row.redshift is None:
            skipped += 1
            continue
        try:
            result = classifier.classify_sync(row.spectrum)
        except Exception as exc:
            skipped += 1
            logger.warning(
                "Skipping %s on %s: %s",
                definition.id,
                row.filename,
                exc,
            )
            continue
        if not result:
            skipped += 1
            continue
        predicted = canonicalize((result.get("best_match") or {}).get("type"))
        if predicted is None:
            skipped += 1
            continue
        vector = _probability_vector(result)
        if vector is None:
            skipped += 1
            continue
        y_true.append(row.canonical_type)
        y_pred.append(predicted)
        y_proba.append(vector)

    metrics = score_predictions(y_true, y_pred, y_proba)
    metrics.update(
        {
            "id": definition.id,
            "skipped": skipped,
        }
    )
    return metrics


def evaluate_month(
    year_month: str,
    *,
    data_dir: Optional[Path] = None,
    out_path: Optional[Path] = None,
) -> dict:
    data_dir = Path(data_dir) if data_dir is not None else challenge_dir(year_month)
    rows = load_challenge(data_dir)
    # Stamp the dataset's collection date onto the standings. Without it a
    # score set cannot be reproduced, because the window keeps growing.
    snapshot = load_snapshot_for(data_dir)
    factory = ModelFactory(get_settings())
    models = []
    for definition in listed_definitions():
        classifier = factory.get_classifier(definition.id)
        models.append(score_definition(definition, rows, classifier))

    payload = {
        "month": year_month,
        "month_label": month_label(year_month),
        "eval_window": eval_window(year_month),
        "status": "Finalized",
        "scraped_at": (snapshot or {}).get("scraped_at"),
        "spectra_count": len(rows),
        "models": models,
    }
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        out_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        write_scores(payload)
    return payload


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score listed models on a monthly WISeREP challenge set."
    )
    parser.add_argument(
        "--month",
        required=True,
        help="Challenge month as YYYY-MM (e.g. 2026-07)",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Challenge directory (default: wiserep_scrape/data/<month>)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Score JSON path (default: astrodash/data/leaderboard/<month>.json)",
    )
    return parser.parse_args(argv)


def _print_payload(payload: dict) -> None:
    print(f"Scored {payload['spectra_count']} spectra for {payload['month_label']}")
    for row in payload["models"]:
        acc = row.get("accuracy")
        acc_s = f"{acc:.1f}%" if isinstance(acc, (int, float)) else "—"
        print(f"  {row['id']}: accuracy={acc_s} n={row.get('n_scored')}")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        payload = evaluate_month(args.month, data_dir=args.data, out_path=args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    _print_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
