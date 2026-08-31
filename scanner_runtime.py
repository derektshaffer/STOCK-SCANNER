"""Shared runtime for live Momentum Scanner processes.

The combined app uses a non-blocking child process for automatic scans so the
Analyzer remains responsive. Manual/standalone scans can still use the
synchronous wrapper. A filesystem lock prevents Scanner/Analyzer/browser
sessions from launching overlapping scanner processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SCAN_FILE = Path("scan_logs/latest_scan.json")
LOCK_FILE = Path(tempfile.gettempdir()) / "stock-scanner-live.lock"
LOCK_STALE_SECONDS = 120.0


def _provider_error():
    return {
        "ok": False,
        "started": False,
        "busy": False,
        "message": (
            "No market-data provider is configured. Add either "
            "TRADIER_ACCESS_TOKEN (preferred) or both ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY in Streamlit Secrets."
        ),
        "stdout": "",
        "stderr": "",
        "runtime_seconds": None,
    }


def _build_env(
    *,
    alpaca_key="",
    alpaca_secret="",
    alpaca_live_feed="iex",
    tradier_token="",
    discovery_universe_size="1200",
    learning_github_token="",
    learning_repository="",
    learning_branch="learning-journal",
):
    alpaca_key = str(alpaca_key or "").strip()
    alpaca_secret = str(alpaca_secret or "").strip()
    tradier_token = str(tradier_token or "").strip()
    feed = str(alpaca_live_feed or "iex").strip().lower()
    if feed not in {"iex", "sip"}:
        feed = "iex"

    has_alpaca = bool(alpaca_key and alpaca_secret)
    if not has_alpaca and not tradier_token:
        return None

    env = os.environ.copy()
    if has_alpaca:
        env["ALPACA_API_KEY"] = alpaca_key
        env["ALPACA_SECRET_KEY"] = alpaca_secret
    else:
        env.pop("ALPACA_API_KEY", None)
        env.pop("ALPACA_SECRET_KEY", None)
    env["ALPACA_LIVE_FEED"] = feed

    if tradier_token:
        env["TRADIER_ACCESS_TOKEN"] = tradier_token
        env["SCANNER_TRADIER_DISCOVERY"] = "1"
        env["SCANNER_DISCOVERY_UNIVERSE_SIZE"] = str(discovery_universe_size)

    learning_github_token = str(learning_github_token or "").strip()
    learning_repository = str(learning_repository or "").strip()
    learning_branch = str(learning_branch or "learning-journal").strip() or "learning-journal"
    if learning_github_token:
        env["SCANNER_LIVE_JOURNAL_ENABLED"] = "1"
        env["SCANNER_LEARNING_GITHUB_TOKEN"] = learning_github_token
        env["SCANNER_LEARNING_GITHUB_REPO"] = (
            learning_repository or "derektshaffer/STOCK-SCANNER"
        )
        env["SCANNER_LEARNING_GITHUB_BRANCH"] = learning_branch
    else:
        env.pop("SCANNER_LIVE_JOURNAL_ENABLED", None)
        env.pop("SCANNER_LEARNING_GITHUB_TOKEN", None)

    return env


def _read_lock():
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _lock_age_seconds(payload=None):
    payload = payload or _read_lock() or {}
    try:
        return max(0.0, time.time() - float(payload.get("created_at") or 0.0))
    except Exception:
        return None


def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _update_lock_child_pid(token, child_pid):
    payload = _read_lock() or {}
    if not token or payload.get("token") != token:
        return
    payload["child_pid"] = int(child_pid)
    try:
        LOCK_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _scan_marker():
    if not SCAN_FILE.exists():
        return {"mtime": None, "scan_time_et": None}
    try:
        mtime = SCAN_FILE.stat().st_mtime
    except Exception:
        mtime = None
    scan_time = None
    try:
        payload = json.loads(SCAN_FILE.read_text(encoding="utf-8"))
        scan_time = payload.get("scan_time_et")
    except Exception:
        pass
    return {"mtime": mtime, "scan_time_et": scan_time}


def _scan_marker_changed(before, after):
    before = before or {}
    after = after or {}
    if (
        after.get("scan_time_et")
        and after.get("scan_time_et") != before.get("scan_time_et")
    ):
        return True
    before_mtime = before.get("mtime")
    after_mtime = after.get("mtime")
    try:
        return (
            after_mtime is not None
            and (
                before_mtime is None
                or float(after_mtime) > float(before_mtime)
            )
        )
    except Exception:
        return False


def _clear_stale_lock():
    payload = _read_lock()
    if payload is None:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return True

    child_pid = payload.get("child_pid")
    if child_pid and not _pid_alive(child_pid):
        try:
            LOCK_FILE.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    age = _lock_age_seconds(payload)
    if age is not None and age > LOCK_STALE_SECONDS:
        try:
            LOCK_FILE.unlink(missing_ok=True)
            return True
        except Exception:
            return False
    return False


def _acquire_scan_lock():
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "created_at": time.time(),
        "pid": os.getpid(),
    }
    for _ in range(2):
        try:
            fd = os.open(
                str(LOCK_FILE),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return token
        except FileExistsError:
            if not _clear_stale_lock():
                return None
        except Exception:
            return None
    return None


def _release_scan_lock(token):
    if not token:
        return
    payload = _read_lock()
    if payload and payload.get("token") != token:
        return
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def scanner_process_busy():
    if not LOCK_FILE.exists():
        return False
    if _clear_stale_lock():
        return False
    return LOCK_FILE.exists()


def _log_path(kind):
    fd, raw_path = tempfile.mkstemp(prefix="momentum-scan-", suffix=f".{kind}")
    os.close(fd)
    return Path(raw_path)


def _read_log(path, limit):
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return text[-limit:]
    except Exception:
        return ""


def _cleanup_logs(state):
    for key in ("stdout_path", "stderr_path"):
        raw = state.get(key)
        if not raw:
            continue
        try:
            Path(raw).unlink(missing_ok=True)
        except Exception:
            pass


def _command_or_default(command=None):
    return list(command) if command else [sys.executable, "stock_scanner.py"]


def start_scanner_process(
    *,
    alpaca_key="",
    alpaca_secret="",
    alpaca_live_feed="iex",
    tradier_token="",
    discovery_universe_size="1200",
    learning_github_token="",
    learning_repository="",
    learning_branch="learning-journal",
    timeout_seconds=180,
    command=None,
    require_scan_file=True,
):
    """Start a scanner child process and return immediately."""
    env = _build_env(
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        alpaca_live_feed=alpaca_live_feed,
        tradier_token=tradier_token,
        discovery_universe_size=discovery_universe_size,
        learning_github_token=learning_github_token,
        learning_repository=learning_repository,
        learning_branch=learning_branch,
    )
    if env is None:
        return _provider_error()

    token = _acquire_scan_lock()
    if not token:
        return {
            "ok": False,
            "started": False,
            "busy": True,
            "message": "A momentum scan is already running.",
            "stdout": "",
            "stderr": "",
            "runtime_seconds": None,
        }

    stdout_path = _log_path("out")
    stderr_path = _log_path("err")
    scan_marker_before = _scan_marker()
    out_handle = None
    err_handle = None
    try:
        out_handle = stdout_path.open("w", encoding="utf-8")
        err_handle = stderr_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            _command_or_default(command),
            env=env,
            stdout=out_handle,
            stderr=err_handle,
            text=True,
        )
        _update_lock_child_pid(token, process.pid)
    except Exception as exc:
        _release_scan_lock(token)
        for handle in (out_handle, err_handle):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass
        _cleanup_logs(
            {"stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}
        )
        return {
            "ok": False,
            "started": False,
            "busy": False,
            "message": f"Could not start scanner: {exc}",
            "stdout": "",
            "stderr": "",
            "runtime_seconds": None,
        }
    finally:
        for handle in (out_handle, err_handle):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass

    return {
        "ok": None,
        "started": True,
        "busy": False,
        "message": "Momentum scan started.",
        "process": process,
        "lock_token": token,
        "started_at": time.time(),
        "timeout_seconds": float(timeout_seconds),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "require_scan_file": bool(require_scan_file),
        "scan_marker_before": scan_marker_before,
    }


def poll_scanner_process(state):
    """Poll a state returned by start_scanner_process without blocking."""
    state = state or {}
    process = state.get("process")
    if process is None:
        return {
            "done": True,
            "ok": False,
            "busy": False,
            "message": "Scanner process state is missing.",
            "runtime_seconds": None,
            "stdout": "",
            "stderr": "",
        }

    started_at = float(state.get("started_at") or time.time())
    elapsed = max(0.0, time.time() - started_at)
    timeout_seconds = float(state.get("timeout_seconds") or 180.0)
    returncode = process.poll()

    timed_out = False
    if returncode is None and elapsed > timeout_seconds:
        timed_out = True
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        returncode = process.poll()

    if returncode is None:
        return {
            "done": False,
            "ok": None,
            "busy": True,
            "message": f"Momentum scan running ({elapsed:.1f}s).",
            "runtime_seconds": round(elapsed, 1),
        }

    stdout = _read_log(state.get("stdout_path"), 12000)
    stderr = _read_log(state.get("stderr_path"), 6000)
    _release_scan_lock(state.get("lock_token"))
    _cleanup_logs(state)

    elapsed = round(max(0.0, time.time() - started_at), 1)
    if timed_out:
        return {
            "done": True,
            "ok": False,
            "busy": False,
            "message": (
                f"The scanner exceeded its {int(timeout_seconds)}-second timeout."
            ),
            "runtime_seconds": elapsed,
            "stdout": stdout,
            "stderr": stderr,
        }

    if returncode != 0:
        error = stderr.strip() or stdout.strip() or "Unknown scanner error"
        return {
            "done": True,
            "ok": False,
            "busy": False,
            "message": error[-3000:],
            "runtime_seconds": elapsed,
            "stdout": stdout,
            "stderr": stderr,
        }

    require_scan_file = bool(state.get("require_scan_file", True))
    marker_before = state.get("scan_marker_before") or {}
    marker_after = _scan_marker()
    fresh_snapshot = _scan_marker_changed(marker_before, marker_after)
    ok = True if not require_scan_file else bool(
        SCAN_FILE.exists() and fresh_snapshot
    )
    return {
        "done": True,
        "ok": ok,
        "busy": False,
        "message": (
            f"Fresh scan complete in {elapsed:.1f}s."
            if ok
            else (
                "Scanner process exited, but it did not write a fresh "
                "latest_scan.json snapshot."
            )
        ),
        "runtime_seconds": elapsed,
        "stdout": stdout,
        "stderr": stderr,
    }


def cancel_scanner_process(state):
    """Terminate an active async scanner process and release its shared lock."""
    state = state or {}
    process = state.get("process")
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass

    stdout = _read_log(state.get("stdout_path"), 12000)
    stderr = _read_log(state.get("stderr_path"), 6000)
    _release_scan_lock(state.get("lock_token"))
    _cleanup_logs(state)
    return {
        "done": True,
        "ok": False,
        "cancelled": True,
        "busy": False,
        "message": "Momentum scan cancelled.",
        "runtime_seconds": round(
            max(0.0, time.time() - float(state.get("started_at") or time.time())),
            1,
        ),
        "stdout": stdout,
        "stderr": stderr,
    }


def run_scanner_process(
    *,
    alpaca_key="",
    alpaca_secret="",
    alpaca_live_feed="iex",
    tradier_token="",
    discovery_universe_size="1200",
    learning_github_token="",
    learning_repository="",
    learning_branch="learning-journal",
    timeout_seconds=180,
):
    """Blocking wrapper retained for manual and standalone Scanner use."""
    env = _build_env(
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        alpaca_live_feed=alpaca_live_feed,
        tradier_token=tradier_token,
        discovery_universe_size=discovery_universe_size,
        learning_github_token=learning_github_token,
        learning_repository=learning_repository,
        learning_branch=learning_branch,
    )
    if env is None:
        return _provider_error()

    token = _acquire_scan_lock()
    if not token:
        return {
            "ok": False,
            "started": False,
            "busy": True,
            "message": "A momentum scan is already running.",
            "stdout": "",
            "stderr": "",
            "runtime_seconds": None,
        }

    scan_marker_before = _scan_marker()
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [sys.executable, "stock_scanner.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "started": True,
            "busy": False,
            "message": (
                f"The scanner exceeded its {int(timeout_seconds)}-second timeout."
            ),
            "stdout": "",
            "stderr": "",
            "runtime_seconds": round(time.perf_counter() - started, 1),
        }
    finally:
        _release_scan_lock(token)

    elapsed = round(time.perf_counter() - started, 1)
    stdout = process.stdout or ""
    stderr = process.stderr or ""

    if process.returncode != 0:
        error = stderr.strip() or stdout.strip() or "Unknown scanner error"
        return {
            "ok": False,
            "started": True,
            "busy": False,
            "message": error[-3000:],
            "stdout": stdout,
            "stderr": stderr,
            "runtime_seconds": elapsed,
        }

    fresh_snapshot = _scan_marker_changed(
        scan_marker_before,
        _scan_marker(),
    )
    ok = bool(SCAN_FILE.exists() and fresh_snapshot)
    return {
        "ok": ok,
        "started": True,
        "busy": False,
        "message": (
            f"Fresh scan complete in {elapsed:.1f}s."
            if ok
            else (
                "Scanner process exited, but it did not write a fresh "
                "latest_scan.json snapshot."
            )
        ),
        "stdout": stdout,
        "stderr": stderr,
        "runtime_seconds": elapsed,
    }


def cadence_health(runtime_seconds, target_seconds=120.0):
    try:
        runtime = float(runtime_seconds)
    except Exception:
        return {
            "status": "unknown",
            "headroom_seconds": None,
            "message": "Scanner runtime has not been measured yet.",
        }

    headroom = round(float(target_seconds) - runtime, 1)
    if runtime >= float(target_seconds):
        status = "overrun"
        message = (
            f"Last scan took {runtime:.1f}s, longer than the "
            f"{int(target_seconds)}s target cadence."
        )
    elif runtime >= 90.0:
        status = "tight"
        message = (
            f"Last scan took {runtime:.1f}s; only {max(0.0, headroom):.1f}s "
            "of two-minute cadence headroom remains."
        )
    else:
        status = "healthy"
        message = (
            f"Last scan took {runtime:.1f}s with "
            f"{max(0.0, headroom):.1f}s of cadence headroom."
        )
    return {
        "status": status,
        "headroom_seconds": headroom,
        "message": message,
    }
