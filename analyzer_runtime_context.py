"""Per-execution Analyzer context that is safe across Streamlit sessions.

Environment variables are process-global, so they are unsafe as the primary
source of a browser-session identifier when multiple Streamlit sessions share
one Python process. ContextVar keeps the active namespace local to the current
execution context/thread. Subprocess workers still use the environment fallback.
"""

from __future__ import annotations

import os
from contextvars import ContextVar


_ANALYZER_NAMESPACE = ContextVar("analyzer_thesis_namespace", default=None)


def set_analyzer_namespace(value):
    value = str(value or "").strip() or None
    return _ANALYZER_NAMESPACE.set(value)


def reset_analyzer_namespace(token):
    try:
        _ANALYZER_NAMESPACE.reset(token)
    except Exception:
        pass


def get_analyzer_namespace():
    value = _ANALYZER_NAMESPACE.get()
    if value:
        return str(value)
    return (
        os.environ.get("ANALYZER_THESIS_NAMESPACE", "").strip()
        or "standalone"
    )
