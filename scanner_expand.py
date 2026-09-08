from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import html


SCAN_FILE = Path(__file__).with_name("scan_logs") / "latest_scan.json"


def _f(value, digits=1, suffix=""):
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _news_time(news: dict) -> str:
    raw = news.get("published_at") or news.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            et = dt.astimezone(ZoneInfo("America/New_York"))
            stamp = et.strftime("%a %b %d, %Y · %I:%M %p ET").replace(" 0", " ")
            age = news.get("age_hours")
            if age is not None:
                try:
                    age = float(age)
                    if age < 1:
                        age_text = f"{max(1, round(age * 60))} min ago"
                    elif age < 24:
                        age_text = f"{age:.1f}h ago"
                    else:
                        age_text = f"{age / 24:.1f}d ago"
                    return f"Published {stamp} · {age_text}"
                except (TypeError, ValueError):
                    pass
            return f"Published {stamp}"
        except (TypeError, ValueError):
            pass
    return "Publication time unavailable"


def _load_details(payload=None) -> dict[str, dict]:
    if payload is None:
        try:
            payload = json.loads(SCAN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    details = {}
    for row in payload.get("candidates") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue

        news = row.get("news") or {}
        notes = (
            (row.get("grade_reasons") or [])
            + (row.get("failed_filters") or [])
            + (row.get("tradability_warnings") or [])
            + (row.get("setup_flags") or [])
        )
        setup_read = " · ".join(str(x) for x in notes[:4]) or "No major issues flagged."
        news_bits = [news.get("category"), news.get("headline")]
        catalyst = " — ".join(str(x) for x in news_bits if x)
        spread = row.get("iex_spread_pct")
        if spread is None:
            spread = row.get("spread_pct")

        details[symbol] = {
            "symbol": symbol,
            "price": "$" + _f(row.get("price"), 2),
            "day": _f(row.get("day_pct"), 1, "%"),
            "day_positive": float(row.get("day_pct") or 0) >= 0,
            "score": _f(row.get("score"), 0),
            "explosion_score": _f(
                row.get("explosion_score")
                if row.get("explosion_score") is not None
                else row.get("score"),
                0,
            ),
            "tradeability_score": _f(row.get("tradeability_score"), 0),
            "risk_lane": str(row.get("risk_lane") or "$1-$50"),
            "radar_3m": _f(row.get("radar_change_3m_pct"), 2, "%"),
            "radar_5m": _f(row.get("radar_change_5m_pct"), 2, "%"),
            "radar_velocity": _f(row.get("radar_volume_velocity_ratio"), 2, "x"),
            "grade": str(row.get("setup_grade") or "REJECT"),
            "label": str(row.get("setup_label") or ""),
            "passed": bool(row.get("passed_base_filters")),
            "alert": str(row.get("alert_tier") or ""),
            "above_vwap": bool(row.get("above_vwap")),
            "momentum_5m": _f(row.get("momentum_5m"), 2, "%"),
            "momentum_15m": _f(row.get("momentum_15m"), 2, "%"),
            "volume_pace": _f(row.get("volume_pace"), 2, "x"),
            "normal_volume": _f(row.get("expected_volume_fraction_pct"), 1, "%"),
            "vwap": "$" + _f(row.get("vwap"), 2),
            "from_high": _f(row.get("distance_from_high_pct"), 2, "%"),
            "spread": _f(spread, 2, "%"),
            "liquidity": "$" + _f((row.get("liquidity_dollar_volume") or 0) / 1_000_000, 1, "M"),
            "setup_read": setup_read,
            "catalyst": catalyst,
            "catalyst_time": _news_time(news) if catalyst else "",
        }
    return details



def scanner_detail_html(data):
    """Render details as native HTML; no mutation of Streamlit/React-owned nodes."""
    if not data:
        return ""
    esc = lambda value: html.escape(str(value or "—"))
    metrics = (
        ("Price", data["price"]), ("Today", data["day"]),
        ("Setup Score", data["score"]), ("Explosion Score", data["explosion_score"]),
        ("Tradeability", data["tradeability_score"]), ("Risk Lane", data["risk_lane"]),
        ("Radar 3 Min", data["radar_3m"]), ("Radar 5 Min", data["radar_5m"]),
        ("Volume Velocity", data["radar_velocity"]), ("5 Min", data["momentum_5m"]),
        ("15 Min", data["momentum_15m"]), ("Volume Pace", data["volume_pace"]),
        ("Normal Volume by Now", data["normal_volume"]), ("VWAP", data["vwap"]),
        ("From High", data["from_high"]), ("Live Spread", data["spread"]),
        ("Liquidity", data["liquidity"]),
    )
    cells = "".join(
        f'<div class="sid-metric"><div class="sid-mk">{esc(k)}</div>'
        f'<div class="sid-mv">{esc(v)}</div></div>' for k, v in metrics
    )
    catalyst = (
        f'<div class="sid-note"><strong>Catalyst</strong><p>{esc(data["catalyst"])}</p>'
        f'<small>{esc(data["catalyst_time"])}</small></div>' if data["catalyst"] else ""
    )
    return (
        f'<div class="scanner-inline-detail"><strong>{esc(data["symbol"])} · Scanner details</strong>'
        f'<div class="sid-note">Grade {esc(data["grade"])} · {esc(data["label"])} · '
        f'{"Base filters passed" if data["passed"] else "Watch / risk flagged"} · '
        f'{"Above VWAP" if data["above_vwap"] else "Below VWAP"} · Alert {esc(data["alert"])}</div>'
        f'<div class="sid-grid">{cells}</div>'
        f'<div class="sid-note"><strong>Setup read</strong><p>{esc(data["setup_read"])}</p></div>'
        f'{catalyst}</div>'
    )
