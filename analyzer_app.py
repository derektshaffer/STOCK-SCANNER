from pathlib import Path
import runpy

from scanner_expand import install_scanner_expander

# Compatibility entrypoint for Streamlit deployments that were originally
# configured to launch analyzer_app.py. Re-execute the combined app on every
# Streamlit rerun instead of importing it as a cached Python module.
target = Path(__file__).with_name("app.py")

if not target.exists():
    raise FileNotFoundError(
        "app.py was not found in the repository root. "
        "The combined Momentum Scanner + Stock Analyzer requires app.py."
    )

# Render the combined app first so app.py's st.set_page_config remains the
# first Streamlit command on each rerun.
runpy.run_path(str(target), run_name="__main__")

# Then attach the client-side ticker disclosure behavior. The zero-height
# component uses delegated click/keyboard listeners, so clicking a ticker
# expands/collapses its detail card instantly without a full Streamlit rerun.
# The separate Analyze buttons are untouched.
install_scanner_expander()
