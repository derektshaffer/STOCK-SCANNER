from pathlib import Path
import runpy

# Streamlit Cloud is configured to launch app.py.
# Keep the actual Single Stock Analyzer UI in analyzer_app.py and execute it
# on every Streamlit rerun.
target = Path(__file__).with_name("analyzer_app.py")

if not target.exists():
    raise FileNotFoundError(
        "analyzer_app.py was not found in the repository root. "
        "Upload analyzer_app.py next to app.py."
    )

runpy.run_path(str(target), run_name="__main__")
