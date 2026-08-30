"""Out-of-sample research for predictive Swing setup conditions.

This module searches the multi-year historical replay for simple, explainable
stock-specific conditions and two-condition combinations that separate better
Swing setups from worse ones.

Important safeguards:
- candidate thresholds are discovered on the earlier 65% of replay dates only;
- the later 35% is untouched until frozen candidates are evaluated;
- pairs are built only from discovery-qualified single-feature rules;
- broad market-regime features are excluded from candidate discovery because the
  prior A/B test showed they degraded the Swing model;
- calendar-year and market-regime breakdowns are validation diagnostics only;
- nothing here can alter live Analyzer scoring.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path

import timeframe_ml_ranker as tml

DATA_PATH = Path("timeframe_replay/timeframe_historical_replay.json")
OUTPUT_PATH = Path("timeframe_replay/swing_feature_research.json")
RESEARCH_VERSION = "swing-feature-research-v1"

EXCLUDED_DISCOVERY_FEATURES = {
    "market_score",
    "broad_market_avg_pct",
}
RESEARCH_FEATURES = [
    name
    for name in tml.BASE_FEATURES
    if name not in EXCLUDED_DISCOVERY_FEATURES
]

DISCOVERY_FRACTION = 0.65
QUANTILES = (0.20, 0.35, 0.50, 0.65, 0.80)
MIN_DISCOVERY_SINGLE_N = 150
MIN_DISCOVERY_PAIR_N = 100
MIN_CONFIRMATION_SINGLE_N = 100
MIN_CONFIRMATION_PAIR_N = 75
MIN_YEAR_RULE_N = 40


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _quantile(values, q):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * float(q)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _discovery_split(rows, fraction=DISCOVERY_FRACTION):
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 20:
        return rows, [], None
    split_index = max(1, min(len(dates) - 1, int(len(dates) * fraction)))
    discovery_dates = set(dates[:split_index])
    confirmation_dates = set(dates[split_index:])
    discovery = [row for row in rows if row["date"] in discovery_dates]
    confirmation = [row for row in rows if row["date"] in confirmation_dates]
    return discovery, confirmation, dates[split_index]


def _rule_matches(row, rule):
    value = _num((row.get("features") or {}).get(rule["feature"]))
    if value is None:
        return False
    threshold = float(rule["threshold"])
    if rule["op"] == ">=":
        return value >= threshold
    if rule["op"] == "<=":
        return value <= threshold
    raise ValueError(f"Unsupported rule op: {rule['op']}")


def _candidate_matches(row, candidate):
    return all(_rule_matches(row, rule) for rule in candidate["rules"])


def _pooled_z(wins_a, n_a, wins_b, n_b):
    if n_a <= 0 or n_b <= 0:
        return None
    p_a = wins_a / n_a
    p_b = wins_b / n_b
    pooled = (wins_a + wins_b) / (n_a + n_b)
    variance = pooled * (1.0 - pooled) * (1.0 / n_a + 1.0 / n_b)
    if variance <= 0:
        return None
    return (p_a - p_b) / math.sqrt(variance)


def _average(rows, field):
    values = [_num(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    return round(statistics.mean(values), 3) if values else None


def _stats(rows, candidate):
    usable = [
        row
        for row in rows
        if all(
            _num((row.get("features") or {}).get(rule["feature"])) is not None
            for rule in candidate["rules"]
        )
    ]
    selected = [row for row in usable if _candidate_matches(row, candidate)]
    remainder = [row for row in usable if not _candidate_matches(row, candidate)]

    n = len(selected)
    rest_n = len(remainder)
    wins = sum(int(row["label"]) for row in selected)
    rest_wins = sum(int(row["label"]) for row in remainder)
    rate = wins / n * 100.0 if n else None
    rest_rate = rest_wins / rest_n * 100.0 if rest_n else None
    lift = rate - rest_rate if rate is not None and rest_rate is not None else None
    z_score = _pooled_z(wins, n, rest_wins, rest_n)

    return {
        "n": n,
        "rest_n": rest_n,
        "target_before_stop_rate_pct": round(rate, 1) if rate is not None else None,
        "rest_target_rate_pct": round(rest_rate, 1) if rest_rate is not None else None,
        "lift_pp": round(lift, 1) if lift is not None else None,
        "z_score": round(z_score, 3) if z_score is not None else None,
        "avg_return_5d_pct": _average(selected, "return_5d_pct"),
        "avg_mfe_5d_pct": _average(selected, "mfe_5d_pct"),
        "avg_mae_5d_pct": _average(selected, "mae_5d_pct"),
        "avg_excess_vs_spy_5d_pct": _average(
            selected,
            "excess_return_vs_spy_5d_pct",
        ),
    }


def _rule_text(rule):
    threshold = float(rule["threshold"])
    if abs(threshold) >= 1000:
        formatted = f"{threshold:,.0f}"
    elif abs(threshold) >= 100:
        formatted = f"{threshold:.1f}"
    else:
        formatted = f"{threshold:.3f}".rstrip("0").rstrip(".")
    return f"{rule['feature']} {rule['op']} {formatted}"


def _candidate_text(candidate):
    return " AND ".join(_rule_text(rule) for rule in candidate["rules"])


def _single_rule_pool(discovery):
    rules = []
    for feature in RESEARCH_FEATURES:
        values = [
            _num((row.get("features") or {}).get(feature))
            for row in discovery
        ]
        values = [value for value in values if value is not None]
        if len(values) < MIN_DISCOVERY_SINGLE_N * 2:
            continue
        unique = sorted(set(values))

        thresholds = []
        if len(unique) <= 4:
            midpoint = (min(unique) + max(unique)) / 2.0
            thresholds.append((midpoint, "binary_midpoint"))
        else:
            for q in QUANTILES:
                value = _quantile(values, q)
                if value is not None:
                    thresholds.append((value, f"q{int(q * 100):02d}"))

        seen = set()
        for threshold, source in thresholds:
            rounded = round(float(threshold), 8)
            for op in ("<=", ">="):
                key = (feature, op, rounded)
                if key in seen:
                    continue
                seen.add(key)
                rules.append(
                    {
                        "feature": feature,
                        "op": op,
                        "threshold": rounded,
                        "threshold_source": source,
                    }
                )
    return rules


def _candidate(rule_or_rules):
    rules = (
        list(rule_or_rules)
        if isinstance(rule_or_rules, (list, tuple))
        else [rule_or_rules]
    )
    return {
        "rules": rules,
        "text": " AND ".join(_rule_text(rule) for rule in rules),
    }


def _discovery_single_candidates(discovery):
    qualified = []
    all_results = []
    for rule in _single_rule_pool(discovery):
        candidate = _candidate(rule)
        stats = _stats(discovery, candidate)
        item = {**candidate, "discovery": stats}
        all_results.append(item)
        if (
            stats["n"] >= MIN_DISCOVERY_SINGLE_N
            and stats["rest_n"] >= MIN_DISCOVERY_SINGLE_N
            and (stats["lift_pp"] or -999) >= 4.0
            and (stats["z_score"] or -999) >= 1.5
        ):
            qualified.append(item)

    qualified.sort(
        key=lambda item: (
            item["discovery"]["lift_pp"],
            item["discovery"]["z_score"],
            math.log1p(item["discovery"]["n"]),
        ),
        reverse=True,
    )
    all_results.sort(
        key=lambda item: (
            item["discovery"]["lift_pp"] or -999,
            item["discovery"]["z_score"] or -999,
        ),
        reverse=True,
    )

    # Keep discovery diverse so one feature with neighboring quantiles cannot
    # monopolize the pair search.
    diverse = []
    per_feature = Counter()
    for item in qualified:
        feature = item["rules"][0]["feature"]
        if per_feature[feature] >= 2:
            continue
        per_feature[feature] += 1
        diverse.append(item)
        if len(diverse) >= 20:
            break
    return diverse, all_results[:40]


def _discovery_pair_candidates(discovery, singles):
    pairs = []
    for left_index, left in enumerate(singles):
        left_rule = left["rules"][0]
        for right in singles[left_index + 1 :]:
            right_rule = right["rules"][0]
            if left_rule["feature"] == right_rule["feature"]:
                continue
            candidate = _candidate([left_rule, right_rule])
            stats = _stats(discovery, candidate)
            if (
                stats["n"] >= MIN_DISCOVERY_PAIR_N
                and stats["rest_n"] >= MIN_DISCOVERY_PAIR_N
                and (stats["lift_pp"] or -999) >= 6.0
                and (stats["z_score"] or -999) >= 1.5
            ):
                pairs.append({**candidate, "discovery": stats})

    pairs.sort(
        key=lambda item: (
            item["discovery"]["lift_pp"],
            item["discovery"]["z_score"],
            math.log1p(item["discovery"]["n"]),
        ),
        reverse=True,
    )
    return pairs[:50]


def _year_stability(rows, candidate):
    years = sorted({row["date"][:4] for row in rows})
    by_year = {}
    positive = 0
    eligible = 0
    worst_lift = None
    for year in years:
        year_rows = [row for row in rows if row["date"].startswith(year)]
        stats = _stats(year_rows, candidate)
        by_year[year] = stats
        if stats["n"] < MIN_YEAR_RULE_N or stats["rest_n"] < MIN_YEAR_RULE_N:
            continue
        eligible += 1
        lift = stats["lift_pp"]
        if lift is not None and lift > 0:
            positive += 1
        if lift is not None:
            worst_lift = lift if worst_lift is None else min(worst_lift, lift)

    return {
        "eligible_years": eligible,
        "positive_lift_years": positive,
        "positive_year_fraction": (
            round(positive / eligible, 3) if eligible else None
        ),
        "worst_year_lift_pp": worst_lift,
        "by_year": by_year,
    }


def _regime_breakdown(rows, candidate):
    groups = {}
    for row in rows:
        label = str(
            (row.get("market_context") or {}).get("regime_label")
            or "UNKNOWN"
        )
        groups.setdefault(label, []).append(row)
    return {
        label: _stats(group_rows, candidate)
        for label, group_rows in sorted(groups.items())
    }


def _is_robust(item):
    confirmation = item["confirmation"]
    discovery = item["discovery"]
    stability = item["year_stability"]
    rule_count = len(item["rules"])
    min_confirmation_n = (
        MIN_CONFIRMATION_SINGLE_N if rule_count == 1 else MIN_CONFIRMATION_PAIR_N
    )
    min_discovery_lift = 4.0 if rule_count == 1 else 6.0
    min_confirmation_lift = 2.5 if rule_count == 1 else 3.0

    eligible_years = int(stability.get("eligible_years") or 0)
    positive_years = int(stability.get("positive_lift_years") or 0)
    required_positive_years = max(3, math.ceil(eligible_years * 0.60))

    return bool(
        discovery["n"] >= (
            MIN_DISCOVERY_SINGLE_N
            if rule_count == 1
            else MIN_DISCOVERY_PAIR_N
        )
        and (discovery["lift_pp"] or -999) >= min_discovery_lift
        and confirmation["n"] >= min_confirmation_n
        and confirmation["rest_n"] >= min_confirmation_n
        and (confirmation["lift_pp"] or -999) >= min_confirmation_lift
        and (confirmation["z_score"] or -999) >= 1.0
        and eligible_years >= 4
        and positive_years >= required_positive_years
        and (stability.get("worst_year_lift_pp") or -999) >= -7.0
    )


def _evaluate_frozen(candidates, confirmation, all_rows):
    evaluated = []
    for item in candidates:
        frozen = {
            "rules": item["rules"],
            "text": item["text"],
            "discovery": item["discovery"],
        }
        frozen["confirmation"] = _stats(confirmation, frozen)
        frozen["full_history"] = _stats(all_rows, frozen)
        frozen["year_stability"] = _year_stability(all_rows, frozen)
        frozen["regime_breakdown"] = _regime_breakdown(all_rows, frozen)
        frozen["robust_research_candidate"] = _is_robust(frozen)
        evaluated.append(frozen)

    evaluated.sort(
        key=lambda item: (
            bool(item["robust_research_candidate"]),
            item["confirmation"]["lift_pp"] or -999,
            item["confirmation"]["z_score"] or -999,
            item["confirmation"]["n"],
        ),
        reverse=True,
    )
    return evaluated


def main():
    rows, source = tml.load_rows(DATA_PATH)
    if len(rows) < 2500:
        raise RuntimeError(
            f"Swing feature research needs >=2500 labeled replay rows; found {len(rows)}."
        )

    discovery, confirmation, confirmation_start = _discovery_split(rows)
    if len(confirmation) < 500:
        raise RuntimeError(
            f"Confirmation holdout is too small: {len(confirmation)} rows."
        )

    singles, top_screen = _discovery_single_candidates(discovery)
    pairs = _discovery_pair_candidates(discovery, singles)
    frozen = singles + pairs
    evaluated = _evaluate_frozen(frozen, confirmation, rows)

    robust = [item for item in evaluated if item["robust_research_candidate"]]
    rejected = [item for item in evaluated if not item["robust_research_candidate"]]

    payload = {
        "schema_version": 1,
        "research_version": RESEARCH_VERSION,
        "source_replay_version": source.get("replay_version"),
        "source_generated_at_utc": source.get("generated_at_utc"),
        "status": (
            "robust_patterns_found"
            if robust
            else "no_robust_patterns_yet"
        ),
        "production_enabled": False,
        "target": source.get("summary", {}).get("swing_path_target") or {},
        "design": {
            "discovery_fraction": DISCOVERY_FRACTION,
            "discovery_samples": len(discovery),
            "confirmation_samples": len(confirmation),
            "discovery_dates": len({row["date"] for row in discovery}),
            "confirmation_dates": len({row["date"] for row in confirmation}),
            "confirmation_start": confirmation_start,
            "calendar_years": sorted({row["date"][:4] for row in rows}),
            "research_features": RESEARCH_FEATURES,
            "excluded_discovery_features": sorted(EXCLUDED_DISCOVERY_FEATURES),
            "candidate_threshold_quantiles": list(QUANTILES),
            "note": (
                "Thresholds and pair candidates are selected only on the earlier "
                "discovery period. Confirmation statistics are computed only after "
                "rules are frozen. Market regimes are diagnostics, not discovery "
                "inputs."
            ),
        },
        "summary": {
            "labeled_rows": len(rows),
            "discovery_qualified_singles": len(singles),
            "discovery_qualified_pairs": len(pairs),
            "frozen_candidates_tested": len(evaluated),
            "robust_research_candidates": len(robust),
        },
        "robust_candidates": robust[:20],
        "best_confirmation_candidates": evaluated[:25],
        "top_discovery_single_screen": top_screen[:25],
        "not_confirmed": rejected[:25],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    compact = []
    for item in robust[:10]:
        compact.append(
            {
                "setup": item["text"],
                "discovery_lift_pp": item["discovery"]["lift_pp"],
                "confirmation_lift_pp": item["confirmation"]["lift_pp"],
                "confirmation_n": item["confirmation"]["n"],
                "positive_years": item["year_stability"]["positive_lift_years"],
                "eligible_years": item["year_stability"]["eligible_years"],
            }
        )

    print(
        "SWING_FEATURE_RESEARCH_SUMMARY="
        + json.dumps(payload["summary"], sort_keys=True)
    )
    print(
        "SWING_FEATURE_RESEARCH_STATUS="
        + str(payload["status"])
    )
    print(
        "SWING_FEATURE_ROBUST="
        + json.dumps(compact, sort_keys=True)
    )
    print(
        "SWING_FEATURE_BEST_CONFIRMATION="
        + json.dumps(
            [
                {
                    "setup": item["text"],
                    "discovery_lift_pp": item["discovery"]["lift_pp"],
                    "confirmation_lift_pp": item["confirmation"]["lift_pp"],
                    "confirmation_n": item["confirmation"]["n"],
                    "robust": item["robust_research_candidate"],
                }
                for item in evaluated[:10]
            ],
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
