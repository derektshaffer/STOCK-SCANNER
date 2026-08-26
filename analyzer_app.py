from pathlib import Path
import runpy

from scanner_expand import install_scanner_expander
from scanner_single_open import install_single_open_scanner_details

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

# Then attach the client-side ticker disclosure behavior. Clicking a ticker
# expands/collapses its detail card instantly without a full Streamlit rerun.
install_scanner_expander()

# Accordion behavior: when a different ticker is opened, automatically close
# the previously expanded ticker so only one detail card is visible at a time.
install_single_open_scanner_details()
