"""Small disk cache for slow, non-tick Analyzer context.

Short-lived Analyzer subprocesses cannot benefit from Python lru_cache across
launches. This cache lets SEC/fundamental and other slow context survive those
process boundaries inside the Streamlit container without persisting secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


CACHE_DIR = Path(
    os.environ.get(
        "ANALYZER_CONTEXT_CACHE_DIR",
        str(Path(tempfile.gettempdir()) / "stock-analyzer-context-cache"),
    )
)


def _path(key):
    digest=hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def get_cached_context(key, ttl_seconds):
    path=_path(key)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        saved=float(payload.get("saved_at") or 0.0)
        if not saved or time.time()-saved > float(ttl_seconds):
            return None
        value=payload.get("value")
        return value if isinstance(value,dict) else None
    except Exception:
        return None


def set_cached_context(key, value):
    if not isinstance(value,dict):
        return False
    path=_path(key)
    try:
        CACHE_DIR.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"saved_at":time.time(),"value":value},
                separators=(",",":"),
                default=str,
            ),
            encoding="utf-8",
        )
        os.replace(tmp,path)
        return True
    except Exception:
        return False
