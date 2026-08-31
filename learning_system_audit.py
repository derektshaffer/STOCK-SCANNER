from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT_DIR = ROOT / "outcome_reports"
DEFAULT_OPPORTUNITY_REPORT_DIR = ROOT / "opportunity_reports"
DEFAULT_OUTPUT_DIR = ROOT / "learning_audits"


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def audit_source_contracts(root: Path = ROOT) -> list[dict[str, Any]]:
    """Audit the learning specification, not just implementation correctness."""
    scanner_ml = _read(root / "scanner_ml_ranker.py")
    scorer = _read(root / "score_outcomes.py")
    opportunity_scorer = _read(root / "score_opportunity_outcomes.py")
    scanner = _read(root / "stock_scanner.py")
    live_journal = _read(root / "scanner_live_journal.py")
    scanner_runtime = _read(root / "scanner_runtime.py")
    workflow = _read(root / ".github/workflows/stock-scanner.yml")
    app = _read(root / "app.py")

    findings: list[dict[str, Any]] = []

    narrow_target = (
        ">= +3% at 60 minutes" in scanner_ml
        or re.search(r"return_60\s*>=\s*3(?:\.0)?", scanner_ml) is not None
    )
    if narrow_target:
        findings.append(
            {
                "id": "single_endpoint_primary_target",
                "severity": "high",
                "status": "open",
                "category": "objective",
                "evidence": "Scanner ML primary label is a >= +3% 60-minute endpoint target.",
                "risk": (
                    "A large interim winner can be labeled negative if it gives back "
                    "enough of the move by the 60-minute endpoint."
                ),
                "recommended_action": (
                    "Keep the endpoint target for continuity, but add shadow path/MFE "
                    "targets and validate a multi-objective ranking layer."
                ),
            }
        )

    regular_only = (
        'payload.get("mode") != "regular_market_session"' in scorer
        or "load_regular_session_scans" in scorer
    )
    shadow_extended_capture = (
        "extended_market_session" in opportunity_scorer
        and 'tradier_session_filter="all"' in opportunity_scorer
        and "research_mfe_60m_pct" in opportunity_scorer
    )
    if regular_only and not shadow_extended_capture:
        findings.append(
            {
                "id": "extended_session_outcomes_excluded",
                "severity": "high",
                "status": "open",
                "category": "coverage",
                "evidence": "Durable Scanner outcome scoring filters to regular-session scans and no shadow extended-session outcome collector was detected.",
                "risk": (
                    "Premarket/after-hours discoveries can be visible to the Scanner "
                    "but absent from the learning feedback loop."
                ),
                "recommended_action": (
                    "Collect extended-session outcomes in shadow-only fields and keep "
                    "the current production model explicitly regular-session gated."
                ),
            }
        )
    elif regular_only and shadow_extended_capture:
        findings.append(
            {
                "id": "extended_session_shadow_capture",
                "severity": "info",
                "status": "resolved_shadow",
                "category": "coverage",
                "evidence": (
                    "Production Scanner ML remains regular-session gated while a "
                    "separate shadow collector measures premarket/after-hours paths."
                ),
                "risk": "No current production behavior change; extended data still requires validation before promotion.",
                "recommended_action": "Accumulate shadow evidence and validate session-specific targets before any production influence.",
            }
        )

    mfe_present = "mfe_60m_pct" in scorer
    mfe_targeted = False
    if "mfe_60m_pct" in scanner_ml:
        start = scanner_ml.find("mfe_60m_pct")
        mfe_targeted = "label" in scanner_ml[start : start + 600]
    if mfe_present and not mfe_targeted:
        findings.append(
            {
                "id": "path_information_not_primary_target",
                "severity": "medium",
                "status": "open",
                "category": "objective",
                "evidence": "Outcome scoring records MFE/MAE, while Scanner ML is endpoint-label driven.",
                "risk": "Useful path information is collected but can be underused by the ranking model.",
                "recommended_action": "Add MFE/MAE and threshold-before-failure shadow targets.",
            }
        )

    top_match = re.search(r"SCAN_LOG_TOP\s*=\s*(\d+)", scanner)
    if top_match:
        top_n = int(top_match.group(1))
        findings.append(
            {
                "id": "top_n_observation_censoring",
                "severity": "medium",
                "status": "watch",
                "category": "coverage",
                "evidence": f"Only the top {top_n} Scanner rows are written to each durable scan snapshot.",
                "risk": (
                    "The system has less evidence about near-misses and false negatives "
                    "that ranked below the durable logging cutoff."
                ),
                "recommended_action": (
                    "Retain a bounded shadow sample below the cutoff so the opportunity "
                    "audit can study explosive winners the production ranking missed."
                ),
            }
        )

    durable_30m = bool(re.search(r"cron:\s*['\"]7,37", workflow))
    app_2m = "2-minute" in app or "120" in app
    live_journal_present = (
        "capture_live_scan" in live_journal
        and "BUCKET_MINUTES = 15" in live_journal
        and "learning-journal" in live_journal
        and "SCANNER_LIVE_JOURNAL_ENABLED" in scanner_runtime
        and "capture_live_scan(rows, now_et)" in scanner
    )
    if durable_30m and app_2m and not live_journal_present:
        findings.append(
            {
                "id": "live_vs_durable_cadence_gap",
                "severity": "high",
                "status": "open",
                "category": "capture",
                "evidence": (
                    "The app targets a roughly 2-minute live scan cadence while the "
                    "durable GitHub collector is scheduled around every 30 minutes."
                ),
                "risk": (
                    "A high-value live observation can disappear before a durable "
                    "learning snapshot records that exact state."
                ),
                "recommended_action": (
                    "Add a durable, deduplicated high-value observation journal for "
                    "the live app stream."
                ),
            }
        )
    elif durable_30m and app_2m and live_journal_present:
        findings.append(
            {
                "id": "high_frequency_live_journal",
                "severity": "info",
                "status": "resolved_shadow",
                "category": "capture",
                "evidence": (
                    "Every live app scan is inspected by a bounded 15-minute-bucket "
                    "learning journal, with periodic durable sync to the isolated "
                    "learning-journal branch."
                ),
                "risk": (
                    "This reduces the 2-minute-vs-30-minute capture gap, but the "
                    "journal remains shadow data and still requires nightly outcome "
                    "resolution and later validation."
                ),
                "recommended_action": (
                    "Continue monitoring journal sync health and use de-correlated "
                    "rows for any later validation."
                ),
            }
        )

    return findings


def _score_bucket(value):
    value = _num(value)
    if value is None:
        return "unknown"
    if value >= 80:
        return "80+"
    if value >= 70:
        return "70-79"
    if value >= 60:
        return "60-69"
    if value >= 50:
        return "50-59"
    return "<50"


def _rank_bucket(value):
    value = _num(value)
    if value is None:
        return "unknown"
    rank = int(value)
    if rank <= 5:
        return "1-5"
    if rank <= 10:
        return "6-10"
    if rank <= 20:
        return "11-20"
    if rank <= 30:
        return "21-30"
    return "31+"


def _group_summary(rows):
    n = len(rows)
    mfe = [
        _num(row.get("research_mfe_60m_pct"))
        for row in rows
        if _num(row.get("research_mfe_60m_pct")) is not None
    ]
    ret = [
        _num(row.get("research_return_60m_pct"))
        for row in rows
        if _num(row.get("research_return_60m_pct")) is not None
    ]
    up5 = [
        row.get("research_up_5_60m_before_stop")
        for row in rows
        if row.get("research_up_5_60m_before_stop") is not None
    ]
    up10 = [
        row.get("research_up_10_60m_before_stop")
        for row in rows
        if row.get("research_up_10_60m_before_stop") is not None
    ]
    stop3 = [
        row
        for row in rows
        if row.get("research_failure_stop_60m_hit") is True
    ]
    return {
        "n": n,
        "mfe_n": len(mfe),
        "median_mfe_60m_pct": round(median(mfe), 3) if mfe else None,
        "average_mfe_60m_pct": round(mean(mfe), 3) if mfe else None,
        "endpoint_positive_rate_pct": (
            round(sum(value > 0 for value in ret) / len(ret) * 100.0, 2)
            if ret
            else None
        ),
        "up5_before_stop_rate_pct": (
            round(sum(value is True for value in up5) / len(up5) * 100.0, 2)
            if up5
            else None
        ),
        "up10_before_stop_rate_pct": (
            round(sum(value is True for value in up10) / len(up10) * 100.0, 2)
            if up10
            else None
        ),
        "failure_stop_hit_rate_pct": (
            round(len(stop3) / n * 100.0, 2) if n else None
        ),
    }


def audit_observations(observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    raw_rows = [row for row in observations if isinstance(row, dict)]

    # The same durable regular-session observation can appear in both the
    # legacy endpoint report and the richer shadow opportunity report. Prefer
    # the richer row so empirical counts are not inflated.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw_rows:
        key = (
            str(row.get("symbol") or "").upper().strip(),
            str(row.get("scan_time_et") or ""),
        )
        old = deduped.get(key)
        if old is None:
            deduped[key] = row
            continue
        old_rich = old.get("research_mfe_60m_pct") is not None
        new_rich = row.get("research_mfe_60m_pct") is not None
        if new_rich and not old_rich:
            deduped[key] = row

    rows = list(deduped.values())
    resolved = []
    contradictions = []
    explosive = []
    high_score_contradictions = []
    phase_counts: dict[str, int] = {}
    contradiction_phase_counts: dict[str, int] = {}

    research_rows = [
        row
        for row in rows
        if row.get("research_mfe_60m_pct") is not None
    ]
    complete_research_rows = [
        row
        for row in research_rows
        if row.get("research_horizon_60m_complete") is not False
    ]

    for row in rows:
        ret60 = _num(
            row.get("research_return_60m_pct")
            if row.get("research_return_60m_pct") is not None
            else row.get("return_60m_pct")
        )
        mfe60 = _num(
            row.get("research_mfe_60m_pct")
            if row.get("research_mfe_60m_pct") is not None
            else row.get("mfe_60m_pct")
        )
        mae60 = _num(
            row.get("research_mae_60m_pct")
            if row.get("research_mae_60m_pct") is not None
            else row.get("mae_60m_pct")
        )
        score = _num(row.get("opportunity_score"))
        if score is None:
            score = _num(row.get("score"))
        phase = str(row.get("session_phase") or "unknown").lower().strip() or "unknown"
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

        if ret60 is not None:
            resolved.append(row)

        if ret60 is not None and mfe60 is not None and mfe60 >= 5.0 and ret60 < 3.0:
            item = {
                "symbol": str(row.get("symbol") or ""),
                "scan_time_et": row.get("scan_time_et"),
                "session_phase": phase,
                "score": score,
                "rank": _num(row.get("rank")),
                "return_60m_pct": ret60,
                "mfe_60m_pct": mfe60,
                "mae_60m_pct": mae60,
                "source": (
                    "shadow_opportunity"
                    if row.get("research_mfe_60m_pct") is not None
                    else "production_outcome"
                ),
            }
            contradictions.append(item)
            contradiction_phase_counts[phase] = (
                contradiction_phase_counts.get(phase, 0) + 1
            )
            if mfe60 >= 10.0:
                explosive.append(item)
            if score is not None and score >= 70.0:
                high_score_contradictions.append(item)

    denominator = len(
        [
            row
            for row in rows
            if _num(
                row.get("research_return_60m_pct")
                if row.get("research_return_60m_pct") is not None
                else row.get("return_60m_pct")
            )
            is not None
            and _num(
                row.get("research_mfe_60m_pct")
                if row.get("research_mfe_60m_pct") is not None
                else row.get("mfe_60m_pct")
            )
            is not None
        ]
    )

    by_session: dict[str, list[dict[str, Any]]] = {}
    by_score: dict[str, list[dict[str, Any]]] = {}
    by_rank: dict[str, list[dict[str, Any]]] = {}
    for row in complete_research_rows:
        phase = str(row.get("session_phase") or "unknown").lower().strip() or "unknown"
        by_session.setdefault(phase, []).append(row)
        score = (
            row.get("opportunity_score")
            if row.get("opportunity_score") is not None
            else row.get("score")
        )
        by_score.setdefault(_score_bucket(score), []).append(row)
        by_rank.setdefault(_rank_bucket(row.get("rank")), []).append(row)

    high_rank_winners = []
    high_rank_failures = []
    low_rank_explosive = []
    filter_missed_winners: dict[str, int] = {}
    for row in complete_research_rows:
        rank = _num(row.get("rank"))
        up3 = row.get("research_up_3_60m_before_stop")
        up5 = row.get("research_up_5_60m_before_stop")
        up10 = row.get("research_up_10_60m_before_stop")
        stop_hit = row.get("research_failure_stop_60m_hit") is True
        mfe = _num(row.get("research_mfe_60m_pct"))

        if rank is not None and rank <= 10 and up5 is True:
            high_rank_winners.append(row)
        if rank is not None and rank <= 10 and stop_hit and up3 is not True:
            high_rank_failures.append(row)
        if (
            (rank is not None and rank > 30)
            or str(row.get("sample_role") or "") == "control"
        ) and (up10 is True or (mfe is not None and mfe >= 10.0)):
            low_rank_explosive.append(row)
            snapshot = row.get("signal_snapshot") or {}
            failed = (
                snapshot.get("failed_filters")
                if isinstance(snapshot, dict)
                else None
            ) or []
            for reason in failed:
                key = str(reason)
                filter_missed_winners[key] = filter_missed_winners.get(key, 0) + 1

    contradiction_symbols = {
        str(row.get("symbol") or "").upper().strip()
        for row in contradictions
        if str(row.get("symbol") or "").strip()
    }
    low_rank_symbols = {
        str(row.get("symbol") or "").upper().strip()
        for row in low_rank_explosive
        if str(row.get("symbol") or "").strip()
    }

    return {
        "observations_total_raw": len(raw_rows),
        "observations_total": len(rows),
        "resolved_60m": len(resolved),
        "research_observations": len(research_rows),
        "complete_research_60m": len(complete_research_rows),
        "session_phase_counts": phase_counts,
        "path_endpoint_comparable_n": denominator,
        "endpoint_label_contradictions_n": len(contradictions),
        "endpoint_label_contradiction_symbols": len(contradiction_symbols),
        "endpoint_label_contradiction_rate_pct": (
            round(len(contradictions) / denominator * 100.0, 2)
            if denominator
            else None
        ),
        "contradictions_by_session_phase": contradiction_phase_counts,
        "explosive_mfe_endpoint_misses_n": len(explosive),
        "high_score_endpoint_misses_n": len(high_score_contradictions),
        "high_rank_up5_before_stop_n": len(high_rank_winners),
        "high_rank_failure_before_up3_n": len(high_rank_failures),
        "low_rank_or_control_explosive_n": len(low_rank_explosive),
        "low_rank_or_control_explosive_symbols": len(low_rank_symbols),
        "missed_winner_failed_filters": dict(
            sorted(
                filter_missed_winners.items(),
                key=lambda item: (-item[1], item[0]),
            )[:20]
        ),
        "by_session_phase": {
            key: _group_summary(value)
            for key, value in sorted(by_session.items())
        },
        "by_score_bucket": {
            key: _group_summary(value)
            for key, value in sorted(by_score.items())
        },
        "by_rank_bucket": {
            key: _group_summary(value)
            for key, value in sorted(by_rank.items())
        },
        "examples": sorted(
            contradictions,
            key=lambda row: (
                -(row.get("mfe_60m_pct") or 0.0),
                -(row.get("score") or 0.0),
            ),
        )[:20],
        "low_rank_explosive_examples": [
            {
                "symbol": row.get("symbol"),
                "scan_time_et": row.get("scan_time_et"),
                "session_phase": row.get("session_phase"),
                "rank": row.get("rank"),
                "score": (
                    row.get("opportunity_score")
                    if row.get("opportunity_score") is not None
                    else row.get("score")
                ),
                "mfe_60m_pct": row.get("research_mfe_60m_pct"),
                "up10_before_stop": row.get("research_up_10_60m_before_stop"),
                "sample_role": row.get("sample_role"),
            }
            for row in sorted(
                low_rank_explosive,
                key=lambda item: _num(item.get("research_mfe_60m_pct")) or -999,
                reverse=True,
            )[:20]
        ],
    }

def load_outcome_observations(report_dir: Path = DEFAULT_REPORT_DIR) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    report_count = 0
    if not report_dir.exists():
        return rows, report_count

    for path in sorted(report_dir.glob("outcomes_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        report_count += 1
        for row in payload.get("observations") or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows, report_count



def load_opportunity_observations(
    report_dir: Path = DEFAULT_OPPORTUNITY_REPORT_DIR,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    report_count = 0
    if not report_dir.exists():
        return rows, report_count

    for path in sorted(report_dir.glob("opportunity_outcomes_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        report_count += 1
        for row in payload.get("observations") or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows, report_count


def build_hypotheses(source_findings: list[dict[str, Any]], empirical: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate testable shadow hypotheses only when minimum evidence exists."""
    hypotheses: list[dict[str, Any]] = []

    contradiction_n = int(empirical.get("endpoint_label_contradictions_n") or 0)
    contradiction_symbols = int(
        empirical.get("endpoint_label_contradiction_symbols") or 0
    )
    comparable = int(empirical.get("path_endpoint_comparable_n") or 0)
    contradiction_rate = _num(
        empirical.get("endpoint_label_contradiction_rate_pct")
    ) or 0.0

    if (
        comparable >= 20
        and contradiction_n >= 5
        and contradiction_symbols >= 3
        and contradiction_rate >= 10.0
    ):
        hypotheses.append(
            {
                "id": "path_target_candidate",
                "status": "shadow_candidate",
                "statement": (
                    "A path-aware target using MFE/MAE and threshold-before-failure "
                    "may preserve useful momentum discoveries that the 60-minute "
                    "endpoint target labels negative."
                ),
                "generated_from": {
                    "comparable_n": comparable,
                    "contradictions_n": contradiction_n,
                    "distinct_symbols": contradiction_symbols,
                    "contradiction_rate_pct": contradiction_rate,
                },
                "production_influence": False,
            }
        )

    session_stats = empirical.get("by_session_phase") or {}
    eligible_sessions = {
        phase: stats
        for phase, stats in session_stats.items()
        if int((stats or {}).get("n") or 0) >= 20
        and _num((stats or {}).get("up5_before_stop_rate_pct")) is not None
    }
    if len(eligible_sessions) >= 2:
        rates = {
            phase: float(stats["up5_before_stop_rate_pct"])
            for phase, stats in eligible_sessions.items()
        }
        spread = max(rates.values()) - min(rates.values())
        if spread >= 15.0:
            hypotheses.append(
                {
                    "id": "session_specific_calibration_candidate",
                    "status": "shadow_candidate",
                    "statement": (
                        "Momentum continuation appears materially different by market "
                        "session; premarket, regular, and after-hours may need separate "
                        "calibration or models."
                    ),
                    "generated_from": {
                        "up5_before_stop_rates_pct": rates,
                        "max_rate_spread_pct": round(spread, 2),
                    },
                    "production_influence": False,
                }
            )

    low_rank_n = int(empirical.get("low_rank_or_control_explosive_n") or 0)
    low_rank_symbols = int(
        empirical.get("low_rank_or_control_explosive_symbols") or 0
    )
    if low_rank_n >= 5 and low_rank_symbols >= 3:
        hypotheses.append(
            {
                "id": "missed_explosive_filter_candidate",
                "status": "shadow_candidate",
                "statement": (
                    "A recurring class of low-ranked/control observations later makes "
                    "large favorable moves; inspect their failed filters and feature "
                    "patterns for a false-negative discovery rule."
                ),
                "generated_from": {
                    "examples_n": low_rank_n,
                    "distinct_symbols": low_rank_symbols,
                    "failed_filter_counts": empirical.get(
                        "missed_winner_failed_filters"
                    )
                    or {},
                },
                "production_influence": False,
            }
        )

    # Detect obvious non-monotonic score behavior with decent sample support.
    score_stats = empirical.get("by_score_bucket") or {}
    ordered = ["50-59", "60-69", "70-79", "80+"]
    eligible = []
    for bucket in ordered:
        stats = score_stats.get(bucket) or {}
        n = int(stats.get("n") or 0)
        rate = _num(stats.get("up5_before_stop_rate_pct"))
        if n >= 30 and rate is not None:
            eligible.append((bucket, n, rate))
    for lower, higher in zip(eligible, eligible[1:]):
        if lower[2] >= higher[2] + 10.0:
            hypotheses.append(
                {
                    "id": "score_monotonicity_candidate",
                    "status": "shadow_candidate",
                    "statement": (
                        "A lower Scanner score bucket is outperforming a higher bucket "
                        "on +5%-before-stop outcomes, suggesting score weighting or "
                        "calibration may be misordered."
                    ),
                    "generated_from": {
                        "lower_bucket": lower[0],
                        "lower_n": lower[1],
                        "lower_rate_pct": lower[2],
                        "higher_bucket": higher[0],
                        "higher_n": higher[1],
                        "higher_rate_pct": higher[2],
                    },
                    "production_influence": False,
                }
            )
            break

    return hypotheses

def run_audit(
    root: Path = ROOT,
    report_dir: Path = DEFAULT_REPORT_DIR,
    opportunity_report_dir: Path = DEFAULT_OPPORTUNITY_REPORT_DIR,
) -> dict[str, Any]:
    source_findings = audit_source_contracts(root)
    production_rows, report_count = load_outcome_observations(report_dir)
    opportunity_rows, opportunity_report_count = load_opportunity_observations(
        opportunity_report_dir
    )
    empirical = audit_observations(production_rows + opportunity_rows)
    hypotheses = build_hypotheses(source_findings, empirical)

    return {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_type": "learning_objective_and_opportunity",
        "production_changes_made": False,
        "outcome_reports_loaded": report_count,
        "opportunity_reports_loaded": opportunity_report_count,
        "source_findings": source_findings,
        "empirical_opportunity_audit": empirical,
        "hypotheses": hypotheses,
        "policy": (
            "Hypotheses are shadow/research candidates only. This audit may identify "
            "improvements but cannot promote a rule/model into production."
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    findings = payload.get("source_findings") or []
    empirical = payload.get("empirical_opportunity_audit") or {}
    hypotheses = payload.get("hypotheses") or []

    lines = [
        "# Learning Objective / Opportunity Audit",
        "",
        f"Generated: **{payload.get('generated_at_utc')}**",
        "",
        "## Source/specification findings",
        "",
    ]
    if not findings:
        lines.append("- No source-level learning-objective gaps detected by the current rules.")
    for row in findings:
        lines.extend(
            [
                f"### {str(row.get('severity') or '').upper()} — {row.get('id')}",
                f"- Evidence: {row.get('evidence')}",
                f"- Risk: {row.get('risk')}",
                f"- Recommended action: {row.get('recommended_action')}",
                "",
            ]
        )

    lines.extend(
        [
            "## Empirical opportunity audit",
            "",
            f"- Production outcome reports loaded: **{payload.get('outcome_reports_loaded', 0)}**",
            f"- Shadow opportunity reports loaded: **{payload.get('opportunity_reports_loaded', 0)}**",
            f"- Observations: **{empirical.get('observations_total', 0)}**",
            f"- Session counts: **{empirical.get('session_phase_counts', {})}**",
            f"- Comparable path/endpoint observations: **{empirical.get('path_endpoint_comparable_n', 0)}**",
            f"- MFE >= +5% but 60m endpoint < +3%: **{empirical.get('endpoint_label_contradictions_n', 0)}**",
            f"- MFE >= +10% but endpoint target missed: **{empirical.get('explosive_mfe_endpoint_misses_n', 0)}**",
            f"- High-score (>=70) endpoint/path contradictions: **{empirical.get('high_score_endpoint_misses_n', 0)}**",
            f"- Top-10 observations reaching +5% before -3%: **{empirical.get('high_rank_up5_before_stop_n', 0)}**",
            f"- Top-10 failures before +3%: **{empirical.get('high_rank_failure_before_up3_n', 0)}**",
            f"- Rank>30/control explosive outcomes: **{empirical.get('low_rank_or_control_explosive_n', 0)}**",
            f"- Session outcome groups: **{empirical.get('by_session_phase', {})}**",
            f"- Score-bucket outcome groups: **{empirical.get('by_score_bucket', {})}**",
            "",
            "## Shadow hypotheses",
            "",
        ]
    )
    if not hypotheses:
        lines.append("- No hypothesis generated from currently available evidence.")
    for row in hypotheses:
        lines.append(f"- **{row.get('id')}** — {row.get('statement')} ({row.get('status')})")

    lines.extend(
        [
            "",
            "> These hypotheses are research-only. They cannot alter production ranking or trade decisions without separate validation and later live confirmation.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output_dir: Path = DEFAULT_OUTPUT_DIR) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest_learning_audit.json"
    md_path = output_dir / "latest_learning_audit.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument(
        "--opportunity-report-dir",
        default=str(DEFAULT_OPPORTUNITY_REPORT_DIR),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    payload = run_audit(
        ROOT,
        Path(args.report_dir),
        Path(args.opportunity_report_dir),
    )
    json_path, md_path = write_outputs(payload, Path(args.output_dir))
    print(f"Learning audit JSON: {json_path}")
    print(f"Learning audit Markdown: {md_path}")
    open_findings = [
        row
        for row in (payload.get("source_findings") or [])
        if row.get("status") in {"open", "watch"}
    ]
    print("Open source findings: " + str(len(open_findings)))
    empirical = payload.get("empirical_opportunity_audit") or {}
    print(
        "Endpoint/path contradictions: "
        + str(empirical.get("endpoint_label_contradictions_n", 0))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
