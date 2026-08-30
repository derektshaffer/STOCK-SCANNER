"""Legacy compatibility entrypoint.

Older Streamlit deployments may still be configured to launch
analyzer_app_fixed.py. Keep that deployment path working, but route it to the
single current combined Momentum Scanner + Stock Analyzer implementation so
there is no second stale Analyzer codebase to drift or require different
credentials.
"""

from pathlib import Path
import runpy

target = Path(__file__).with_name("analyzer_app.py")
if not target.exists():
    raise FileNotFoundError(
        "analyzer_app.py was not found. The current combined Stock Workspace "
        "requires analyzer_app.py and app.py in the repository root."
    )

runpy.run_path(str(target), run_name="__main__")
