from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit.components.v1 as components


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


def _load_details() -> dict[str, dict]:
    if not SCAN_FILE.exists():
        return {}
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


def install_scanner_expander() -> None:
    details_json = json.dumps(_load_details(), ensure_ascii=False).replace("</", "<\\/")
    components.html(
        f"""
        <script>
        (() => {{
          const p = window.parent;
          const d = p.document;
          p.__scannerDetailData = {details_json};
          if (!p.__scannerExpandedSymbols) p.__scannerExpandedSymbols = new Set();

          const STYLE_ID = 'scanner-inline-expand-style';
          if (!d.getElementById(STYLE_ID)) {{
            const style = d.createElement('style');
            style.id = STYLE_ID;
            style.textContent = `
              .scanner-expandable-ticker {{ cursor:pointer !important; user-select:none; }}
              .scanner-expandable-ticker:hover {{ color:#7dd3fc !important; }}
              .scanner-expandable-ticker:focus {{ outline:2px solid #4593ff; outline-offset:4px; border-radius:4px; }}
              .scanner-expandable-ticker::after {{ content:'  ▾'; font-size:.58em; color:#8fa8c7; vertical-align:middle; }}
              .scanner-expandable-ticker[aria-expanded="true"]::after {{ content:'  ▴'; }}
              .scanner-inline-detail {{
                --detail-accent:#38bdf8;
                box-sizing:border-box;width:100%;margin:2px 0 15px;padding:24px 28px;
                background:#111b2e;border:1px solid #31435f;border-top:4px solid var(--detail-accent);
                border-radius:16px;color:#f4f7fb;
              }}
              .scanner-inline-detail.grade-a {{ --detail-accent:#22c55e; }}
              .scanner-inline-detail.grade-b {{ --detail-accent:#38bdf8; }}
              .scanner-inline-detail.grade-c {{ --detail-accent:#f59e0b; }}
              .sid-head {{ display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:14px; }}
              .sid-symbol {{ font-size:34px;font-weight:950;letter-spacing:-.02em; }}
              .sid-price {{ font-size:20px;font-weight:900;margin-top:12px; }}
              .sid-day-pos {{ color:#65e98d; }} .sid-day-neg {{ color:#ff8181; }}
              .sid-score {{ font-size:32px;font-weight:950;color:#65e98d;text-align:right; }}
              .sid-score small {{ display:block;color:#9fb0c9;font-size:11px;letter-spacing:.08em;margin-top:6px; }}
              .sid-badges {{ display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px; }}
              .sid-badge {{ border-radius:999px;padding:7px 11px;font-size:12px;font-weight:900;border:1px solid #355071;background:#16233a; }}
              .sid-good {{ color:#b8f7ca;border-color:rgba(34,197,94,.48);background:rgba(34,197,94,.13); }}
              .sid-warn {{ color:#ffe0a0;border-color:rgba(245,158,11,.46);background:rgba(245,158,11,.13); }}
              .sid-bad {{ color:#ffc1c1;border-color:rgba(239,68,68,.46);background:rgba(239,68,68,.13); }}
              .sid-grid {{ display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 14px;margin-top:8px; }}
              .sid-metric {{ background:#16233a;border:1px solid #31435f;border-radius:12px;padding:14px 16px;min-width:0; }}
              .sid-mk {{ color:#9fb0c9;font-size:11px;font-weight:900;letter-spacing:.07em;text-transform:uppercase; }}
              .sid-mv {{ font-size:20px;font-weight:950;margin-top:8px; }}
              .sid-note {{ background:#172238;border-left:4px solid #31435f;border-radius:8px;padding:13px 16px;margin-top:14px; }}
              .sid-note-k {{ color:#9fb0c9;font-size:11px;font-weight:900;letter-spacing:.07em;text-transform:uppercase; }}
              .sid-note-v {{ font-size:14px;line-height:1.45;margin-top:7px; }}
              .sid-note-time {{ color:#9fb0c9;font-size:12px;font-weight:750;margin-top:7px; }}
              @media(max-width:760px) {{
                .scanner-inline-detail {{ padding:18px 16px; }}
                .sid-grid {{ grid-template-columns:1fr; }}
                .sid-symbol {{ font-size:28px; }}
              }}
            `;
            d.head.appendChild(style);
          }}

          const esc = (value) => String(value ?? '')
            .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;').replace(/'/g,'&#039;');

          function gradeClass(grade) {{
            const g = String(grade || '').toUpperCase();
            if (g === 'A') return 'grade-a';
            if (g === 'B') return 'grade-b';
            if (g === 'C') return 'grade-c';
            return '';
          }}

          function metric(label, value, good=false) {{
            return `<div class="sid-metric"><div class="sid-mk">${{esc(label)}}</div>` +
                   `<div class="sid-mv ${{good ? 'sid-day-pos' : ''}}">${{esc(value)}}</div></div>`;
          }}

          function cardHTML(x) {{
            const dayCls = x.day_positive ? 'sid-day-pos' : 'sid-day-neg';
            const passBadge = x.passed
              ? '<span class="sid-badge sid-good">BASE FILTERS PASS</span>'
              : '<span class="sid-badge sid-warn">FILTERED / NEAR MISS</span>';
            const alertBadge = x.alert
              ? `<span class="sid-badge ${{x.alert === 'HIGH' ? 'sid-good' : 'sid-warn'}}">ALERT ${{esc(x.alert)}}</span>`
              : '';
            const vwapBadge = x.above_vwap
              ? '<span class="sid-badge sid-good">ABOVE VWAP</span>'
              : '<span class="sid-badge sid-bad">BELOW VWAP</span>';
            const catalyst = x.catalyst
              ? `<div class="sid-note"><div class="sid-note-k">CATALYST</div><div class="sid-note-v">${{esc(x.catalyst)}}</div>` +
                `<div class="sid-note-time">${{esc(x.catalyst_time)}}</div></div>`
              : '';

            return `
              <div class="sid-head">
                <div><div class="sid-symbol">${{esc(x.symbol)}}</div>
                  <div class="sid-price">${{esc(x.price)}} <span class="${{dayCls}}">${{esc(x.day)}} today</span></div>
                </div>
                <div class="sid-score">${{esc(x.score)}}<small>SCORE / 100</small></div>
              </div>
              <div class="sid-badges">
                <span class="sid-badge sid-good">GRADE ${{esc(x.grade)}} · ${{esc(x.label)}}</span>
                ${{passBadge}}${{alertBadge}}${{vwapBadge}}
              </div>
              <div class="sid-grid">
                ${{metric('5 MIN', x.momentum_5m, Number.parseFloat(x.momentum_5m) > 0)}}
                ${{metric('15 MIN', x.momentum_15m, Number.parseFloat(x.momentum_15m) > 0)}}
                ${{metric('TOD VOL PACE', x.volume_pace, Number.parseFloat(x.volume_pace) >= 1.5)}}
                ${{metric('NORMAL VOL BY NOW', x.normal_volume)}}
                ${{metric('VWAP PRICE', x.vwap, x.above_vwap)}}
                ${{metric('FROM HIGH', x.from_high)}}
                ${{metric('IEX SPREAD', x.spread)}}
                ${{metric('LIQUIDITY', x.liquidity)}}
              </div>
              <div class="sid-note"><div class="sid-note-k">SETUP READ</div><div class="sid-note-v">${{esc(x.setup_read)}}</div></div>
              ${{catalyst}}
            `;
          }}

          function hostForTicker(el) {{
            return el.closest('[data-testid="stHorizontalBlock"]') || el.parentElement;
          }}

          function detailId(symbol) {{ return `scanner-detail-${{symbol.replace(/[^A-Z0-9_-]/g,'-')}}`; }}

          function renderDetail(el, symbol) {{
            const data = p.__scannerDetailData && p.__scannerDetailData[symbol];
            if (!data) return;
            const host = hostForTicker(el);
            if (!host || !host.parentNode) return;
            let detail = d.getElementById(detailId(symbol));
            if (!detail) {{
              detail = d.createElement('div');
              detail.id = detailId(symbol);
              detail.className = `scanner-inline-detail ${{gradeClass(data.grade)}}`;
              host.parentNode.insertBefore(detail, host.nextSibling);
            }}
            detail.innerHTML = cardHTML(data);
            el.setAttribute('aria-expanded','true');
          }}

          function removeDetail(el, symbol) {{
            const detail = d.getElementById(detailId(symbol));
            if (detail) detail.remove();
            if (el) el.setAttribute('aria-expanded','false');
          }}

          function toggle(el) {{
            const symbol = String(el.textContent || '').replace(/[▾▴]/g,'').trim().toUpperCase();
            if (!symbol || !p.__scannerDetailData || !p.__scannerDetailData[symbol]) return;
            if (p.__scannerExpandedSymbols.has(symbol)) {{
              p.__scannerExpandedSymbols.delete(symbol);
              removeDetail(el, symbol);
            }} else {{
              p.__scannerExpandedSymbols.add(symbol);
              renderDetail(el, symbol);
            }}
          }}

          function enhance() {{
            d.querySelectorAll('.combined-ticker-symbol').forEach((el) => {{
              const symbol = String(el.textContent || '').replace(/[▾▴]/g,'').trim().toUpperCase();
              if (!p.__scannerDetailData || !p.__scannerDetailData[symbol]) return;
              el.classList.add('scanner-expandable-ticker');
              el.setAttribute('role','button');
              el.setAttribute('tabindex','0');
              el.setAttribute('aria-label', `${{symbol}} details; click to expand or collapse`);
              el.setAttribute('aria-expanded', p.__scannerExpandedSymbols.has(symbol) ? 'true' : 'false');
              if (p.__scannerExpandedSymbols.has(symbol) && !d.getElementById(detailId(symbol))) renderDetail(el, symbol);
            }});
          }}

          const old = p.__scannerExpandController;
          if (old) {{
            try {{ d.removeEventListener('click', old.click); }} catch (_) {{}}
            try {{ d.removeEventListener('keydown', old.keydown); }} catch (_) {{}}
            try {{ old.observer.disconnect(); }} catch (_) {{}}
          }}

          const click = (event) => {{
            const el = event.target.closest && event.target.closest('.scanner-expandable-ticker');
            if (el) toggle(el);
          }};
          const keydown = (event) => {{
            const el = event.target.closest && event.target.closest('.scanner-expandable-ticker');
            if (!el || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            toggle(el);
          }};
          d.addEventListener('click', click);
          d.addEventListener('keydown', keydown);

          let queued = false;
          const observer = new MutationObserver(() => {{
            if (queued) return;
            queued = true;
            p.requestAnimationFrame(() => {{ queued = false; enhance(); }});
          }});
          if (d.body) observer.observe(d.body, {{childList:true, subtree:true}});
          p.__scannerExpandController = {{click, keydown, observer}};
          enhance();
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )
