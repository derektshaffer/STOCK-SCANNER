import streamlit as st


def inject_glass_theme():
    """Shared visual theme for the scanner/analyzer workspace.

    This intentionally changes presentation only. It does not alter scanner,
    analyzer, market-data, ML, navigation, or session-state logic.
    """
    st.markdown(
        """
        <style>
        :root {
            --glass-bg-0: #050d18;
            --glass-bg-1: #071524;
            --glass-bg-2: #0a1b2d;
            --glass-panel: rgba(10, 24, 40, .78);
            --glass-panel-strong: rgba(12, 29, 48, .92);
            --glass-inner: rgba(17, 38, 61, .58);
            --glass-line: rgba(105, 151, 197, .22);
            --glass-line-strong: rgba(105, 174, 226, .38);
            --glass-text: #f4f8ff;
            --glass-muted: #91a9c5;
            --glass-green: #37ef79;
            --glass-green-soft: rgba(55, 239, 121, .13);
            --glass-green-line: rgba(55, 239, 121, .46);
            --glass-blue: #63cfff;
            --glass-purple: #b85cff;
            --glass-red: #ff5368;
            --glass-amber: #ffc95a;
            --glass-radius: 14px;
            --glass-shadow: 0 14px 40px rgba(0, 0, 0, .22);
        }

        html, body,
        [data-testid="stAppViewContainer"],
        .stApp {
            background:
                radial-gradient(circle at 78% -10%, rgba(38, 120, 180, .13), transparent 34%),
                radial-gradient(circle at 18% 12%, rgba(40, 235, 128, .055), transparent 30%),
                linear-gradient(180deg, var(--glass-bg-0) 0%, var(--glass-bg-1) 46%, #06101d 100%) !important;
            color: var(--glass-text) !important;
        }

        .block-container {
            max-width: 1640px !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-bottom: 2rem !important;
        }

        /* ---------- Workspace top bar ---------- */
        .workspace-brand {
            height: 52px;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 0 14px;
            border: 1px solid var(--glass-line);
            border-radius: 14px;
            background: linear-gradient(145deg, rgba(10,25,42,.88), rgba(7,18,31,.74));
            box-shadow: inset 0 1px 0 rgba(255,255,255,.035);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            box-sizing: border-box;
        }
        .workspace-brand-mark {
            width: 30px;
            height: 30px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 9px;
            color: var(--glass-green);
            background: linear-gradient(145deg, rgba(55,239,121,.17), rgba(33,137,83,.08));
            border: 1px solid rgba(55,239,121,.23);
            box-shadow: 0 0 18px rgba(55,239,121,.09);
            font-size: 17px;
            font-weight: 950;
        }
        .workspace-brand-text {
            color: #eef6ff;
            font-size: 13px;
            line-height: 1;
            font-weight: 900;
            letter-spacing: .075em;
            white-space: nowrap;
        }

        .workspace-status {
            min-height: 52px;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 7px;
            padding: 0 4px;
            box-sizing: border-box;
        }
        .workspace-status-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-height: 32px;
            padding: 0 10px;
            border-radius: 999px;
            border: 1px solid var(--glass-line);
            background: rgba(8, 21, 35, .72);
            color: #dbe9f7;
            font-size: 10px;
            font-weight: 850;
            letter-spacing: .04em;
            white-space: nowrap;
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
        }
        .workspace-status-pill.live {
            color: #74f7a5;
            border-color: rgba(55,239,121,.24);
            background: rgba(18, 74, 44, .20);
        }
        .workspace-status-pill.session {
            color: #e9d9ff;
            border-color: rgba(184,92,255,.24);
            background: rgba(79,38,112,.18);
        }
        .workspace-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: currentColor;
            box-shadow: 0 0 10px currentColor;
        }

        /* Keep the brand, segmented selector and status pills on one baseline.
           Streamlit's radio wrapper is slightly taller than its visible group
           unless we explicitly collapse that extra layout space. */
        div[data-testid="stHorizontalBlock"]:has(.workspace-brand):has(.st-key-app_view) {
            align-items: center !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.workspace-brand):has(.st-key-app_view)
        > [data-testid="stColumn"] {
            align-self: center !important;
        }
        .workspace-brand,
        .workspace-status,
        .st-key-app_view,
        .st-key-app_view [data-testid="stRadio"],
        .st-key-app_view [data-testid="stRadio"] > div {
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }
        .st-key-app_view {
            min-height: 52px !important;
            height: 52px !important;
            display: flex !important;
            align-items: center !important;
            padding: 0 !important;
        }
        .st-key-app_view [data-testid="stRadio"] {
            min-height: 52px !important;
            height: 52px !important;
            display: flex !important;
            align-items: center !important;
            padding: 0 !important;
        }

        .combined-nav-wrap { display: none !important; }

        .st-key-app_view,
        .st-key-app_view > div,
        .st-key-app_view [data-testid="stRadio"],
        .st-key-app_view [data-testid="stRadio"] > div {
            width: 100% !important;
            max-width: none !important;
            min-width: 0 !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
            width: 100% !important;
            min-height: 52px !important;
            display: grid !important;
            grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            gap: 4px !important;
            padding: 4px !important;
            margin: 0 !important;
            box-sizing: border-box !important;
            border-radius: 14px !important;
            border: 1px solid var(--glass-line-strong) !important;
            background:
                linear-gradient(145deg, rgba(12,28,46,.88), rgba(7,18,31,.76)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 12px 34px rgba(0,0,0,.18) !important;
            backdrop-filter: blur(18px) !important;
            -webkit-backdrop-filter: blur(18px) !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
            position: relative !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 9px !important;
            min-height: 42px !important;
            padding: 0 14px !important;
            margin: 0 !important;
            box-sizing: border-box !important;
            border-radius: 10px !important;
            border: 1px solid transparent !important;
            background: transparent !important;
            cursor: pointer !important;
            overflow: hidden !important;
            transition:
                background .16s ease,
                border-color .16s ease,
                box-shadow .16s ease !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            pointer-events: none !important;
        }

        /* Streamlit/BaseWeb can render the native radio mark one level deeper
           than the label's first child. Remove that visual mark completely;
           the highlighted segment itself is the selected-state indicator. */
        .st-key-app_view input[type="radio"] {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            pointer-events: none !important;
        }
        .st-key-app_view label div:has(> input[type="radio"]),
        .st-key-app_view label span:has(> input[type="radio"]),
        .st-key-app_view [data-baseweb="radio"] > div:has(input[type="radio"]) {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            overflow: hidden !important;
            pointer-events: none !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            width: 24px !important;
            height: 24px !important;
            flex: 0 0 24px !important;
            border-radius: 999px !important;
            color: #b7c8da !important;
            border: 1px solid rgba(126,167,205,.24) !important;
            background: rgba(11,27,44,.62) !important;
            font-size: 14px !important;
            line-height: 1 !important;
            font-weight: 900 !important;
            box-sizing: border-box !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(1)::before {
            content: "↗";
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(2)::before {
            content: "⌕";
            font-size: 16px !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
            margin: 0 !important;
            color: #afbed0 !important;
            font-size: 14px !important;
            line-height: 1 !important;
            font-weight: 850 !important;
            letter-spacing: -.01em !important;
            white-space: nowrap !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::after {
            display: none !important;
            content: none !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
            border-color: rgba(99,207,255,.24) !important;
            background: rgba(17,37,59,.48) !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
            border-color: var(--glass-green-line) !important;
            background:
                linear-gradient(145deg, rgba(29,119,67,.34), rgba(12,58,35,.28)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.055),
                0 0 22px rgba(55,239,121,.12),
                0 4px 16px rgba(0,0,0,.16) !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked)::before {
            color: var(--glass-green) !important;
            border-color: rgba(55,239,121,.46) !important;
            background: rgba(16,84,47,.32) !important;
            box-shadow: 0 0 15px rgba(55,239,121,.12) !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) p {
            color: #f7fbff !important;
        }

        /* ---------- Glass surface system ---------- */
        .header,
        .hero,
        .combined-quick,
        .legend-box,
        .stat,
        .card,
        .tradeplan,
        .saved-stock-shell,
        .market-box,
        .auto-box,
        .callout,
        [data-testid="stExpander"] details {
            background:
                linear-gradient(145deg, rgba(12,29,48,.84), rgba(8,20,34,.70)) !important;
            border-color: var(--glass-line) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 10px 30px rgba(0,0,0,.14) !important;
            backdrop-filter: blur(15px) !important;
            -webkit-backdrop-filter: blur(15px) !important;
        }

        .header,
        .hero,
        .legend-box,
        .tradeplan,
        .saved-stock-shell {
            border-radius: var(--glass-radius) !important;
        }

        .title,
        .hero .title,
        .section,
        .search-label,
        .saved-stock-title {
            color: #f3f8ff !important;
            text-shadow: 0 1px 18px rgba(100,190,255,.04);
        }

        .sub,
        .stat-n,
        .tradewhy,
        .smallnote,
        .saved-stock-sub,
        .n,
        .mk,
        .nk,
        .news-time {
            color: var(--glass-muted) !important;
        }

        .pill,
        .badge {
            border-width: 1px !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
        }
        .green {
            color: #92fbb9 !important;
            background: rgba(55,239,121,.10) !important;
            border-color: rgba(55,239,121,.28) !important;
        }
        .blue {
            color: #9addff !important;
            background: rgba(99,207,255,.09) !important;
            border-color: rgba(99,207,255,.27) !important;
        }
        .amber {
            color: #ffe1a0 !important;
            background: rgba(255,201,90,.09) !important;
            border-color: rgba(255,201,90,.28) !important;
        }
        .red {
            color: #ffb4bf !important;
            background: rgba(255,83,104,.09) !important;
            border-color: rgba(255,83,104,.28) !important;
        }

        .stat,
        .card {
            border-radius: 12px !important;
        }
        .stat:hover,
        .card:hover {
            border-color: rgba(99,207,255,.32) !important;
        }

        .card-a {
            border-top-color: var(--glass-green) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.035), 0 0 24px rgba(55,239,121,.045) !important;
        }
        .card-b { border-top-color: var(--glass-blue) !important; }
        .card-c { border-top-color: var(--glass-amber) !important; }
        .card-r { border-top-color: var(--glass-red) !important; }

        .metric,
        .legend-item,
        .sid-metric {
            background: linear-gradient(145deg, rgba(20,43,68,.58), rgba(12,29,48,.52)) !important;
            border-color: rgba(105,151,197,.18) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.02);
        }

        /* ---------- Compact scanner table ---------- */
        .combined-quick {
            border: 1px solid var(--glass-line) !important;
            background: linear-gradient(145deg, rgba(10,27,44,.72), rgba(7,19,32,.60)) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025), 0 10px 28px rgba(0,0,0,.12) !important;
        }

        .combined-ticker-row {
            border: 1px solid rgba(105,151,197,.14) !important;
            border-radius: 10px !important;
            background: linear-gradient(90deg, rgba(12,30,49,.54), rgba(8,21,35,.40)) !important;
            padding-left: 6px !important;
            padding-right: 6px !important;
            margin: 3px 0 !important;
            transition: border-color .15s ease, background .15s ease !important;
        }
        .combined-ticker-row:hover {
            border-color: rgba(55,239,121,.34) !important;
            background: linear-gradient(90deg, rgba(18,56,41,.34), rgba(9,27,41,.52)) !important;
        }
        .combined-stat {
            background: rgba(14,34,55,.46) !important;
            border-color: rgba(105,151,197,.12) !important;
        }
        .combined-rank {
            color: #89a1be !important;
        }
        .combined-ticker-symbol {
            color: #f6f9ff !important;
            letter-spacing: .01em !important;
        }
        .combined-stat-label {
            color: #849bb8 !important;
        }

        .scanner-expandable-ticker {
            border-color: rgba(105,151,197,.26) !important;
            background: rgba(15,36,58,.62) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025) !important;
        }
        .scanner-expandable-ticker:hover {
            border-color: rgba(55,239,121,.38) !important;
            background: rgba(20,58,43,.38) !important;
        }
        .scanner-expandable-ticker::after {
            color: #75d7ff !important;
            border-left-color: rgba(105,151,197,.26) !important;
        }

        .scanner-inline-detail {
            background:
                linear-gradient(145deg, rgba(12,29,48,.96), rgba(7,20,34,.94)) !important;
            border-color: rgba(105,151,197,.24) !important;
            box-shadow: var(--glass-shadow) !important;
        }

        /* ---------- Controls ---------- */
        div[data-testid="stButton"] button {
            border-radius: 10px !important;
            transition:
                border-color .14s ease,
                background .14s ease,
                box-shadow .14s ease !important;
        }

        div[data-testid="stButton"] button[kind="primary"] {
            color: #f6fff9 !important;
            border: 1px solid rgba(55,239,121,.52) !important;
            background:
                linear-gradient(145deg, rgba(28,138,74,.92), rgba(13,82,45,.92)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.08),
                0 0 20px rgba(55,239,121,.10) !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover:not(:disabled) {
            border-color: rgba(91,255,146,.80) !important;
            background:
                linear-gradient(145deg, rgba(35,162,88,.96), rgba(16,96,53,.96)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.10),
                0 0 26px rgba(55,239,121,.16) !important;
        }

        div[data-testid="stButton"] button[kind="secondary"] {
            color: #eaf3fc !important;
            border: 1px solid rgba(105,151,197,.28) !important;
            background: rgba(12,29,48,.62) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled) {
            border-color: rgba(99,207,255,.38) !important;
            background: rgba(16,42,67,.74) !important;
        }

        [class*="st-key-combined_analyze_"] button {
            color: #fff6f7 !important;
            border: 1px solid rgba(255,83,104,.70) !important;
            background:
                linear-gradient(145deg, rgba(208,47,69,.96), rgba(139,24,43,.96)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.08),
                0 0 17px rgba(255,83,104,.10) !important;
        }
        [class*="st-key-combined_analyze_"] button:hover:not(:disabled) {
            border-color: rgba(255,119,136,.88) !important;
            background:
                linear-gradient(145deg, rgba(228,57,80,.98), rgba(160,29,49,.98)) !important;
            box-shadow: 0 0 22px rgba(255,83,104,.16) !important;
        }

        /* Analyze loading feedback: yellow is reserved for "working now".
           The browser adds stock-analyze-loading immediately on click, and the
           Analyzer's native disabled/loading rerun gets the same treatment. */
        button.stock-analyze-loading,
        .st-key-analyzer_manual_analyze button:disabled {
            color: #211800 !important;
            border-color: rgba(255,214,91,.92) !important;
            background:
                linear-gradient(145deg, #ffd75b 0%, #e7a928 100%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.34),
                0 0 24px rgba(255,201,90,.22) !important;
            opacity: 1 !important;
            cursor: wait !important;
        }
        button.stock-analyze-loading *,
        .st-key-analyzer_manual_analyze button:disabled * {
            color: #211800 !important;
            fill: #211800 !important;
            opacity: 1 !important;
        }

        div[data-testid="stButton"] button:disabled {
            opacity: .72 !important;
            background: rgba(13,27,43,.72) !important;
            border-color: rgba(105,151,197,.16) !important;
            color: #8196ae !important;
            box-shadow: none !important;
        }

        div[data-baseweb="select"] > div,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input {
            color: #eef6ff !important;
            border-color: rgba(105,151,197,.25) !important;
            background: rgba(10,25,42,.78) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.02) !important;
        }

        /* ---------- Streamlit-native surfaces ---------- */
        div[data-testid="stDataFrame"] {
            border: 1px solid var(--glass-line) !important;
            border-radius: 12px !important;
            overflow: hidden !important;
            box-shadow: 0 10px 28px rgba(0,0,0,.12) !important;
        }

        [data-testid="stExpander"] details {
            border: 1px solid var(--glass-line) !important;
            border-radius: 11px !important;
        }

        div[data-testid="stAlert"] {
            border-radius: 11px !important;
            border: 1px solid rgba(105,151,197,.20) !important;
            background: rgba(12,29,48,.70) !important;
        }

        hr {
            border-color: rgba(105,151,197,.14) !important;
        }

        /* ---------- Status controls ---------- */
        .auto-box {
            border-left-width: 3px !important;
            border-radius: 10px !important;
        }
        .auto-on {
            border-left-color: var(--glass-green) !important;
        }
        .auto-wait {
            border-left-color: var(--glass-purple) !important;
        }
        .market-box.open {
            border-left-color: var(--glass-green) !important;
        }
        .market-box.closed {
            border-left-color: var(--glass-purple) !important;
        }

        /* ---------- Analyzer ---------- */
        .hero {
            border-color: rgba(105,151,197,.22) !important;
            background:
                radial-gradient(circle at 92% 0%, rgba(55,239,121,.075), transparent 32%),
                linear-gradient(145deg, rgba(12,29,48,.90), rgba(7,19,33,.80)) !important;
        }
        .tradeplan {
            border-color: rgba(55,239,121,.22) !important;
            background:
                linear-gradient(145deg, rgba(15,43,42,.55), rgba(9,25,40,.82)) !important;
        }
        .callout {
            border-left-color: var(--glass-blue) !important;
            background: rgba(12,31,51,.70) !important;
        }

        .good { color: #6df59d !important; }
        .bad { color: #ff8392 !important; }
        .warn { color: #ffd476 !important; }
        .pos { color: #6df59d !important; }
        .neg { color: #ff8392 !important; }

        #stock-tech-tooltip {
            border-color: rgba(105,151,197,.34) !important;
            background: rgba(7,20,34,.96) !important;
            box-shadow: 0 16px 46px rgba(0,0,0,.38) !important;
            backdrop-filter: blur(16px) !important;
        }

        @media (max-width: 1100px) {
            .workspace-brand-text { display: none; }
            .workspace-brand {
                justify-content: center;
                padding-left: 8px;
                padding-right: 8px;
            }
            .workspace-status-pill.time { display: none; }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
                font-size: 13px !important;
            }
        }

        @media (max-width: 760px) {
            .workspace-status { display: none; }
            .workspace-brand {
                height: 46px;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
                min-height: 46px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
                min-height: 36px !important;
                padding-left: 8px !important;
                padding-right: 8px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
                display: none !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
                font-size: 12px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
