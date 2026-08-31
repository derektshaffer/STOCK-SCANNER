from __future__ import annotations

import csv
import json
from bisect import bisect_left
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

import score_outcomes as so

VERSION = "1.0"
HORIZONS_MINUTES = (15, 30, 60, 120)
UPSIDE_THRESHOLDS_PCT = (3, 5, 10, 20)
FAILURE_STOP_PCT = 3.0
REPORT_DIR = Path("opportunity_reports")


def _session_phase(scan, scan_time):
    phase = str(scan.get("session_phase") or "").lower().strip()
    if phase in {"premarket", "regular", "afterhours"}:
        return phase
    minutes = scan_time.hour * 60 + scan_time.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "afterhours"
    return "closed"


def load_active_session_scans(target_date, artifacts):
    """Load regular + supported extended-session scans for shadow research."""
    all_scans = []
    seen_ids = set()

    for artifact in artifacts:
        try:
            payloads = so.extract_scan_payloads(artifact)
        except Exception as exc:
            print(f"WARN could not read artifact {artifact.get('name')}: {exc}")
            continue

        for payload in payloads:
            scan_id = payload.get("scan_id")
            if not scan_id or scan_id in seen_ids:
                continue
            seen_ids.add(scan_id)

            scan_time = so.parse_iso(payload.get("scan_time_et"))
            if scan_time is None:
                continue
            scan_time = scan_time.astimezone(so.ET)
            if scan_time.date() != target_date:
                continue

            phase = _session_phase(payload, scan_time)
            if phase not in {"premarket", "regular", "afterhours"}:
                continue
            if payload.get("mode") not in {
                "regular_market_session",
                "extended_market_session",
            }:
                continue

            payload = dict(payload)
            payload["session_phase"] = phase
            all_scans.append(payload)

    all_scans.sort(key=lambda row: row.get("scan_time_et") or "")
    return all_scans


def opportunity_path_metrics(
    indexed_symbol,
    scan_time,
    session_end,
    entry_price,
    *,
    horizon_minutes,
    thresholds=UPSIDE_THRESHOLDS_PCT,
    failure_stop_pct=FAILURE_STOP_PCT,
):
    """Measure the full future path without stopping after the first barrier."""
    result = {
        "horizon_minutes": horizon_minutes,
        "horizon_complete": False,
        "bars_seen": 0,
        "mfe_pct": None,
        "mae_pct": None,
        "time_to_peak_minutes": None,
        "time_to_trough_minutes": None,
        "failure_stop_pct": float(failure_stop_pct),
        "failure_stop_hit": False,
        "failure_stop_time_minutes": None,
    }
    for threshold in thresholds:
        key = str(int(threshold))
        result[f"up_{key}_hit"] = False
        result[f"up_{key}_time_minutes"] = None
        result[f"up_{key}_before_stop"] = None

    if not indexed_symbol or entry_price in (None, 0):
        return result

    requested_end = scan_time + timedelta(minutes=horizon_minutes)
    end_time = min(requested_end, session_end)
    result["horizon_complete"] = requested_end <= session_end

    entry_price = float(entry_price)
    bars = indexed_symbol.get("bars") or []
    times = indexed_symbol.get("times") or []
    start_pos = bisect_left(times, scan_time + timedelta(minutes=1))

    mfe = 0.0
    mae = 0.0
    peak_time = None
    trough_time = None
    stop_time = None
    threshold_times = {float(threshold): None for threshold in thresholds}

    for bar in bars[start_pos:]:
        ts = bar["time"]
        if ts > end_time or ts.date() != scan_time.date():
            break

        high = float(bar["high"])
        low = float(bar["low"])
        result["bars_seen"] += 1

        high_ret = (high / entry_price - 1.0) * 100.0
        low_ret = (low / entry_price - 1.0) * 100.0

        if high_ret > mfe:
            mfe = high_ret
            peak_time = ts
        if low_ret < mae:
            mae = low_ret
            trough_time = ts

        # Same-bar ambiguity is conservative: a failure stop is treated as
        # occurring before an upside threshold if both first appear in one bar.
        if stop_time is None and low_ret <= -float(failure_stop_pct):
            stop_time = ts

        for threshold in thresholds:
            threshold = float(threshold)
            if threshold_times[threshold] is None and high_ret >= threshold:
                threshold_times[threshold] = ts

    if result["bars_seen"] == 0:
        return result

    result["mfe_pct"] = round(mfe, 3)
    result["mae_pct"] = round(mae, 3)
    if peak_time is not None:
        result["time_to_peak_minutes"] = round(
            max(0.0, (peak_time - scan_time).total_seconds() / 60.0), 2
        )
    if trough_time is not None:
        result["time_to_trough_minutes"] = round(
            max(0.0, (trough_time - scan_time).total_seconds() / 60.0), 2
        )

    if stop_time is not None:
        result["failure_stop_hit"] = True
        result["failure_stop_time_minutes"] = round(
            max(0.0, (stop_time - scan_time).total_seconds() / 60.0), 2
        )

    for threshold in thresholds:
        threshold = float(threshold)
        key = str(int(threshold))
        hit_time = threshold_times[threshold]
        if hit_time is None:
            continue
        result[f"up_{key}_hit"] = True
        result[f"up_{key}_time_minutes"] = round(
            max(0.0, (hit_time - scan_time).total_seconds() / 60.0), 2
        )
        if stop_time is None:
            result[f"up_{key}_before_stop"] = True
        else:
            result[f"up_{key}_before_stop"] = hit_time < stop_time

    return result


def build_research_observations(scans, target_date, bars_index):
    session_end = datetime.combine(target_date, time(20, 0), tzinfo=so.ET)
    regular_close = datetime.combine(target_date, time(16, 0), tzinfo=so.ET)
    rows = []

    for scan in scans:
        scan_time = so.parse_iso(scan.get("scan_time_et"))
        if scan_time is None:
            continue
        scan_time = scan_time.astimezone(so.ET)
        phase = _session_phase(scan, scan_time)
        scan_data = scan.get("data") or {}

        for candidate in scan.get("candidates") or []:
            symbol = str(candidate.get("symbol") or "").upper().strip()
            entry_price = candidate.get("price")
            if not symbol or entry_price in (None, 0):
                continue

            indexed_symbol = bars_index.get(symbol)
            row = {
                "observation_id": f"{scan.get('scan_id')}:{symbol}:opportunity-v1",
                "scan_id": scan.get("scan_id"),
                "scan_time_et": scan_time.isoformat(),
                "session_phase": phase,
                "mode": scan.get("mode"),
                "feature_version": scan.get("feature_version"),
                "behavior_feature_version": (
                    candidate.get("behavior_feature_version")
                    or scan.get("behavior_feature_version")
                ),
                "market_provider": scan_data.get("live_provider"),
                "live_feed": scan_data.get("live_feed"),
                "rank": candidate.get("rank"),
                "symbol": symbol,
                "entry_price": float(entry_price),
                "score": candidate.get("score"),
                "opportunity_score": candidate.get("opportunity_score"),
                "setup_grade": candidate.get("setup_grade"),
                "scanner_action": candidate.get("scanner_action"),
                "scanner_action_tier": candidate.get("scanner_action_tier"),
                "timeframe_best_fit": candidate.get("timeframe_best_fit"),
                "day_pct": candidate.get("day_pct"),
                "momentum_5m": candidate.get("momentum_5m"),
                "momentum_15m": candidate.get("momentum_15m"),
                "volume_pace": candidate.get("volume_pace"),
                "distance_from_high_pct": candidate.get("distance_from_high_pct"),
                "distance_from_vwap_pct": candidate.get("distance_from_vwap_pct"),
                "above_vwap": candidate.get("above_vwap"),
                "volume_acceleration_ratio": candidate.get("volume_acceleration_ratio"),
                "pullback_quality_score": candidate.get("pullback_quality_score"),
                "sequence_health_score": candidate.get("sequence_health_score"),
                "stair_structure_score": candidate.get("stair_structure_score"),
                "crosses_regular_close_60m": (
                    scan_time < regular_close < scan_time + timedelta(minutes=60)
                ),
                "crosses_regular_close_120m": (
                    scan_time < regular_close < scan_time + timedelta(minutes=120)
                ),
            }

            for minutes in HORIZONS_MINUTES:
                target_time = scan_time + timedelta(minutes=minutes)
                price, matched_time = so.price_at_or_after(
                    indexed_symbol,
                    target_time,
                    session_end,
                )
                row[f"research_return_{minutes}m_pct"] = so.pct_return(
                    price,
                    entry_price,
                )
                row[f"research_price_{minutes}m"] = (
                    round(float(price), 4) if price is not None else None
                )
                row[f"research_time_{minutes}m_et"] = (
                    matched_time.isoformat() if matched_time is not None else None
                )

                path = opportunity_path_metrics(
                    indexed_symbol,
                    scan_time,
                    session_end,
                    entry_price,
                    horizon_minutes=minutes,
                )
                row[f"research_mfe_{minutes}m_pct"] = path.get("mfe_pct")
                row[f"research_mae_{minutes}m_pct"] = path.get("mae_pct")
                row[f"research_horizon_{minutes}m_complete"] = path.get("horizon_complete")
                if minutes in {60, 120}:
                    row[f"research_time_to_peak_{minutes}m"] = path.get(
                        "time_to_peak_minutes"
                    )
                    row[f"research_time_to_trough_{minutes}m"] = path.get(
                        "time_to_trough_minutes"
                    )
                    row[f"research_failure_stop_{minutes}m_hit"] = path.get(
                        "failure_stop_hit"
                    )
                    row[f"research_failure_stop_{minutes}m_time"] = path.get(
                        "failure_stop_time_minutes"
                    )
                    for threshold in UPSIDE_THRESHOLDS_PCT:
                        key = str(int(threshold))
                        row[f"research_up_{key}_{minutes}m_hit"] = path.get(
                            f"up_{key}_hit"
                        )
                        row[f"research_up_{key}_{minutes}m_time"] = path.get(
                            f"up_{key}_time_minutes"
                        )
                        row[f"research_up_{key}_{minutes}m_before_stop"] = path.get(
                            f"up_{key}_before_stop"
                        )

            # Preserve the complete candidate snapshot for later hypothesis
            # research without allowing this shadow dataset into production ML.
            row["signal_snapshot"] = candidate
            rows.append(row)

    return rows


def render_markdown(target_date, scans, rows, outcome_source):
    phase_counts = Counter(row.get("session_phase") or "unknown" for row in rows)
    explosive = [
        row
        for row in rows
        if (row.get("research_mfe_60m_pct") or 0) >= 10.0
    ]
    endpoint_misses = [
        row
        for row in rows
        if (
            row.get("research_return_60m_pct") is not None
            and row.get("research_mfe_60m_pct") is not None
            and row["research_mfe_60m_pct"] >= 5.0
            and row["research_return_60m_pct"] < 3.0
        )
    ]

    lines = [
        f"# Shadow Opportunity Outcomes — {target_date.isoformat()}",
        "",
        "> Research-only. These labels do not affect production Scanner ranking or Analyzer decisions.",
        "",
        f"- Scanner snapshots loaded: **{len(scans)}**",
        f"- Candidate observations: **{len(rows)}**",
        f"- Market-data source: **{outcome_source}**",
        f"- Premarket observations: **{phase_counts.get('premarket', 0)}**",
        f"- Regular observations: **{phase_counts.get('regular', 0)}**",
        f"- After-hours observations: **{phase_counts.get('afterhours', 0)}**",
        f"- >= +10% MFE within 60m: **{len(explosive)}**",
        f"- MFE >= +5% but 60m endpoint < +3%: **{len(endpoint_misses)}**",
        "",
        "## Largest 60-minute favorable excursions",
        "",
        "| Symbol | Phase | Rank | Score | MFE 60m | Return 60m | MAE 60m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    top = sorted(
        [row for row in rows if row.get("research_mfe_60m_pct") is not None],
        key=lambda row: row.get("research_mfe_60m_pct") or -999,
        reverse=True,
    )[:20]
    for row in top:
        lines.append(
            "| {symbol} | {phase} | {rank} | {score} | {mfe:+.2f}% | {ret} | {mae:+.2f}% |".format(
                symbol=row.get("symbol") or "",
                phase=row.get("session_phase") or "",
                rank=row.get("rank") or "",
                score=row.get("opportunity_score")
                if row.get("opportunity_score") is not None
                else row.get("score") or "",
                mfe=float(row.get("research_mfe_60m_pct") or 0.0),
                ret=(
                    f"{float(row['research_return_60m_pct']):+.2f}%"
                    if row.get("research_return_60m_pct") is not None
                    else "—"
                ),
                mae=float(row.get("research_mae_60m_pct") or 0.0),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_reports(target_date, scans, rows, outcome_source):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "version": VERSION,
        "trading_date": target_date.isoformat(),
        "generated_at_utc": datetime.now(so.timezone.utc).isoformat(),
        "production_influence": False,
        "purpose": "shadow opportunity/path learning",
        "market_data_source": outcome_source,
        "scanner_snapshot_count": len(scans),
        "observation_count": len(rows),
        "observations": rows,
    }

    stem = f"opportunity_outcomes_{target_date.isoformat()}"
    json_path = REPORT_DIR / f"{stem}.json"
    csv_path = REPORT_DIR / f"{stem}.csv"
    md_path = REPORT_DIR / f"{stem}.md"

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(
        render_markdown(target_date, scans, rows, outcome_source),
        encoding="utf-8",
    )

    flat_fields = [
        key
        for key in sorted(
            {
                key
                for row in rows
                for key, value in row.items()
                if key != "signal_snapshot" and not isinstance(value, (dict, list))
            }
        )
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=flat_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in flat_fields})

    print(f"Shadow opportunity JSON: {json_path}")
    print(f"Shadow opportunity CSV: {csv_path}")
    print(f"Shadow opportunity Markdown: {md_path}")
    return json_path, csv_path, md_path


def main():
    now_et = datetime.now(so.ET)
    target_date = so.resolve_target_date(now_et)
    artifacts = so.list_scan_artifacts(target_date)
    scans = load_active_session_scans(target_date, artifacts)

    if not scans:
        print("No active-session Scanner artifacts were found.")
        write_reports(target_date, [], [], "none")
        return 0

    symbols = {
        str(candidate.get("symbol") or "").upper().strip()
        for scan in scans
        for candidate in (scan.get("candidates") or [])
        if str(candidate.get("symbol") or "").strip()
    }
    session_start = datetime.combine(target_date, time(4, 0), tzinfo=so.ET)
    session_end = datetime.combine(target_date, time(20, 1), tzinfo=so.ET)
    bars, outcome_source = so.get_outcome_bars(
        symbols,
        session_start,
        session_end,
        tradier_session_filter="all",
    )
    indexed = so.index_bars(bars)
    rows = build_research_observations(scans, target_date, indexed)
    write_reports(target_date, scans, rows, outcome_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
