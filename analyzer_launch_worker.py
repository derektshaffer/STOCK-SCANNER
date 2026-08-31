"""Background worker used by the combined Scanner -> Analyzer launch flow."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: analyzer_launch_worker.py TICKER OUTPUT_JSON")

    symbol=str(sys.argv[1] or "").upper().strip()
    output=Path(sys.argv[2])
    if not symbol:
        raise SystemExit("Missing ticker")

    try:
        import stock_analyzer as sa
        from historical_integration import install_historical_analysis
        from ml_integration import install_ml_analysis
        from analyzer_v2_integration import install_v2_analysis

        install_historical_analysis(sa)
        install_ml_analysis(sa)
        install_v2_analysis(sa)
        result=sa.analyze(symbol)
        payload={"ok":True,"symbol":symbol,"result":result}
        code=0
    except Exception as exc:
        payload={"ok":False,"symbol":symbol,"error":str(exc)}
        code=1

    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(output.suffix+".tmp")
    tmp.write_text(json.dumps(payload,default=str),encoding="utf-8")
    os.replace(tmp,output)
    raise SystemExit(code)


if __name__=="__main__":
    main()
