"""Research-only validation for the Scanner path-aware 60-minute target."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scanner_ml_ranker as sm


OUTPUT = Path("outcome_reports/path_target_ml_validation.json")


def build_report():
    endpoint_rows, endpoint_source = sm.load_training_observations()
    _endpoint_model, endpoint_meta = sm._validation_and_model(endpoint_rows)

    path_rows, path_source = sm.load_path_research_observations()
    path_meta = sm.validate_path_research_model(path_rows)

    endpoint_replay_rows = [
        row for row in endpoint_rows
        if row.get("observation_source") == "historical_replay"
    ]
    endpoint_independent = sm.independent_confirmation_rows(endpoint_replay_rows)

    path_replay_rows = [
        row for row in path_rows
        if row.get("observation_source") == "historical_replay"
    ]
    path_independent = sm.independent_confirmation_rows(path_replay_rows)

    comparable = [
        row for row in path_independent
        if row.get("endpoint_label") in (0, 1)
    ]
    disagreements = sum(
        bool(row.get("endpoint_path_disagreement"))
        for row in comparable
    )
    path_positives = sum(int(row.get("label") or 0) for row in path_independent)
    endpoint_positives = sum(
        int(row.get("endpoint_label") or 0)
        for row in comparable
    )

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "production_influence": False,
        "can_change_scanner_rank": False,
        "can_change_analyzer_trade_plan": False,
        "path_target": sm.PATH_RESEARCH_TARGET_DESCRIPTION,
        "path_model_version": sm.PATH_RESEARCH_MODEL_VERSION,
        "path_source": path_source,
        "endpoint_source": endpoint_source,
        "historical_replay": {
            "path_raw_rows": len(path_replay_rows),
            "path_independent_rows": len(path_independent),
            "path_positives": path_positives,
            "path_negatives": len(path_independent) - path_positives,
            "endpoint_comparable_rows": len(comparable),
            "endpoint_positives_on_comparable": endpoint_positives,
            "endpoint_negatives_on_comparable": len(comparable) - endpoint_positives,
            "endpoint_path_disagreements": disagreements,
            "endpoint_path_disagreement_rate_pct": (
                round(disagreements / len(comparable) * 100.0, 2)
                if comparable else None
            ),
        },
        "path_model": path_meta,
        "endpoint_model": {
            "target": sm.TARGET_DESCRIPTION,
            "historical_replay_raw_rows": len(endpoint_replay_rows),
            "historical_replay_independent_rows": len(endpoint_independent),
            "historical_validated": bool(
                endpoint_meta.get("historical_validated")
            ),
            "validated": bool(endpoint_meta.get("validated")),
            "status": endpoint_meta.get("status"),
            "validation_samples": endpoint_meta.get("validation_samples"),
            "walk_forward_auc": endpoint_meta.get("walk_forward_auc"),
            "walk_forward_brier": endpoint_meta.get("walk_forward_brier"),
            "baseline_brier": endpoint_meta.get("baseline_brier"),
            "live_confirmation_samples": endpoint_meta.get(
                "live_confirmation_samples"
            ),
            "live_confirmation_auc": endpoint_meta.get(
                "live_confirmation_auc"
            ),
        },
    }


def main():
    report = build_report()
    replay = report["historical_replay"]
    if int(replay.get("path_raw_rows") or 0) <= 0:
        raise RuntimeError("Historical replay produced no path-labeled rows.")
    if int(replay.get("path_independent_rows") or 0) <= 0:
        raise RuntimeError("Historical replay produced no independent path rows.")

    assert report["research_only"] is True
    assert report["production_influence"] is False
    assert report["can_change_scanner_rank"] is False
    assert report["can_change_analyzer_trade_plan"] is False

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    path_meta = report.get("path_model") or {}
    endpoint_meta = report.get("endpoint_model") or {}
    print("PATH_REPLAY_RAW=" + str(replay.get("path_raw_rows")))
    print("PATH_REPLAY_INDEPENDENT=" + str(replay.get("path_independent_rows")))
    print("PATH_REPLAY_POSITIVES=" + str(replay.get("path_positives")))
    print(
        "PATH_ENDPOINT_DISAGREEMENTS="
        + str(replay.get("endpoint_path_disagreements"))
    )
    print(
        "PATH_ENDPOINT_DISAGREEMENT_RATE_PCT="
        + str(replay.get("endpoint_path_disagreement_rate_pct"))
    )
    print(
        "PATH_MODEL_HISTORICAL_VALIDATED="
        + str(bool(path_meta.get("historical_validated"))).lower()
    )
    print("PATH_MODEL_STATUS=" + str(path_meta.get("status")))
    print("PATH_MODEL_AUC=" + str(path_meta.get("walk_forward_auc")))
    print("PATH_MODEL_BRIER=" + str(path_meta.get("walk_forward_brier")))
    print(
        "ENDPOINT_MODEL_HISTORICAL_VALIDATED="
        + str(bool(endpoint_meta.get("historical_validated"))).lower()
    )
    print("ENDPOINT_MODEL_STATUS=" + str(endpoint_meta.get("status")))
    print("ENDPOINT_MODEL_AUC=" + str(endpoint_meta.get("walk_forward_auc")))
    print("ENDPOINT_MODEL_BRIER=" + str(endpoint_meta.get("walk_forward_brier")))


if __name__ == "__main__":
    main()
