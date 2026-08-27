import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

# ============================================================
# MOMENTUM SCANNER OUTCOME TRACKER - v2.8
#
# Purpose:
#   - Read saved GitHub Actions scan artifacts for one trading day.
#   - Ignore off-hours/test scans so preview data never contaminates results.
#   - Pull consolidated SIP 1-minute bars after the market closes.
#   - Measure each logged observation at +15m / +30m / +60m.
#   - Save JSON, CSV, and Markdown daily performance reports.
#
# This is research/performance measurement only. It does not trade.
# ============================================================

VERSION = "2.8"
GITHUB_API = "https://api.github.com"
ALPACA_DATA_BASE = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")

ARTIFACT_PREFIX = "scan-logs-"
REPORT_DIR = os.environ.get("OUTCOME_REPORT_DIR", "outcome_reports").strip() or "outcome_reports"
OUTCOME_DATE = os.environ.get("OUTCOME_DATE", "").strip()
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()
GITHUB_TOKEN = (
    os.environ.get("GH_TOKEN", "").strip()
    or os.environ.get("GITHUB_TOKEN", "").strip()
)
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "").strip()

HORIZONS_MINUTES = (15, 30, 60)
SIP_FEED = "sip"

if not REPOSITORY or "/" not in REPOSITORY:
    print("ERROR: GITHUB_REPOSITORY is missing or invalid.")
    sys.exit(1)

if not GITHUB_TOKEN:
    print("ERROR: GH_TOKEN/GITHUB_TOKEN is missing.")
    sys.exit(1)

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    print("ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY.")
    sys.exit(1)


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "momentum-scanner-outcome-tracker/2.8",
    }


def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Accept": "application/json",
        "User-Agent": "momentum-scanner-outcome-tracker/2.7",
    }


def request_bytes(url, headers, timeout=45):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def request_json(url, headers, timeout=45):
    return json.loads(request_bytes(url, headers, timeout=timeout).decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Expose GitHub's artifact redirect so auth is not forwarded to blob storage."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_github_artifact_zip(artifact, timeout=60):
    """
    GitHub's artifact endpoint returns a short-lived redirect to blob storage.
    The GitHub Authorization header must NOT be forwarded to that storage URL.
    """
    artifact_id = artifact.get("id")
    if not artifact_id:
        raise RuntimeError(f"Artifact is missing an id: {artifact.get('name')}")

    api_url = f"{GITHUB_API}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
    req = urllib.request.Request(api_url, headers=github_headers())
    opener = urllib.request.build_opener(_NoRedirect())

    try:
        with opener.open(req, timeout=timeout) as response:
            # Unusual but valid if GitHub returns the ZIP directly.
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTP {exc.code} for {api_url}: {body[:600]}"
            ) from exc

        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError(
                f"GitHub artifact download redirect had no Location header for {artifact_id}"
            ) from exc

    # Request the signed blob-storage URL WITHOUT the GitHub bearer token.
    storage_headers = {
        "Accept": "application/zip",
        "User-Agent": "momentum-scanner-outcome-tracker/2.7",
    }
    return request_bytes(location, storage_headers, timeout=timeout)


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def previous_weekday(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def resolve_target_date(now_et):
    if OUTCOME_DATE:
        try:
            return datetime.strptime(OUTCOME_DATE, "%Y-%m-%d").date()
        except ValueError:
            raise RuntimeError("OUTCOME_DATE must be YYYY-MM-DD.")

    # After the close on a weekday, score today. Otherwise use the most recent weekday.
    if now_et.weekday() < 5 and now_et.time() >= time(16, 15):
        return now_et.date()
    return previous_weekday(now_et.date())


def list_scan_artifacts(target_date):
    """Return scan artifacts whose creation date in New York matches target_date."""
    matched = []
    page = 1

    while page <= 5:
        params = urllib.parse.urlencode({"per_page": 100, "page": page})
        url = f"{GITHUB_API}/repos/{REPOSITORY}/actions/artifacts?{params}"
        data = request_json(url, github_headers())
        artifacts = data.get("artifacts") or []
        if not artifacts:
            break

        oldest_seen = None
        for artifact in artifacts:
            created = parse_iso(artifact.get("created_at"))
            if not created:
                continue
            created_et = created.astimezone(ET)
            oldest_seen = created_et.date() if oldest_seen is None else min(oldest_seen, created_et.date())

            if (
                created_et.date() == target_date
                and str(artifact.get("name") or "").startswith(ARTIFACT_PREFIX)
                and not artifact.get("expired")
            ):
                matched.append(artifact)

        # Artifacts are newest-first. Once a full page is older than target day, stop.
        if oldest_seen is not None and oldest_seen < target_date:
            break

        page += 1

    matched.sort(key=lambda a: a.get("created_at") or "")
    return matched


def extract_scan_payloads(artifact):
    """Download one artifact ZIP and return unique scan_*.json payloads."""
    raw_zip = download_github_artifact_zip(artifact, timeout=60)
    payloads = []

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if not base.startswith("scan_") or not base.endswith(".json"):
                continue
            if base == "latest_scan.json":
                continue
            try:
                payloads.append(json.loads(zf.read(name).decode("utf-8")))
            except Exception as exc:
                print(f"WARN could not parse {artifact.get('name')}:{name}: {exc}")

    return payloads


def load_regular_session_scans(target_date, artifacts):
    all_scans = []
    seen_ids = set()

    for artifact in artifacts:
        try:
            payloads = extract_scan_payloads(artifact)
        except Exception as exc:
            print(f"WARN could not read artifact {artifact.get('name')}: {exc}")
            continue

        for payload in payloads:
            scan_id = payload.get("scan_id")
            if not scan_id or scan_id in seen_ids:
                continue
            seen_ids.add(scan_id)
            all_scans.append(payload)

    regular = []
    off_hours = 0
    wrong_date = 0

    for payload in all_scans:
        scan_time = parse_iso(payload.get("scan_time_et"))
        if not scan_time:
            wrong_date += 1
            continue
        scan_time = scan_time.astimezone(ET)

        if scan_time.date() != target_date:
            wrong_date += 1
            continue
        if payload.get("mode") != "regular_market_session":
            off_hours += 1
            continue

        regular.append(payload)

    regular.sort(key=lambda p: p.get("scan_time_et") or "")
    return regular, {
        "artifact_count": len(artifacts),
        "scan_payload_count": len(all_scans),
        "regular_session_scan_count": len(regular),
        "off_hours_scans_ignored": off_hours,
        "wrong_date_scans_ignored": wrong_date,
    }


def get_multi_bars(symbols, start_et, end_et):
    """Fetch consolidated SIP 1-minute bars for all symbols, following pagination."""
    if not symbols:
        return {}

    merged = defaultdict(list)
    page_token = None
    pages = 0

    while True:
        query = {
            "symbols": ",".join(sorted(symbols)),
            "timeframe": "1Min",
            "start": start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": 10000,
            "adjustment": "raw",
            "feed": SIP_FEED,
            "sort": "asc",
        }
        if page_token:
            query["page_token"] = page_token

        params = urllib.parse.urlencode(query)
        url = f"{ALPACA_DATA_BASE}/v2/stocks/bars?{params}"
        data = request_json(url, alpaca_headers(), timeout=60)

        for symbol, bars in (data.get("bars") or {}).items():
            merged[str(symbol).upper()].extend(bars or [])

        page_token = data.get("next_page_token")
        pages += 1
        if not page_token or pages >= 20:
            break

    return dict(merged)


def index_bars(bars_by_symbol):
    indexed = {}
    for symbol, bars in bars_by_symbol.items():
        parsed = []
        for bar in bars:
            ts = parse_iso(bar.get("t"))
            close = bar.get("c")
            if ts is None or close in (None, 0):
                continue
            parsed.append((ts.astimezone(ET), float(close)))
        parsed.sort(key=lambda x: x[0])
        indexed[symbol] = {
            "times": [x[0] for x in parsed],
            "prices": [x[1] for x in parsed],
        }
    return indexed


def price_at_or_after(indexed_symbol, target_time, session_close):
    if not indexed_symbol or target_time > session_close:
        return None, None

    times = indexed_symbol["times"]
    prices = indexed_symbol["prices"]
    pos = bisect_left(times, target_time)
    if pos >= len(times):
        return None, None

    ts = times[pos]
    if ts.date() != target_time.date() or ts > session_close:
        return None, None

    return prices[pos], ts


def pct_return(exit_price, entry_price):
    if exit_price in (None, 0) or entry_price in (None, 0):
        return None
    return round((float(exit_price) / float(entry_price) - 1.0) * 100.0, 3)


def build_observations(scans, target_date, bars_index):
    session_close = datetime.combine(target_date, time(16, 0), tzinfo=ET)
    rows = []

    for scan in scans:
        scan_time = parse_iso(scan.get("scan_time_et"))
        if not scan_time:
            continue
        scan_time = scan_time.astimezone(ET)

        for c in scan.get("candidates") or []:
            symbol = str(c.get("symbol") or "").upper().strip()
            entry_price = c.get("price")
            if not symbol or entry_price in (None, 0):
                continue

            row = {
                "observation_id": f"{scan.get('scan_id')}:{symbol}",
                "scan_id": scan.get("scan_id"),
                "scan_time_et": scan_time.isoformat(),
                "rank": c.get("rank"),
                "symbol": symbol,
                "entry_price": float(entry_price),
                "day_pct": c.get("day_pct"),
                "score": c.get("score"),
                "base_score": c.get("base_score"),
                "live_bonus": c.get("live_bonus"),
                "news_bonus": c.get("news_bonus"),
                "opportunity_score": c.get("opportunity_score"),
                "intraday_range_pct": c.get("intraday_range_pct"),
                "expected_volume_fraction_pct": c.get("expected_volume_fraction_pct"),
                "volume_vs_expected_pct": c.get("volume_vs_expected_pct"),
                "live_confirmation_count": c.get("live_confirmation_count"),
                "ml_continuation_prob_pct": c.get("ml_continuation_prob_pct"),
                "ml_validated": c.get("ml_validated"),
                "ml_status": c.get("ml_status"),
                "setup_grade": c.get("setup_grade"),
                "setup_label": c.get("setup_label"),
                "alert_tier": c.get("alert_tier"),
                "alert_ready": bool(c.get("alert_ready")),
                "passed_base_filters": bool(c.get("passed_base_filters")),
                "momentum_5m": c.get("momentum_5m"),
                "momentum_15m": c.get("momentum_15m"),
                "volume_pace": c.get("volume_pace"),
                "liquidity_dollar_volume": c.get("liquidity_dollar_volume"),
                "liquidity_source": c.get("liquidity_source"),
                "live_quote_source": c.get("live_quote_source"),
                "live_intraday_source": c.get("live_intraday_source"),
                "spread_pct": (
                    c.get("live_spread_pct")
                    if c.get("live_spread_pct") is not None
                    else c.get("iex_spread_pct")
                ),
                # Retained for compatibility with older reports/models only.
                "iex_spread_pct": c.get("iex_spread_pct"),
                "distance_from_high_pct": c.get("distance_from_high_pct"),
                "distance_from_vwap_pct": c.get("distance_from_vwap_pct"),
                "above_vwap": bool(c.get("above_vwap")),
                "tradability_warnings": c.get("tradability_warnings") or [],
                "setup_flags": c.get("setup_flags") or [],
                "news_status": (c.get("news") or {}).get("status"),
                "news_category": (c.get("news") or {}).get("category"),
                "news_score": (c.get("news") or {}).get("score"),
                "historical_status": (c.get("historical") or {}).get("status"),
                "historical_quality": (c.get("historical") or {}).get("quality"),
            }

            symbol_index = bars_index.get(symbol)
            for minutes in HORIZONS_MINUTES:
                target_time = scan_time + timedelta(minutes=minutes)
                exit_price, exit_time = price_at_or_after(
                    symbol_index, target_time, session_close
                )
                row[f"price_{minutes}m"] = round(exit_price, 4) if exit_price is not None else None
                row[f"time_{minutes}m_et"] = exit_time.isoformat() if exit_time else None
                row[f"return_{minutes}m_pct"] = pct_return(exit_price, entry_price)

            rows.append(row)

    return rows


def horizon_stats(rows, minutes):
    values = [
        r.get(f"return_{minutes}m_pct")
        for r in rows
        if r.get(f"return_{minutes}m_pct") is not None
    ]
    if not values:
        return {
            "n": 0,
            "win_rate_pct": None,
            "median_return_pct": None,
            "average_return_pct": None,
        }

    return {
        "n": len(values),
        "win_rate_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 1),
        "median_return_pct": round(median(values), 3),
        "average_return_pct": round(mean(values), 3),
    }


def selection_stats(rows):
    values = [
        r.get("return_60m_pct")
        for r in rows
        if r.get("return_60m_pct") is not None
    ]
    if not values:
        return {
            "n": 0,
            "hit_3pct_rate_pct": None,
            "win_rate_pct": None,
            "median_return_60m_pct": None,
            "average_return_60m_pct": None,
        }
    return {
        "n": len(values),
        "hit_3pct_rate_pct": round(
            sum(v >= 3.0 for v in values) / len(values) * 100.0, 1
        ),
        "win_rate_pct": round(
            sum(v > 0 for v in values) / len(values) * 100.0, 1
        ),
        "median_return_60m_pct": round(median(values), 3),
        "average_return_60m_pct": round(mean(values), 3),
    }


def top_per_scan(rows, key, n=5):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("scan_id")].append(row)

    selected = []
    for scan_rows in grouped.values():
        ranked = sorted(
            scan_rows,
            key=lambda r: (
                float(r.get(key))
                if r.get(key) is not None
                else float("-inf")
            ),
            reverse=True,
        )
        selected.extend(ranked[:n])
    return selected


def summarize(rows):
    scanner_top5 = top_per_scan(rows, "score", 5)
    opportunity_top5 = top_per_scan(rows, "opportunity_score", 5)

    summary = {
        "observation_count": len(rows),
        "unique_symbols": len({r["symbol"] for r in rows}),
        "alert_ready_observations": sum(bool(r.get("alert_ready")) for r in rows),
        "overall": {
            f"{m}m": horizon_stats(rows, m) for m in HORIZONS_MINUTES
        },
        "by_grade": {},
        "by_alert_tier": {},
        "ranking_comparison": {
            "scanner_score_top5": selection_stats(scanner_top5),
            "opportunity_score_top5": selection_stats(opportunity_top5),
        },
    }

    grades = sorted(
        {str(r.get("setup_grade") or "UNKNOWN") for r in rows},
        key=lambda g: {"A": 0, "B": 1, "C": 2, "REJECT": 3}.get(g, 9),
    )
    for grade in grades:
        group = [r for r in rows if str(r.get("setup_grade") or "UNKNOWN") == grade]
        summary["by_grade"][grade] = {
            "n": len(group),
            **{f"{m}m": horizon_stats(group, m) for m in HORIZONS_MINUTES},
        }

    tiers = sorted({str(r.get("alert_tier") or "NONE") for r in rows})
    for tier in tiers:
        group = [r for r in rows if str(r.get("alert_tier") or "NONE") == tier]
        summary["by_alert_tier"][tier] = {
            "n": len(group),
            **{f"{m}m": horizon_stats(group, m) for m in HORIZONS_MINUTES},
        }

    return summary


def fmt_pct(value):
    return "-" if value is None else f"{value:+.2f}%"


def render_markdown(target_date, discovery, summary, status, error=None):
    lines = [
        f"# Momentum Scanner Outcome Report — {target_date.isoformat()}",
        "",
        f"- Tracker version: **v{VERSION}**",
        f"- Status: **{status}**",
        f"- Scan artifacts found: **{discovery.get('artifact_count', 0)}**",
        f"- Regular-session scans scored: **{discovery.get('regular_session_scan_count', 0)}**",
        f"- Off-hours/test scans ignored: **{discovery.get('off_hours_scans_ignored', 0)}**",
    ]

    if error:
        lines += ["", f"> Error: {error}"]

    if not summary:
        lines += [
            "",
            "No valid regular-session observations were available to score.",
            "Off-hours previews are intentionally excluded from performance statistics.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        f"- Observations: **{summary['observation_count']}**",
        f"- Unique symbols: **{summary['unique_symbols']}**",
        f"- Alert-ready observations: **{summary['alert_ready_observations']}**",
        "",
        "## Overall",
        "",
        "| Horizon | N | Win rate | Median return | Average return |",
        "|---|---:|---:|---:|---:|",
    ]

    for m in HORIZONS_MINUTES:
        s = summary["overall"][f"{m}m"]
        win = "-" if s["win_rate_pct"] is None else f"{s['win_rate_pct']:.1f}%"
        lines.append(
            f"| +{m}m | {s['n']} | {win} | {fmt_pct(s['median_return_pct'])} | {fmt_pct(s['average_return_pct'])} |"
        )

    lines += [
        "",
        "## By setup grade",
        "",
        "| Grade | Observations | +15m win | +30m win | +60m win | +30m median |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for grade, group in summary["by_grade"].items():
        def win(m):
            v = group[f"{m}m"]["win_rate_pct"]
            return "-" if v is None else f"{v:.1f}%"

        lines.append(
            f"| {grade} | {group['n']} | {win(15)} | {win(30)} | {win(60)} | {fmt_pct(group['30m']['median_return_pct'])} |"
        )

    comparison = summary.get("ranking_comparison") or {}
    scanner_cmp = comparison.get("scanner_score_top5") or {}
    ml_cmp = comparison.get("opportunity_score_top5") or {}

    def pct_or_dash(value):
        return "-" if value is None else f"{value:.1f}%"

    lines += [
        "",
        "## Scanner vs ML-enhanced ranking",
        "",
        "| Ranking method | N | +3% at 60m | Positive at 60m | Median 60m return |",
        "|---|---:|---:|---:|---:|",
        (
            f"| Original scanner score — top 5 | {scanner_cmp.get('n', 0)} | "
            f"{pct_or_dash(scanner_cmp.get('hit_3pct_rate_pct'))} | "
            f"{pct_or_dash(scanner_cmp.get('win_rate_pct'))} | "
            f"{fmt_pct(scanner_cmp.get('median_return_60m_pct'))} |"
        ),
        (
            f"| ML Opportunity score — top 5 | {ml_cmp.get('n', 0)} | "
            f"{pct_or_dash(ml_cmp.get('hit_3pct_rate_pct'))} | "
            f"{pct_or_dash(ml_cmp.get('win_rate_pct'))} | "
            f"{fmt_pct(ml_cmp.get('median_return_60m_pct'))} |"
        ),
        "",
        "_The Opportunity score is identical to the original scanner score until the ML validation gate passes, so this comparison only becomes meaningful after validated ML is active._",
        "",
        "_Each row is a scanner observation at a specific scan time. Repeated appearances of the same ticker are intentionally retained for now; later versions can also evaluate deduplicated alert events._",
    ]
    return "\n".join(lines) + "\n"


def write_reports(target_date, discovery, rows, status, error=None):
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize(rows) if rows else None
    payload = {
        "schema_version": 1,
        "tracker_version": VERSION,
        "trading_date": target_date.isoformat(),
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "discovery": discovery,
        "summary": summary,
        "error": error,
        "observations": rows,
    }

    stem = f"outcomes_{target_date.isoformat()}"
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_fields = [
        "observation_id", "scan_id", "scan_time_et", "rank", "symbol",
        "entry_price", "day_pct", "score", "base_score", "live_bonus", "news_bonus",
        "opportunity_score", "intraday_range_pct", "expected_volume_fraction_pct",
        "volume_vs_expected_pct", "live_confirmation_count",
        "ml_continuation_prob_pct", "ml_validated", "ml_status",
        "setup_grade", "setup_label",
        "alert_tier", "alert_ready", "passed_base_filters",
        "momentum_5m", "momentum_15m", "volume_pace",
        "liquidity_dollar_volume", "iex_spread_pct",
        "distance_from_high_pct", "distance_from_vwap_pct", "above_vwap",
        "tradability_warnings", "setup_flags", "news_status", "news_category",
        "news_score", "historical_status", "historical_quality",
        "price_15m", "time_15m_et", "return_15m_pct",
        "price_30m", "time_30m_et", "return_30m_pct",
        "price_60m", "time_60m_et", "return_60m_pct",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["tradability_warnings"] = " | ".join(row.get("tradability_warnings") or [])
            flat["setup_flags"] = " | ".join(row.get("setup_flags") or [])
            writer.writerow({k: flat.get(k) for k in csv_fields})

    md_path.write_text(
        render_markdown(target_date, discovery, summary, status, error=error),
        encoding="utf-8",
    )

    print("\nOUTCOME REPORTS")
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Saved Markdown summary: {md_path}")
    return json_path, csv_path, md_path


def main():
    now_et = datetime.now(ET)
    target_date = resolve_target_date(now_et)

    print(f"Momentum outcome tracker v{VERSION}")
    print(f"Target trading date: {target_date.isoformat()}")
    print(f"Repository: {REPOSITORY}")

    try:
        artifacts = list_scan_artifacts(target_date)
        scans, discovery = load_regular_session_scans(target_date, artifacts)
    except Exception as exc:
        discovery = {
            "artifact_count": 0,
            "scan_payload_count": 0,
            "regular_session_scan_count": 0,
            "off_hours_scans_ignored": 0,
            "wrong_date_scans_ignored": 0,
        }
        write_reports(target_date, discovery, [], "artifact_discovery_error", error=str(exc))
        raise

    print(f"Scan artifacts found: {discovery['artifact_count']}")
    print(f"Regular-session scans: {discovery['regular_session_scan_count']}")
    print(f"Off-hours/test scans ignored: {discovery['off_hours_scans_ignored']}")

    if not scans:
        write_reports(target_date, discovery, [], "no_regular_session_scans")
        print("No regular-session scans to score. This is expected before the first live logging day.")
        return

    symbols = {
        str(c.get("symbol") or "").upper().strip()
        for scan in scans
        for c in (scan.get("candidates") or [])
        if c.get("symbol")
    }

    session_open = datetime.combine(target_date, time(9, 30), tzinfo=ET)
    session_end = datetime.combine(target_date, time(16, 1), tzinfo=ET)

    try:
        bars = get_multi_bars(symbols, session_open, session_end)
        bars_index = index_bars(bars)
    except Exception as exc:
        write_reports(
            target_date,
            discovery,
            [],
            "market_data_error",
            error=str(exc),
        )
        raise

    rows = build_observations(scans, target_date, bars_index)
    status = "complete" if rows else "no_scoreable_observations"
    write_reports(target_date, discovery, rows, status)

    if rows:
        summary = summarize(rows)
        print("\nPERFORMANCE SNAPSHOT")
        for m in HORIZONS_MINUTES:
            s = summary["overall"][f"{m}m"]
            print(
                f"+{m}m: n={s['n']} | "
                f"win={s['win_rate_pct'] if s['win_rate_pct'] is not None else '-'}% | "
                f"median={s['median_return_pct'] if s['median_return_pct'] is not None else '-'}%"
            )


if __name__ == "__main__":
    main()
