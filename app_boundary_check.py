"""Enforce the standalone Momentum Scanner / Analyzer app boundary.

The Trading Intelligence Lab may inspire or supply code that is copied/adapted
into this repository, but STOCK-SCANNER must not gain runtime imports or direct
package dependencies on the Lab.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FORBIDDEN_MODULE_PREFIXES = (
    "trading_intelligence",
    "predictive_ml_pipeline",
    "youtube_strategy",
    "trading_research_orchestrator",
    "machine_learning_lab_core",
    "trading_auto_research",
    "trading_validation_core",
)

SCAN_SUFFIXES = {".py"}
SKIP_DIRS = {".git", ".scanner_cache", "__pycache__", "scan_logs", "outcome_reports", "analyzer_outcomes"}


def _iter_python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _module_is_forbidden(module: str | None) -> bool:
    module = str(module or "")
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def check_runtime_import_boundary():
    violations = []
    for path in _iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            violations.append(f"{path.relative_to(ROOT)}: could not parse: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _module_is_forbidden(alias.name):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno} imports {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if _module_is_forbidden(node.module):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} imports from {node.module}"
                    )

    if violations:
        raise AssertionError(
            "Trading Intelligence Lab runtime dependency detected:\n- "
            + "\n- ".join(violations)
        )


if __name__ == "__main__":
    check_runtime_import_boundary()
    print("PASS standalone Scanner / Analyzer app boundary")
