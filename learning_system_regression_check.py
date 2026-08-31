from __future__ import annotations

import tempfile
from pathlib import Path

from learning_system_audit import audit_observations, audit_source_contracts


def test_usde_like_endpoint_contradiction():
    rows = [
        {
            "symbol": "SYNTH",
            "scan_time_et": "2026-08-31T15:20:00-04:00",
            "score": 88,
            "return_60m_pct": 2.0,
            "mfe_60m_pct": 18.5,
            "mae_60m_pct": -1.4,
        },
        {
            "symbol": "CONTROL",
            "scan_time_et": "2026-08-31T14:00:00-04:00",
            "score": 60,
            "return_60m_pct": 4.0,
            "mfe_60m_pct": 5.0,
            "mae_60m_pct": -0.7,
        },
    ]
    result = audit_observations(rows)
    assert result["endpoint_label_contradictions_n"] == 1, result
    assert result["explosive_mfe_endpoint_misses_n"] == 1, result
    assert result["high_score_endpoint_misses_n"] == 1, result
    assert result["examples"][0]["symbol"] == "SYNTH", result


def test_source_contract_audit_finds_specification_gaps():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".github/workflows").mkdir(parents=True)
        (root / "scanner_ml_ranker.py").write_text(
            'TARGET_DESCRIPTION = ">= +3% at 60 minutes"\n'
            'label = int(return_60 >= 3.0)\n',
            encoding="utf-8",
        )
        (root / "score_outcomes.py").write_text(
            'def load_regular_session_scans(): pass\n'
            'if payload.get("mode") != "regular_market_session": pass\n'
            'row["mfe_60m_pct"] = 12.0\n',
            encoding="utf-8",
        )
        (root / "stock_scanner.py").write_text(
            "SCAN_LOG_TOP = 30\n",
            encoding="utf-8",
        )
        (root / "app.py").write_text(
            "# 2-minute scanner ON\n",
            encoding="utf-8",
        )
        (root / ".github/workflows/stock-scanner.yml").write_text(
            "on:\n  schedule:\n    - cron: '7,37 8-23 * * 1-5'\n",
            encoding="utf-8",
        )

        findings = audit_source_contracts(root)
        ids = {row["id"] for row in findings}
        assert "single_endpoint_primary_target" in ids, findings
        assert "extended_session_outcomes_excluded" in ids, findings
        assert "path_information_not_primary_target" in ids, findings
        assert "top_n_observation_censoring" in ids, findings
        assert "live_vs_durable_cadence_gap" in ids, findings


def main():
    test_usde_like_endpoint_contradiction()
    test_source_contract_audit_finds_specification_gaps()
    print("LEARNING_SYSTEM_REGRESSIONS=passed")


if __name__ == "__main__":
    main()
