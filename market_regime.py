"""Causal broad-market regime features for Swing / timeframe learning.

The functions in this module only inspect benchmark bars supplied by the caller.
Historical replay passes bars available through the replay date, so these
features can be reproduced live later without look-ahead leakage.
"""

from __future__ import annotations

import math
import statistics

BENCHMARKS = ("SPY", "QQQ", "IWM")


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _closes(bars):
    values = []
    for bar in bars or []:
        close = _num((bar or {}).get("c"))
        if close is not None and close > 0:
            values.append(close)
    return values


def _trailing_return(closes, sessions):
    if len(closes) <= sessions:
        return None
    base = closes[-1 - sessions]
    current = closes[-1]
    if base <= 0:
        return None
    return (current / base - 1.0) * 100.0


def _moving_average(closes, sessions):
    if len(closes) < sessions:
        return None
    window = closes[-sessions:]
    return sum(window) / float(sessions)


def _realized_volatility(closes, sessions=20):
    if len(closes) <= sessions:
        return None
    window = closes[-(sessions + 1):]
    returns = []
    for previous, current in zip(window, window[1:]):
        if previous > 0 and current > 0:
            returns.append(current / previous - 1.0)
    if len(returns) < max(10, sessions // 2):
        return None
    return statistics.pstdev(returns) * math.sqrt(252.0) * 100.0


def _drawdown_from_recent_high(closes, sessions=20):
    if not closes:
        return None
    window = closes[-max(1, sessions):]
    peak = max(window)
    if peak <= 0:
        return None
    return (closes[-1] / peak - 1.0) * 100.0


def market_regime_features(histories):
    """Return numeric regime features plus a descriptive regime label.

    histories is a mapping such as {"SPY": [bars...]} where each list is
    chronological and ends on the observation date.
    """
    closes = {
        symbol: _closes((histories or {}).get(symbol) or [])
        for symbol in BENCHMARKS
    }
    spy = closes["SPY"]
    qqq = closes["QQQ"]
    iwm = closes["IWM"]

    spy_5 = _trailing_return(spy, 5)
    spy_20 = _trailing_return(spy, 20)
    spy_60 = _trailing_return(spy, 60)
    qqq_20 = _trailing_return(qqq, 20)
    iwm_20 = _trailing_return(iwm, 20)

    spy_ma20 = _moving_average(spy, 20)
    spy_ma50 = _moving_average(spy, 50)
    spy_ma200 = _moving_average(spy, 200)
    spy_current = spy[-1] if spy else None

    def above_ma(closing_values, sessions):
        ma = _moving_average(closing_values, sessions)
        if not closing_values or ma is None:
            return None
        return 1.0 if closing_values[-1] >= ma else 0.0

    above20_values = [
        value
        for value in (
            above_ma(spy, 20),
            above_ma(qqq, 20),
            above_ma(iwm, 20),
        )
        if value is not None
    ]
    positive20_values = [
        value
        for value in (
            _trailing_return(spy, 20),
            _trailing_return(qqq, 20),
            _trailing_return(iwm, 20),
        )
        if value is not None
    ]

    above20_frac = (
        sum(above20_values) / len(above20_values)
        if above20_values else None
    )
    positive20_frac = (
        sum(value > 0 for value in positive20_values) / len(positive20_values)
        if positive20_values else None
    )

    spy_above_ma20 = (
        1.0 if spy_current is not None and spy_ma20 is not None and spy_current >= spy_ma20
        else 0.0 if spy_current is not None and spy_ma20 is not None
        else None
    )
    spy_above_ma50 = (
        1.0 if spy_current is not None and spy_ma50 is not None and spy_current >= spy_ma50
        else 0.0 if spy_current is not None and spy_ma50 is not None
        else None
    )
    spy_above_ma200 = (
        1.0 if spy_current is not None and spy_ma200 is not None and spy_current >= spy_ma200
        else 0.0 if spy_current is not None and spy_ma200 is not None
        else None
    )

    vol20 = _realized_volatility(spy, 20)
    drawdown20 = _drawdown_from_recent_high(spy, 20)
    qqq_minus_spy = (
        qqq_20 - spy_20
        if qqq_20 is not None and spy_20 is not None
        else None
    )
    iwm_minus_spy = (
        iwm_20 - spy_20
        if iwm_20 is not None and spy_20 is not None
        else None
    )

    regime_score = 50.0
    if spy_20 is not None:
        regime_score += max(-12.0, min(12.0, spy_20 * 1.2))
    if spy_60 is not None:
        regime_score += max(-10.0, min(10.0, spy_60 * 0.4))
    if above20_frac is not None:
        regime_score += (above20_frac - 0.5) * 16.0
    if positive20_frac is not None:
        regime_score += (positive20_frac - 0.5) * 14.0
    if spy_above_ma50 is not None:
        regime_score += 5.0 if spy_above_ma50 else -5.0
    if iwm_minus_spy is not None:
        regime_score += max(-5.0, min(5.0, iwm_minus_spy * 0.4))
    if vol20 is not None and vol20 >= 30.0:
        regime_score -= min(8.0, (vol20 - 30.0) * 0.25)
    regime_score = round(max(0.0, min(100.0, regime_score)), 1)

    if (
        spy_20 is not None
        and spy_20 >= 2.0
        and positive20_frac is not None
        and positive20_frac >= (2.0 / 3.0)
        and spy_above_ma50 == 1.0
    ):
        label = "RISK_ON"
    elif (
        spy_20 is not None
        and spy_20 <= -2.0
        and positive20_frac is not None
        and positive20_frac <= (1.0 / 3.0)
        and spy_above_ma50 == 0.0
    ):
        label = "RISK_OFF"
    elif vol20 is not None and vol20 >= 28.0:
        label = "VOLATILE"
    else:
        label = "MIXED"

    return {
        "regime_label": label,
        "regime_score": regime_score,
        "spy_return_5d_pct": round(spy_5, 3) if spy_5 is not None else None,
        "spy_return_20d_pct": round(spy_20, 3) if spy_20 is not None else None,
        "spy_return_60d_pct": round(spy_60, 3) if spy_60 is not None else None,
        "qqq_return_20d_pct": round(qqq_20, 3) if qqq_20 is not None else None,
        "iwm_return_20d_pct": round(iwm_20, 3) if iwm_20 is not None else None,
        "qqq_minus_spy_20d_pct": (
            round(qqq_minus_spy, 3) if qqq_minus_spy is not None else None
        ),
        "iwm_minus_spy_20d_pct": (
            round(iwm_minus_spy, 3) if iwm_minus_spy is not None else None
        ),
        "spy_above_ma20": spy_above_ma20,
        "spy_above_ma50": spy_above_ma50,
        "spy_above_ma200": spy_above_ma200,
        "benchmark_above_ma20_frac": (
            round(above20_frac, 4) if above20_frac is not None else None
        ),
        "benchmark_positive_20d_frac": (
            round(positive20_frac, 4) if positive20_frac is not None else None
        ),
        "spy_realized_vol_20d_pct": (
            round(vol20, 3) if vol20 is not None else None
        ),
        "spy_drawdown_20d_pct": (
            round(drawdown20, 3) if drawdown20 is not None else None
        ),
    }
