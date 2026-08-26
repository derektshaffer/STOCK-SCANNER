from pathlib import Path

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)

engine_path = Path("stock_analyzer.py")
engine = engine_path.read_text()

engine = replace_once(
    engine,
    'from zoneinfo import ZoneInfo\n',
    'from zoneinfo import ZoneInfo\nfrom historical_patterns import analyze_historical_patterns\n',
    "historical_patterns import",
)
engine = replace_once(
    engine,
    'ANALYZER_ENGINE_VERSION = "trade-plan-v2"',
    'ANALYZER_ENGINE_VERSION = "trade-plan-v3-historical-patterns"',
    "engine version",
)
engine = replace_once(
    engine,
    '    prev_close=fnum(prev.get("c")); day_pct=pct(price,prev_close) if prev_close else None\n',
    '    prev_close=fnum(prev.get("c")); day_pct=pct(price,prev_close) if prev_close else None\n    day_open=fnum(day.get("o")); gap_pct=pct(day_open,prev_close) if day_open and prev_close else None\n',
    "current gap",
)
engine = replace_once(
    engine,
    '    hist=historical_spikes(symbol,now,day_pct)\n',
    '    hist=historical_spikes(symbol,now,day_pct)\n    try:\n        hist["setup_patterns"]=analyze_historical_patterns(symbol,now,day_pct,gap_pct,pace,try_sip_delayed_bars,ET)\n    except Exception as exc:\n        hist["setup_patterns"]={"status":"unavailable","sample_count":0,"matches":[],"error":str(exc)[:160]}\n',
    "historical setup analysis call",
)
engine = replace_once(
    engine,
    '"price":round(price,4),"prev_close":prev_close,"day_pct":round(day_pct,2) if day_pct is not None else None,',
    '"price":round(price,4),"prev_close":prev_close,"day_pct":round(day_pct,2) if day_pct is not None else None,"gap_pct":round(gap_pct,2) if gap_pct is not None else None,',
    "gap output",
)

score_old = '''    spread=metrics.get("spread_pct")
    if spread is not None:
        if spread<1:score+=4
        elif spread>5:score-=7; reasons.append("wide live spread")
    day=metrics.get("day_pct") or 0
'''
score_new = '''    spread=metrics.get("spread_pct")
    if spread is not None:
        if spread<1:score+=4
        elif spread>5:score-=7; reasons.append("wide live spread")
    setup_hist=(metrics.get("historical_analogs") or {}).get("setup_patterns") or {}
    if setup_hist.get("status")=="ok" and int(setup_hist.get("sample_count") or 0)>=5:
        hist_bias=fnum(setup_hist.get("bias_score"))
        if hist_bias is not None:
            score+=_clamp(hist_bias*.35,-7,7)
            if hist_bias>=6:reasons.append("bullish same-ticker setup history")
            elif hist_bias<=-6:reasons.append("bearish same-ticker setup history")
        failure=fnum(setup_hist.get("breakout_failure_pct"))
        follow=fnum(setup_hist.get("breakout_follow_through_pct"))
        if failure is not None and failure>=65 and (follow is None or follow<45):
            score-=3
            reasons.append("historical breakout failure risk")
    day=metrics.get("day_pct") or 0
'''
engine = replace_once(engine, score_old, score_new, "score historical patterns")

engine = replace_once(
    engine,
    '    hist=_hist_trade_context(metrics.get("historical_analogs"))\n    catalyst=_catalyst_bias(metrics.get("news") or [])\n',
    '    hist=_hist_trade_context(metrics.get("historical_analogs"))\n    setup_hist=(metrics.get("historical_analogs") or {}).get("setup_patterns") or {}\n    setup_intraday=setup_hist.get("intraday") or {}\n    catalyst=_catalyst_bias(metrics.get("news") or [])\n',
    "trade-plan setup context",
)
engine = replace_once(
    engine,
    '    if (hist.get("next_day_up_pct") or 0)>=65:confidence+=4\n    if catalyst.get("label")=="POSITIVE":confidence+=4\n',
    '    if (hist.get("next_day_up_pct") or 0)>=65:confidence+=4\n    hist_bias=fnum(setup_hist.get("bias_score"))\n    if setup_hist.get("status")=="ok" and int(setup_hist.get("sample_count") or 0)>=5 and hist_bias is not None:\n        confidence+=_clamp(hist_bias*.45,-8,8)\n    hist_fail=fnum(setup_hist.get("breakout_failure_pct"))\n    hist_follow=fnum(setup_hist.get("breakout_follow_through_pct"))\n    if hist_fail is not None and hist_fail>=60 and (hist_follow is None or hist_follow<=45):confidence-=5\n    if catalyst.get("label")=="POSITIVE":confidence+=4\n',
    "trade-plan historical confidence",
)
engine = replace_once(
    engine,
    '    reasons=[]\n    if severe_risk:\n',
    '    reasons=[]\n    if setup_hist.get("status")=="ok" and int(setup_hist.get("sample_count") or 0)>=5:\n        if setup_hist.get("bias_label")=="BULLISH":reasons.append("Same-ticker historical setup matches lean bullish.")\n        elif setup_hist.get("bias_label")=="BEARISH":reasons.append("Same-ticker historical setup matches lean bearish, so confirmation matters more.")\n        if (fnum(setup_hist.get("breakout_failure_pct")) or 0)>=60:reasons.append("Historical breakout-failure rate is elevated; prefer a hold/retest over a quick poke above resistance.")\n    if severe_risk:\n',
    "trade-plan historical reasons",
)
engine = replace_once(
    engine,
    '        "historical":{**hist,"relevance":"HIGH" if abs(day_pct)>=10 else "MODERATE" if abs(day_pct)>=6 else "LOW"},"catalyst":catalyst,"liquidity":liquidity,\n',
    '        "historical":{**hist,"relevance":"HIGH" if abs(day_pct)>=10 else "MODERATE" if abs(day_pct)>=6 else "LOW"},"historical_setup":setup_hist,"catalyst":catalyst,"liquidity":liquidity,\n',
    "trade-plan historical setup output",
)
engine = replace_once(
    engine,
    '"method_note":"Rule-based long momentum decision support using VWAP, support/resistance, ATR, momentum, volume pace, spread/liquidity, same-ticker historical spike behavior and catalyst context. Targets are scenarios, not guarantees.",',
    '"method_note":"Rule-based long momentum decision support using VWAP, support/resistance, ATR, momentum, volume pace, spread/liquidity, same-ticker spike analogs plus setup-matched gap/run-vs-fade behavior, breakout failure/follow-through, VWAP reclaim tendencies, early pullback depth, time-of-day behavior and catalyst context. Targets are scenarios, not guarantees.",',
    "method note",
)
engine_path.write_text(engine)

app_path = Path("analyzer_app.py")
app = app_path.read_text()

app = replace_once(
    app,
    '        "Historical analogs":histctx.get("sample_count",0),\n        "Analog relevance":histctx.get("relevance") or "—",\n',
    '        "Historical analogs":histctx.get("sample_count",0),\n        "Analog relevance":histctx.get("relevance") or "—",\n        "Setup-match bias":(plan.get("historical_setup") or {}).get("bias_label") or "—",\n        "Setup matches":(plan.get("historical_setup") or {}).get("sample_count",0),\n',
    "plan details setup fields",
)

hist_marker = '''h=r.get("historical_analogs") or {}
st.markdown('<div class="section">Historical spike analogs</div>',unsafe_allow_html=True)
'''
hist_ui = '''h=r.get("historical_analogs") or {}
hs=(h.get("setup_patterns") or plan.get("historical_setup") or {})
st.markdown('<div class="section">Historical setup match</div>',unsafe_allow_html=True)
if hs.get("status")=="ok":
    intr=hs.get("intraday") or {}
    hp=st.columns(6)
    bias=hs.get("bias_score")
    card(hp[0],"SETUP BIAS",hs.get("bias_label") or "MIXED",f'Score {bias:+.1f}' if bias is not None else "same-ticker history","good" if hs.get("bias_label")=="BULLISH" else "bad" if hs.get("bias_label")=="BEARISH" else "warn")
    card(hp[1],"SIMILAR DAYS",str(hs.get("sample_count",0)),hs.get("setup_label") or "setup matches")
    gr=hs.get("gap_run_pct"); gf=hs.get("gap_fade_pct")
    card(hp[2],"GAP RUN / FADE",f'{gr:.0f}% / {gf:.0f}%' if gr is not None and gf is not None else "—",f'n={hs.get("gap_sample_count",0)} gap analogs')
    bf=hs.get("breakout_follow_through_pct"); bfail=hs.get("breakout_failure_pct")
    card(hp[3],"BREAKOUT HOLD / FAIL",f'{bf:.0f}% / {bfail:.0f}%' if bf is not None and bfail is not None else "—",f'n={hs.get("breakout_test_count",0)} tested')
    vr=intr.get("vwap_reclaim_follow_through_pct")
    card(hp[4],"VWAP RECLAIM",f'{vr:.0f}% follow' if vr is not None else "—",f'n={intr.get("sample_count",0)} matched intraday days')
    pb=intr.get("median_first_pullback_pct")
    card(hp[5],"EARLY PULLBACK",pp(pb),f'High most often: {intr.get("session_high_most_common") or "—"}')
    for note in (hs.get("notes") or [])[:5]:
        st.caption("• "+str(note))
    matches=pd.DataFrame(hs.get("matches") or [])
    if not matches.empty:
        show=[c for c in ["date","pattern","gap_pct","day_pct","relative_volume","same_day_pullback_pct","next_day_pct","next_day_mfe_pct","breakout_follow","breakout_failed"] if c in matches.columns]
        st.dataframe(matches[show],width="stretch",hide_index=True)
        st.caption("Similarity combines today's move size, opening gap and relative-volume behavior. Recent matched 5-minute sessions add VWAP-reclaim, early-pullback and time-of-day tendencies when enough data is available.")
else:
    st.info("Not enough comparable same-ticker history for the setup-pattern layer yet.")

st.markdown('<div class="section">Historical spike analogs</div>',unsafe_allow_html=True)
'''
app = replace_once(app, hist_marker, hist_ui, "historical setup UI")
app = app.replace("UI=live-refresh-v5.0", "UI=historical-patterns-v6.0")
app_path.write_text(app)
