"""Background research -> hypothesis generator for the trading analyzer.

Web research can create candidate hypotheses, but it cannot change model
weights, entry rules, or production features. Candidates must pass separate
historical / walk-forward validation before promotion.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


API_URL = "https://api.openai.com/v1/responses"
MODEL = os.environ.get("TRADING_RESEARCH_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
OUT_DIR = Path("research")
RESEARCH_TRIGGER_VERSION = "1.3"
LATEST_PATH = OUT_DIR / "latest_research.json"


PROMPT = r"""
You are a market-research agent supporting a short-term U.S. equity momentum
analyzer. Your job is NOT to produce trade recommendations and NOT to edit
production rules. Your job is to find credible, measurable ideas that can be
tested on historical intraday data.

Research current and foundational evidence relevant to:
- momentum continuation and exhaustion;
- impulse -> pullback -> bounce behavior;
- second/third/repeat bounces and bounce-size decay;
- lower highs, higher lows, failed reclaims, failed breakouts;
- volume climax, volume contraction/expansion, VWAP behavior;
- float, liquidity, spreads, turnover and market microstructure;
- catalyst-driven small-cap / low-priced stocks;
- time-of-day effects and closing-hour behavior;
- variables that help distinguish a quick bounce from a true new-high
  continuation;
- second/third-bounce failure severity: what tends to happen after a weak late
  bounce, including 5%/10% falloff risk and time-to-failure;
- multi-session step -> plateau -> reacceleration structures where price
  accepts a higher level, consolidates, then expands again;
- sequence/motif discovery methods that can find useful recurring price-volume
  patterns even when traders have not already given the pattern a name;
- clustering, change-point, shapelet, motif, state/regime, or other
  leakage-safe sequence methods that could discover unknown intraday or
  multi-session structures worth testing;
- machine-learning features or validation practices that improve short-horizon
  prediction without leakage or overfitting.

Source priority:
1. peer-reviewed / academic or working-paper empirical research;
2. exchanges, regulators, official market-structure material;
3. high-quality quantitative / institutional research;
4. reputable practitioner material only when the claim is clearly testable.

Be skeptical of trading folklore. A widely repeated idea is not evidence.
Prefer sources with data, definitions, sample periods, and measurable outcomes.
At least one hypothesis should be a data-driven pattern-discovery experiment
that does NOT start from a named trading setup. It should search historical
price/volume sequences for recurring motifs, cluster/regime behavior, or
change-points, then test whether the discovered motif has stable forward
outcomes on later unseen dates. Pattern discovery and outcome validation must
be chronologically separated so the system cannot discover a pattern using the
same future data it later claims to predict.

Return ONLY valid JSON with this shape:
{
  "research_summary": "short overview",
  "hypotheses": [
    {
      "title": "short name",
      "claim": "specific falsifiable claim",
      "why_it_might_matter": "brief rationale",
      "features_to_test": ["feature1", "feature2"],
      "target_outcome": "precise future outcome to predict",
      "conditioning_variables": ["price/float/time/catalyst/etc"],
      "expected_direction": "what relationship the evidence suggests",
      "recommended_test": "walk-forward / event-study design",
      "minimum_evidence_to_promote": "explicit threshold concept",
      "failure_or_rejection_criteria": "what would make us discard it",
      "source_quality": "HIGH | MEDIUM | LOW",
      "confidence": "HIGH | MEDIUM | LOW"
    }
  ],
  "important_caveats": ["..."],
  "research_gaps": ["questions worth investigating next"]
}

Generate exactly 6 hypotheses. Every hypothesis must be implementable from
market, news, fundamental, or microstructure data that could realistically be
connected to the analyzer. Keep every field concise. Do not put Markdown links
or citations inside the JSON fields; source URLs are captured separately from
the web-search tool. Do not recommend promoting anything directly to production.
"""


RESEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "research_summary",
        "hypotheses",
        "important_caveats",
        "research_gaps",
    ],
    "properties": {
        "research_summary": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "minItems": 6,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "claim",
                    "why_it_might_matter",
                    "features_to_test",
                    "target_outcome",
                    "conditioning_variables",
                    "expected_direction",
                    "recommended_test",
                    "minimum_evidence_to_promote",
                    "failure_or_rejection_criteria",
                    "source_quality",
                    "confidence",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "claim": {"type": "string"},
                    "why_it_might_matter": {"type": "string"},
                    "features_to_test": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 8,
                        "items": {"type": "string"},
                    },
                    "target_outcome": {"type": "string"},
                    "conditioning_variables": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "string"},
                    },
                    "expected_direction": {"type": "string"},
                    "recommended_test": {"type": "string"},
                    "minimum_evidence_to_promote": {"type": "string"},
                    "failure_or_rejection_criteria": {"type": "string"},
                    "source_quality": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                },
            },
        },
        "important_caveats": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "research_gaps": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
    },
}


def _extract_output_text(payload):
    pieces = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if text:
                    pieces.append(str(text))
    return "\n".join(pieces).strip()


def _walk_sources(value, out):
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            out[url] = {
                "url": url,
                "title": value.get("title") or value.get("name"),
                "type": value.get("type"),
            }
        for key, child in value.items():
            if key in {"input", "instructions"}:
                continue
            _walk_sources(child, out)
    elif isinstance(value, list):
        for child in value:
            _walk_sources(child, out)


def _parse_json_text(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("~~~") and raw.endswith("~~~"):
        raw = raw.strip("~").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        first = raw.find("{")
        last = raw.rfind("}")
        if first >= 0 and last > first:
            try:
                parsed = json.loads(raw[first : last + 1])
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
    return None


def main():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("RESEARCH_STATUS=skipped_missing_OPENAI_API_KEY")
        return 0

    now = datetime.now(timezone.utc)
    request_payload = {
        "model": MODEL,
        "input": PROMPT,
        "reasoning": {"effort": "low"},
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "include": ["web_search_call.action.sources"],
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "trading_research_hypotheses",
                "strict": True,
                "schema": RESEARCH_SCHEMA,
            },
        },
        "max_output_tokens": 9000,
        "store": False,
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI research request failed HTTP {exc.code}: {body[:800]}"
        ) from exc

    output_text = _extract_output_text(response_payload)
    parsed = _parse_json_text(output_text)
    response_status = str(response_payload.get("status") or "")
    incomplete_reason = (
        (response_payload.get("incomplete_details") or {}).get("reason")
        if isinstance(response_payload.get("incomplete_details"), dict)
        else None
    )

    sources = {}
    _walk_sources(response_payload.get("output") or [], sources)

    hypotheses = []
    if isinstance(parsed, dict):
        for index, item in enumerate(parsed.get("hypotheses") or [], start=1):
            if not isinstance(item, dict):
                continue
            candidate = dict(item)
            candidate["candidate_id"] = f"{now.date().isoformat()}-H{index:02d}"
            candidate["status"] = "RESEARCH_CANDIDATE"
            candidate["production_weight"] = 0
            candidate["promotion_gate"] = (
                "Must be implemented as a leakage-safe historical experiment "
                "and pass out-of-sample / walk-forward validation before use."
            )
            hypotheses.append(candidate)

    artifact = {
        "generated_at": now.isoformat(),
        "model": response_payload.get("model") or MODEL,
        "response_id": response_payload.get("id"),
        "status": (
            "ok"
            if parsed is not None
            else "incomplete_output"
            if response_status == "incomplete"
            else "unparsed_output"
        ),
        "response_status": response_status,
        "incomplete_reason": incomplete_reason,
        "guardrail": (
            "Research is hypothesis generation only. No item in this file is "
            "allowed to change live predictions until separately validated."
        ),
        "research_summary": parsed.get("research_summary") if parsed else None,
        "hypotheses": hypotheses,
        "important_caveats": parsed.get("important_caveats") if parsed else [],
        "research_gaps": parsed.get("research_gaps") if parsed else [],
        "sources": list(sources.values()),
        "raw_output": output_text if parsed is None else None,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    archive_dir = OUT_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    rendered = json.dumps(artifact, indent=2, ensure_ascii=False) + "\n"
    LATEST_PATH.write_text(rendered, encoding="utf-8")
    archive_path = archive_dir / f"{now.date().isoformat()}.json"
    archive_path.write_text(rendered, encoding="utf-8")

    print("RESEARCH_STATUS=" + artifact["status"])
    print("RESEARCH_HYPOTHESES=" + str(len(hypotheses)))
    print("RESEARCH_SOURCES=" + str(len(sources)))
    print("RESEARCH_MODEL=" + str(artifact["model"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
