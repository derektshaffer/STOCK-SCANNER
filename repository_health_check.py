"""Read-only development preflight; never repair, delete, or rewrite Git data.

Run from this canonical checkout with: python3 repository_health_check.py
Exit 0 means the checked Git metadata was available and git fsck passed now.
It does not pin files or guarantee they cannot be offloaded later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parent
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)


def check_repository(root=ROOT, *, timeout=60):
    root = Path(root).resolve()
    git_dir = root / ".git"
    result = {"repository": str(root), "ok": False, "repairs_performed": False}
    if not git_dir.is_dir() or git_dir.is_symlink():
        return dict(result, reason="Run in the canonical checkout with a local .git directory.")
    offloaded = []
    errors = []

    def onerror(exc):
        errors.append(type(exc).__name__)

    for parent, dirs, files in os.walk(git_dir, followlinks=False, onerror=onerror):
        for name in [".", *dirs, *files]:
            path = Path(parent) / name
            try:
                info = path.lstat()
                if getattr(info, "st_flags", 0) & SF_DATALESS:
                    offloaded.append(str(path.relative_to(root)))
            except OSError as exc:
                errors.append(type(exc).__name__)
    if offloaded or errors:
        return dict(result, reason="Git metadata is offloaded or unreadable; stop before editing.",
                    offloaded_count=len(set(offloaded)), offloaded_paths=sorted(set(offloaded)),
                    read_errors=errors)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "fsck", "--full"],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except subprocess.TimeoutExpired:
        return dict(result, reason="Git integrity check timed out; availability is unverified.")
    except OSError as exc:
        return dict(result, reason="Git integrity check could not start.", error_type=type(exc).__name__)
    return dict(result, ok=completed.returncode == 0, fsck_exit=completed.returncode,
                offloaded_count=0,
                reason="Git integrity passed." if completed.returncode == 0 else
                "Git integrity failed; preserve existing files and investigate before editing.",
                diagnostics=(completed.stdout + completed.stderr).strip())


if __name__ == "__main__":
    result = check_repository()
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        print("Restore local availability through the storage provider, then rerun this check. "
              "Do not delete Git files, reset, clean, or rebuild indexes as a first response.")
    raise SystemExit(0 if result["ok"] else 1)
