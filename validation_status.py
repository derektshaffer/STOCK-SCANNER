"""Build a durable gate-by-gate forward validation scoreboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import analyzer_versions as versions
import scanner_ml_ranker as scanner_ml
from historical_listing_universe import (
    load_cached_historical_universes,
    target_replay_dates,
)


OUT_DIR = Path("validation_status")
ANALYZER_CALIBRATION = Path("analyzer_outcomes/calibration.json")
OFFHOURS_SUMMARY = Path("offhours_outcomes/latest_summary.json")
PATH_MODEL_REPORT = Path("outcome_reports/path_target_ml_validation.json")
TIMEFRAME_ML = Path("timeframe_replay/timeframe_ml_validation.json")
UNIVERSE_DIR = Path("universe_snapshots")


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _gate(current, required):
    current = int(current or 0)
    required = int(required or 0)
    return {
        "current": current,
        "required": required,
        "remaining": max(0, required - current),
        "passed": current >= required,
    }


def scanner_live_evidence():
    rows, training_source = scanner_ml.load_training_observations()
    replay_rows = [r for r in rows if r.get("observation_source") == "historical_replay"]
    live_rows = [r for r in rows if r.get("observation_source") != "historical_replay"]
    replay_days = sorted({
        r.get("trading_date") for r in replay_rows if r.get("trading_date")
    })
    replay_end = replay_days[-1] if replay_days else None
    live_after = [
        r for r in live_rows
        if not replay_end
        or (r.get("trading_date") and r.get("trading_date") > replay_end)
    ]
    independent = scanner_ml.independent_confirmation_rows(live_after)
    days = sorted({r.get("trading_date") for r in independent if r.get("trading_date")})
    symbols = sorted({r.get("symbol") for r in independent if r.get("symbol")})
    positives = sum(int(r.get("label") or 0) for r in independent)
    negatives = len(independent) - positives
    gates = {
        "samples": _gate(len(independent), scanner_ml.MIN_LIVE_CONFIRMATION_SAMPLES),
        "days": _gate(len(days), scanner_ml.MIN_LIVE_CONFIRMATION_DAYS),
        "symbols": _gate(len(symbols), scanner_ml.MIN_LIVE_CONFIRMATION_SYMBOLS),
        "positives": _gate(positives, scanner_ml.MIN_LIVE_CONFIRMATION_CLASS_COUNT),
        "negatives": _gate(negatives, scanner_ml.MIN_LIVE_CONFIRMATION_CLASS_COUNT),
    }
    return {
        "training_source": training_source,
        "replay_end_day": replay_end,
        "historical_replay_samples": len(replay_rows),
        "live_samples": len(live_rows),
        "live_confirmation_raw_samples": len(live_after),
        "live_confirmation_samples": len(independent),
        "live_confirmation_days": len(days),
        "live_confirmation_symbols": len(symbols),
        "live_positives": positives,
        "live_negatives": negatives,
        "gates": gates,
        "evidence_gate_ready": all(g["passed"] for g in gates.values()),
        "note": (
            "Passing these count gates only makes the independent live holdout "
            "large enough to evaluate. Production validation still requires the "
            "live AUC/Brier performance gate to pass."
        ),
    }


def scanner_path_target_evidence():
    rows, source = scanner_ml.load_path_research_observations()
    model_report = _load(PATH_MODEL_REPORT)
    path_model = model_report.get("path_model") or {}
    endpoint_model = model_report.get("endpoint_model") or {}
    replay_rows = [
        r for r in rows
        if r.get("observation_source") == "historical_replay"
    ]
    live_rows = [
        r for r in rows
        if r.get("observation_source") != "historical_replay"
    ]
    replay_independent = scanner_ml.independent_confirmation_rows(replay_rows)
    replay_days = sorted({
        r.get("trading_date") for r in replay_independent if r.get("trading_date")
    })
    replay_end = replay_days[-1] if replay_days else None

    live_after = [
        r for r in live_rows
        if not replay_end
        or (r.get("trading_date") and r.get("trading_date") > replay_end)
    ]
    independent = scanner_ml.independent_confirmation_rows(live_after)
    days = sorted({r.get("trading_date") for r in independent if r.get("trading_date")})
    symbols = sorted({r.get("symbol") for r in independent if r.get("symbol")})
    positives = sum(int(r.get("label") or 0) for r in independent)
    negatives = len(independent) - positives
    comparable = [
        r for r in independent
        if r.get("endpoint_label") in (0, 1)
    ]
    disagreements = sum(
        bool(r.get("endpoint_path_disagreement")) for r in comparable
    )
    replay_comparable = [
        r for r in replay_independent
        if r.get("endpoint_label") in (0, 1)
    ]
    replay_disagreements = sum(
        bool(r.get("endpoint_path_disagreement"))
        for r in replay_comparable
    )
    gates = {
        "samples": _gate(len(independent), scanner_ml.MIN_LIVE_CONFIRMATION_SAMPLES),
        "days": _gate(len(days), scanner_ml.MIN_LIVE_CONFIRMATION_DAYS),
        "symbols": _gate(len(symbols), scanner_ml.MIN_LIVE_CONFIRMATION_SYMBOLS),
        "positives": _gate(positives, scanner_ml.MIN_LIVE_CONFIRMATION_CLASS_COUNT),
        "negatives": _gate(negatives, scanner_ml.MIN_LIVE_CONFIRMATION_CLASS_COUNT),
    }
    return {
        "target_description": scanner_ml.PATH_RESEARCH_TARGET_DESCRIPTION,
        "production_influence": False,
        "source": source,
        "historical_replay_samples": len(replay_rows),
        "historical_replay_independent_samples": len(replay_independent),
        "historical_replay_days": len(replay_days),
        "historical_replay_endpoint_comparable": len(replay_comparable),
        "historical_replay_endpoint_path_disagreements": replay_disagreements,
        "historical_replay_disagreement_rate_pct": (
            round(replay_disagreements / len(replay_comparable) * 100.0, 1)
            if replay_comparable else None
        ),
        "replay_end_day": replay_end,
        "live_raw_samples": len(live_rows),
        "live_confirmation_raw_samples": len(live_after),
        "independent_samples": len(independent),
        "days": len(days),
        "symbols": len(symbols),
        "positives": positives,
        "negatives": negatives,
        "endpoint_comparable_samples": len(comparable),
        "endpoint_path_disagreements": disagreements,
        "endpoint_path_disagreement_rate_pct": (
            round(disagreements / len(comparable) * 100.0, 1)
            if comparable else None
        ),
        "gates": gates,
        "comparison_ready": all(g["passed"] for g in gates.values()),
        "historical_model_artifact_found": bool(model_report),
        "path_model_version": model_report.get("path_model_version"),
        "path_model_historical_validated": bool(
            path_model.get("historical_validated")
        ),
        "path_model_status": path_model.get("status"),
        "path_model_walk_forward_auc": path_model.get("walk_forward_auc"),
        "path_model_walk_forward_brier": path_model.get("walk_forward_brier"),
        "path_model_baseline_brier": path_model.get("baseline_brier"),
        "path_model_validation_samples": path_model.get("validation_samples"),
        "path_model_live_confirmation_auc": path_model.get(
            "live_confirmation_auc"
        ),
        "endpoint_model_historical_validated": bool(
            endpoint_model.get("historical_validated")
        ),
        "endpoint_model_status": endpoint_model.get("status"),
        "endpoint_model_walk_forward_auc": endpoint_model.get(
            "walk_forward_auc"
        ),
        "endpoint_model_walk_forward_brier": endpoint_model.get(
            "walk_forward_brier"
        ),
        "endpoint_model_baseline_brier": endpoint_model.get("baseline_brier"),
        "note": (
            "Historical replay can bootstrap research immediately, but promotion "
            "still requires independent live path evidence strictly after the "
            "replay period plus a later model-performance comparison."
        ),
    }


def analyzer_forward_evidence():
    data = _load(ANALYZER_CALIBRATION)
    schema = int(data.get("schema_version") or 0)
    resolved = int(data.get("resolved_60m") or 0)
    return {
        "artifact_found": bool(data),
        "schema_version": schema,
        "required_schema_version": versions.CALIBRATION_SCHEMA_VERSION,
        "schema_current": schema == versions.CALIBRATION_SCHEMA_VERSION,
        "prediction_rows": int(data.get("prediction_rows") or 0),
        "calibration_rows": int(data.get("calibration_rows") or 0),
        "calibration_by_prediction_source": (
            data.get("calibration_by_prediction_source") or {}
        ),
        "untrusted_integrity_rows_excluded": int(
            data.get("untrusted_integrity_rows_excluded") or 0
        ),
        "resolved_60m": resolved,
        "early_read_gate": _gate(resolved, 30),
        "useful_gate": _gate(resolved, 100),
        "stronger_sample_gate": _gate(resolved, 300),
        "calibration_ready": bool(data.get("calibration_ready")),
    }


def offhours_forward_evidence():
    data = _load(OFFHOURS_SUMMARY)
    cohorts = data.get("cohorts") or []

    def resolved(horizon):
        total = 0
        for cohort in cohorts:
            horizons = ((cohort.get("summary") or {}).get("horizons") or {})
            total += int((horizons.get(str(horizon)) or {}).get("resolved") or 0)
        return total

    swing = resolved(5)
    longer = resolved(20)
    return {
        "cohort_count": len(cohorts),
        "swing_5d_resolved": swing,
        "longer_20d_resolved": longer,
        "swing_early_read_gate": _gate(swing, 30),
        "swing_useful_gate": _gate(swing, 100),
        "longer_early_read_gate": _gate(longer, 30),
        "longer_useful_gate": _gate(longer, 100),
    }


def timeframe_ml_evidence():
    data = _load(TIMEFRAME_ML)
    overall = data.get("overall") or {}
    return {
        "artifact_found": bool(data),
        "model_version": data.get("model_version"),
        "status": data.get("status"),
        "historical_validated": bool(data.get("historical_validated")),
        "production_enabled": bool(data.get("production_enabled")),
        "samples": int(data.get("samples") or 0),
        "unique_dates": int(data.get("unique_dates") or 0),
        "unique_symbols": int(data.get("unique_symbols") or 0),
        "model_auc": overall.get("model_auc"),
        "hand_score_auc": overall.get("hand_score_auc"),
        "top_decile_target_rate_lift_pp": overall.get("top_decile_target_rate_lift_pp"),
    }


def universe_coverage():
    rows = []
    if UNIVERSE_DIR.exists():
        for path in sorted(UNIVERSE_DIR.glob("universe_????-??-??.json")):
            data = _load(path)
            if data:
                rows.append(data)
    replay_ready = [r for r in rows if r.get("replay_ready") is True]
    dates = sorted(str(r.get("captured_date_et")) for r in replay_ready if r.get("captured_date_et"))
    return {
        "snapshot_count": len(rows),
        "replay_ready_snapshot_count": len(replay_ready),
        "first_replay_ready_capture_date": dates[0] if dates else None,
        "latest_replay_ready_capture_date": dates[-1] if dates else None,
        "minimum_snapshots_for_point_in_time_replay": 3,
        "activation_gate": _gate(len(replay_ready), 3),
        "note": (
            "A snapshot captured after a session may only be used for later "
            "sessions, never the same replay date."
        ),
    }


def historical_listing_coverage():
    cached = load_cached_historical_universes()
    targets = target_replay_dates()
    target_set = set(targets)
    covered = sorted(day for day in cached if day in target_set)
    latest_backfill = _load(
        Path("historical_universes/alpha_vantage/latest_backfill.json")
    )
    provider = _load(
        Path("historical_universes/alpha_vantage/provider_status.json")
    )
    return {
        "provider_status": provider.get("status") or "unknown",
        "api_key_configured": bool(provider.get("api_key_configured")),
        "target_replay_dates": len(targets),
        "exact_historical_snapshot_count": len(cached),
        "covered_target_dates": len(covered),
        "missing_target_dates": max(0, len(targets) - len(covered)),
        "coverage_pct": (
            round(len(covered) / len(targets) * 100.0, 1)
            if targets else 0.0
        ),
        "first_covered_date": covered[0].isoformat() if covered else None,
        "latest_covered_date": covered[-1].isoformat() if covered else None,
        "latest_backfill_status": latest_backfill.get("status"),
        "latest_backfill_fetched_dates": int(
            latest_backfill.get("fetched_dates") or 0
        ),
        "note": (
            "Exact historical-date listing membership is used only when a "
            "cached snapshot exists for that same replay date. Missing dates "
            "remain explicitly on prospective/current-universe fallback."
        ),
    }


def build_status():
    sections = {
        "scanner_ml_live_confirmation": scanner_live_evidence(),
        "scanner_path_target_shadow": scanner_path_target_evidence(),
        "analyzer_calibration_v9": analyzer_forward_evidence(),
        "offhours_timeframe_forward": offhours_forward_evidence(),
        "swing_timeframe_ml": timeframe_ml_evidence(),
        "point_in_time_universe": universe_coverage(),
        "historical_listing_universe": historical_listing_coverage(),
    }
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "production_claim": (
            "Rules-based Scanner/Analyzer decision support remains usable. "
            "Predictive ML and Swing/Longer-Term probability claims remain gated "
            "until their independent evidence requirements pass."
        ),
    }


def _progress_line(label, gate):
    return (
        f"- **{label}:** {gate['current']}/{gate['required']} "
        + ("✅" if gate["passed"] else f"— {gate['remaining']} remaining")
    )


def render_markdown(payload):
    s = payload["sections"]
    scanner = s["scanner_ml_live_confirmation"]
    path_target = s["scanner_path_target_shadow"]
    analyzer = s["analyzer_calibration_v9"]
    off = s["offhours_timeframe_forward"]
    tf = s["swing_timeframe_ml"]
    universe = s["point_in_time_universe"]
    historical_listing = s["historical_listing_universe"]
    source_diag = analyzer.get("calibration_by_prediction_source") or {}
    source_diag_text = ", ".join(
        (
            f"{source}: {int(values.get('calibration_rows') or 0)} rows/"
            f"{int(values.get('resolved_60m') or 0)} resolved"
            + (
                f", avg Scanner rank {values.get('avg_source_scanner_rank')}"
                if values.get("avg_source_scanner_rank") is not None
                else ""
            )
        )
        for source, values in sorted(source_diag.items())
    ) or "none yet"

    lines = [
        "# Forward Validation Status",
        "",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## Scanner ML — independent live confirmation",
        _progress_line("Samples", scanner["gates"]["samples"]),
        _progress_line("Trading days", scanner["gates"]["days"]),
        _progress_line("Symbols", scanner["gates"]["symbols"]),
        _progress_line("Positive class", scanner["gates"]["positives"]),
        _progress_line("Negative class", scanner["gates"]["negatives"]),
        f"- **Count gate ready:** {'YES' if scanner['evidence_gate_ready'] else 'NO'}",
        f"- Replay end day: {scanner.get('replay_end_day') or '—'}",
        "",
        "## Scanner path target — shadow validation",
        f"- Target: {path_target['target_description']}",
        f"- Historical replay: {path_target['historical_replay_independent_samples']} independent rows "
        f"across {path_target['historical_replay_days']} days",
        f"- Historical endpoint/path disagreement: "
        f"{path_target['historical_replay_endpoint_path_disagreements']}/"
        f"{path_target['historical_replay_endpoint_comparable']} "
        f"({path_target.get('historical_replay_disagreement_rate_pct') if path_target.get('historical_replay_disagreement_rate_pct') is not None else '—'}%)",
        f"- Replay end day: {path_target.get('replay_end_day') or '—'}",
        f"- Path model historical status: "
        f"{path_target.get('path_model_status') or 'not run'}"
        + (
            f" · AUC {path_target.get('path_model_walk_forward_auc')} "
            f"· Brier {path_target.get('path_model_walk_forward_brier')}"
            if path_target.get("path_model_walk_forward_auc") is not None
            else ""
        ),
        f"- Endpoint model historical status: "
        f"{path_target.get('endpoint_model_status') or 'not run'}"
        + (
            f" · AUC {path_target.get('endpoint_model_walk_forward_auc')} "
            f"· Brier {path_target.get('endpoint_model_walk_forward_brier')}"
            if path_target.get("endpoint_model_walk_forward_auc") is not None
            else ""
        ),
        _progress_line("Independent live samples", path_target["gates"]["samples"]),
        _progress_line("Trading days", path_target["gates"]["days"]),
        _progress_line("Symbols", path_target["gates"]["symbols"]),
        _progress_line("Positive class", path_target["gates"]["positives"]),
        _progress_line("Negative class", path_target["gates"]["negatives"]),
        f"- Endpoint/path disagreements: {path_target['endpoint_path_disagreements']}/"
        f"{path_target['endpoint_comparable_samples']} "
        f"({path_target.get('endpoint_path_disagreement_rate_pct') if path_target.get('endpoint_path_disagreement_rate_pct') is not None else '—'}%)",
        f"- **Ready for endpoint-vs-path model comparison:** "
        f"{'YES' if path_target['comparison_ready'] else 'NO'}",
        f"- Production influence: **OFF**",
        "",
        "## Analyzer calibration",
        f"- Schema: {analyzer['schema_version']} / required {analyzer['required_schema_version']} "
        f"{'✅' if analyzer['schema_current'] else '⚠️'}",
        _progress_line("Resolved 60m rows — early read", analyzer["early_read_gate"]),
        _progress_line("Resolved 60m rows — useful", analyzer["useful_gate"]),
        f"- Untrusted rows excluded: {analyzer['untrusted_integrity_rows_excluded']}",
        f"- Calibration provenance: {source_diag_text}",
        "",
        "## Swing / Longer-Term forward cohorts",
        _progress_line("Swing 5-day resolved — early read", off["swing_early_read_gate"]),
        _progress_line("Swing 5-day resolved — useful", off["swing_useful_gate"]),
        _progress_line("Longer-Term 20-day resolved — early read", off["longer_early_read_gate"]),
        _progress_line("Longer-Term 20-day resolved — useful", off["longer_useful_gate"]),
        "",
        "## Swing timeframe ML",
        f"- Status: **{tf.get('status') or 'unknown'}**",
        f"- Historical validated: {'YES' if tf['historical_validated'] else 'NO'}",
        f"- Production enabled: {'YES' if tf['production_enabled'] else 'NO'}",
        f"- Samples: {tf['samples']} across {tf['unique_dates']} dates / {tf['unique_symbols']} symbols",
        f"- Model AUC: {tf.get('model_auc')} · hand-score AUC: {tf.get('hand_score_auc')}",
        f"- Top-decile target-rate lift: {tf.get('top_decile_target_rate_lift_pp')} pp",
        "",
        "## Point-in-time universe coverage",
        _progress_line("Replay-ready nightly snapshots", universe["activation_gate"]),
        f"- First capture: {universe.get('first_replay_ready_capture_date') or '—'}",
        f"- Latest capture: {universe.get('latest_replay_ready_capture_date') or '—'}",
        "",
        "## Historical listing-universe backfill",
        f"- Provider: {historical_listing.get('provider_status') or 'unknown'} "
        f"({'key configured' if historical_listing.get('api_key_configured') else 'key missing'})",
        f"- Exact replay dates covered: {historical_listing['covered_target_dates']}/"
        f"{historical_listing['target_replay_dates']} "
        f"({historical_listing['coverage_pct']}%)",
        f"- Missing exact-date memberships: {historical_listing['missing_target_dates']}",
        f"- First covered date: {historical_listing.get('first_covered_date') or '—'}",
        f"- Latest covered date: {historical_listing.get('latest_covered_date') or '—'}",
        f"- Latest backfill status: {historical_listing.get('latest_backfill_status') or 'not started'}",
        "",
        "## Interpretation",
        payload["production_claim"],
        "",
    ]
    return "\n".join(lines)


def main():
    payload = build_status()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "latest.md").write_text(render_markdown(payload), encoding="utf-8")
    print(render_markdown(payload))


if __name__ == "__main__":
    main()
