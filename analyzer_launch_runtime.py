"""Cancelable background runtime for Scanner -> Analyzer launches."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _result_path():
    return Path(tempfile.gettempdir()) / f"stock-analyzer-launch-{uuid.uuid4().hex}.json"


def _log_path(kind):
    return Path(tempfile.gettempdir()) / f"stock-analyzer-launch-{uuid.uuid4().hex}.{kind}"


def _cleanup(state):
    for key in ("result_path","stdout_path","stderr_path"):
        value=(state or {}).get(key)
        if not value:
            continue
        try:
            Path(value).unlink(missing_ok=True)
        except Exception:
            pass


def _read_text(path, limit):
    try:
        text=Path(path).read_text(encoding="utf-8")
        return text[-int(limit):]
    except Exception:
        return ""


def start_analyzer_process(
    symbol,
    *,
    alpaca_key="",
    alpaca_secret="",
    alpaca_live_feed="iex",
    tradier_token="",
    timeout_seconds=180,
):
    symbol=str(symbol or "").upper().strip()
    if not symbol:
        return {"started":False,"ok":False,"message":"No ticker was selected."}

    env=os.environ.copy()
    if alpaca_key:
        env["ALPACA_API_KEY"]=str(alpaca_key)
    if alpaca_secret:
        env["ALPACA_SECRET_KEY"]=str(alpaca_secret)
    feed=str(alpaca_live_feed or "iex").lower().strip()
    env["ALPACA_LIVE_FEED"]=feed if feed in {"iex","sip"} else "iex"
    if tradier_token:
        env["TRADIER_ACCESS_TOKEN"]=str(tradier_token)
    # The launch subprocess should compute snapshots/analysis, not open a
    # long-lived market-stream session or block the page on prediction-log
    # persistence. Those are handled by the persistent UI/background tracker.
    env["ANALYZER_BACKGROUND_WORKER"]="1"

    result_path=_result_path()
    stdout_path=_log_path("out")
    stderr_path=_log_path("err")
    out_handle=stdout_path.open("w",encoding="utf-8")
    err_handle=stderr_path.open("w",encoding="utf-8")
    try:
        process=subprocess.Popen(
            [sys.executable,"analyzer_launch_worker.py",symbol,str(result_path)],
            env=env,
            stdout=out_handle,
            stderr=err_handle,
            text=True,
        )
    except Exception as exc:
        out_handle.close()
        err_handle.close()
        _cleanup({
            "result_path":str(result_path),
            "stdout_path":str(stdout_path),
            "stderr_path":str(stderr_path),
        })
        return {
            "started":False,
            "ok":False,
            "message":f"Could not start Analyzer: {exc}",
        }
    finally:
        try:
            out_handle.close()
        except Exception:
            pass
        try:
            err_handle.close()
        except Exception:
            pass

    return {
        "started":True,
        "ok":None,
        "symbol":symbol,
        "process":process,
        "started_at":time.time(),
        "timeout_seconds":float(timeout_seconds),
        "result_path":str(result_path),
        "stdout_path":str(stdout_path),
        "stderr_path":str(stderr_path),
    }


def cancel_analyzer_process(state):
    state=state or {}
    process=state.get("process")
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass
    symbol=str(state.get("symbol") or "")
    _cleanup(state)
    return {
        "done":True,
        "cancelled":True,
        "ok":False,
        "symbol":symbol,
        "message":f"{symbol} analysis cancelled." if symbol else "Analysis cancelled.",
    }


def poll_analyzer_process(state):
    state=state or {}
    process=state.get("process")
    if process is None:
        return {"done":True,"ok":False,"message":"Analyzer process state is missing."}

    started_at=float(state.get("started_at") or time.time())
    elapsed=max(0.0,time.time()-started_at)
    timeout=float(state.get("timeout_seconds") or 180.0)
    code=process.poll()

    if code is None and elapsed > timeout:
        cancel_analyzer_process(state)
        return {
            "done":True,
            "ok":False,
            "message":f"Analyzer exceeded its {int(timeout)}-second timeout.",
            "runtime_seconds":round(elapsed,1),
        }

    if code is None:
        return {
            "done":False,
            "ok":None,
            "symbol":state.get("symbol"),
            "runtime_seconds":round(elapsed,1),
        }

    stdout=_read_text(state.get("stdout_path"),8000)
    stderr=_read_text(state.get("stderr_path"),8000)
    payload=None
    try:
        payload=json.loads(Path(state.get("result_path")).read_text(encoding="utf-8"))
    except Exception:
        payload=None
    _cleanup(state)

    if code != 0:
        message=(payload or {}).get("error") if isinstance(payload,dict) else None
        message=message or stderr.strip() or stdout.strip() or "Analyzer process failed."
        return {
            "done":True,
            "ok":False,
            "symbol":state.get("symbol"),
            "message":str(message)[-3000:],
            "runtime_seconds":round(elapsed,1),
        }

    if not isinstance(payload,dict) or not isinstance(payload.get("result"),dict):
        return {
            "done":True,
            "ok":False,
            "symbol":state.get("symbol"),
            "message":"Analyzer completed without a valid result.",
            "runtime_seconds":round(elapsed,1),
        }

    return {
        "done":True,
        "ok":True,
        "symbol":state.get("symbol"),
        "result":payload["result"],
        "runtime_seconds":round(elapsed,1),
    }
