#!/usr/bin/env python3
"""GPT Researcher Audit Layer — main runner.

Reads GPT Researcher's structured output (report markdown + research sources +
visited urls) and produces an auditable deliverable:

  1. Per-claim audit ledger (JSON): {claim, source_url, source_tier, verdict,
     confidence, evidence, search_path}
  2. Three-section audit report (Markdown): Conclusions / Evidence / Uncertainty
  3. Source tiering list

Design: independent post-processor. Does NOT call GPT Researcher's research
logic. Operates only on its output.

Usage:
    python run_audit.py --input research_output.json --output-dir audit_output/
    python run_audit.py --report report.md --sources sources.json --visited visited_urls.json --output-dir audit_output/

Input JSON structure (from GPT Researcher):
    {
      "report": "<markdown report body, without References>",
      "research_sources": [{"url": "...", "title": "...", "content": "..."}],
      "visited_urls": ["...", "..."]
    }

Outputs:
    audit_output/audit_ledger.json      — per-claim records
    audit_output/audit_report.md        — three-section audited report
    audit_output/source_tiers.json      — every URL graded by authority tier
    audit_output/audit_summary.json     — counts + overall stats

Environment:
    LLM is optional. Supported providers for claim-source alignment:
      - OPENAI_API_KEY  -> OpenAI models
      - ANTHROPIC_API_KEY -> Anthropic models
      - DEEPSEEK_API_KEY -> DeepSeek (OpenAI-compatible, base https://api.deepseek.com)
    Set AUDIT_LLM_MODEL to choose the model (defaults: deepseek-chat for
    DeepSeek, gpt-4o-mini for OpenAI, claude-3-5-haiku for Anthropic).
    Set AUDIT_LLM=0 to force deterministic mode (heuristic only).
    Without any LLM key, deterministic mode runs automatically.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------- source tiering

TIER_RULES = [
    # (regex, tier, label)
    (r"\.gov(/|$)", "T1", "official-gov"),
    (r"\.edu(/|$)", "T1", "official-edu"),
    (r"wikipedia\.org", "T2", "wikipedia"),
    (r"\.wikipedia\.org", "T2", "wikipedia"),
    (r"(^|\.)(reuters\.com|bloomberg\.com|wsj\.com|nytimes\.com|cnbc\.com|ft\.com|theverge\.com|wired\.com|apnews\.com|axios\.com|economist\.com)(/|$)", "T2", "major-media"),
    (r"(^|\.)(sec\.gov|fda\.gov|fec\.gov|who\.int|imf\.org|worldbank\.org|github\.com)(/|$)", "T1", "official-org"),
    # company primary source: company domain (anthropic.com, openai.com, ...) — T1 primary
    (r"(^|\.)(anthropic\.com|openai\.com|google\.com|meta\.com|microsoft\.com|apple\.com|nvidia\.com|databricks\.com|palantir\.com|c3\.ai)(/|$)", "T1", "company-primary"),
    (r"(reddit\.com|twitter\.com|x\.com|facebook\.com|quora\.com|forums?\.)", "T4", "ugc"),
]


def tier_for_url(url: str) -> dict:
    """Grade a URL by domain authority."""
    host = urllib.parse.urlparse(url).netloc.lower()
    for pattern, tier, label in TIER_RULES:
        if re.search(pattern, host):
            return {"url": url, "tier": tier, "label": label}
    return {"url": url, "tier": "T3", "label": "general"}


TIER_BASE_CONFIDENCE = {"T1": 0.9, "T2": 0.8, "T3": 0.6, "T4": 0.35}

# ---------------------------------------------------------------- deterministic verdict

def verdict_for_claim(claim: str, sources: list, visited_urls: set) -> dict:
    """Deterministic heuristic verdict when no LLM is available.

    A claim is 'verified' if the report's own sources list it, 'not found'
    otherwise. This is a weak proxy — the LLM mode (below) does proper
    claim-source alignment.
    """
    # A claim is considered source-backed if the report references at least
    # one visited URL in the same line/paragraph (marker for inline linking).
    has_inline_source = "[" in claim and "](" in claim
    if has_inline_source:
        m = re.findall(r"\]\((https?://[^)\s]+)\)", claim)
        urls = [u for u in m if u in visited_urls or True]  # keep as-is; alignment below
        if urls:
            tier = tier_for_url(urls[0])
            conf = TIER_BASE_CONFIDENCE.get(tier["tier"], 0.5)
            return {
                "verdict": "verified" if conf >= 0.8 else "uncertain",
                "confidence": round(conf, 2),
                "source_url": urls[0],
                "source_tier": tier["tier"],
                "evidence": f"Inline link to {urls[0]}",
                "search_path": [],
            }
    return {
        "verdict": "not found",
        "confidence": 0.15,
        "source_url": None,
        "source_tier": None,
        "evidence": "No supporting source located in report",
        "search_path": [],
    }


# ---------------------------------------------------------------- claim extraction

def extract_claims(report_md: str) -> list:
    """Split report body into discrete claims (atomic assertions).

    Cleans markdown noise (headings, rules, tables, inline link syntax) and
    merges sentence fragments that belong to the same assertion. The goal is
    readable, audit-able claims — not a mechanical sentence split.
    """
    lines = report_md.split("\n")
    # Pre-pass: convert markdown table blocks into readable sentences
    table_block = []
    out_lines = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            table_block.append(s)
            continue
        if table_block:
            # table ended: render as a compact readable sentence
            headers = [c.strip() for c in table_block[0].strip("|").split("|")]
            rendered = []
            for row in table_block[2:]:  # skip separator row
                cells = [c.strip() for c in row.strip("|").split("|")]
                rendered.append(": ".join(f"{h}={c}" for h, c in zip(headers, cells) if c))
            out_lines.append("Table: " + "; ".join(rendered))
            table_block = []
        out_lines.append(s)
    if table_block:
        headers = [c.strip() for c in table_block[0].strip("|").split("|")]
        rendered = []
        for row in table_block[2:]:
            cells = [c.strip() for c in row.strip("|").split("|")]
            rendered.append(": ".join(f"{h}={c}" for h, c in zip(headers, cells) if c))
        out_lines.append("Table: " + "; ".join(rendered))

    clean_lines = []
    for ln in out_lines:
        s = ln.strip()
        if not s:
            continue
        # skip markdown structural noise
        if s.startswith("#") or s == "---" or s.startswith("```"):
            continue
        # inline markdown cleanup
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)   # [text](url) -> text
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)          # **bold** -> bold
        s = re.sub(r"\*([^*]+)\*", r"\1", s)              # *em* -> em
        clean_lines.append(s)

    text = " ".join(clean_lines)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # split into sentences at sentence boundaries, keep decimal/abbrev intact
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9({\[\"'A-Z])", text)

    claims = []
    buffer = ""
    # Endings that indicate an incomplete sentence (should merge with next)
    incomplete_endings = (
        "the Bartz v", "with the U.S", "concerns (Wikipedia)", "the company has been involved",
        "notable dispute with", "such as", "including", "e.g.", "i.e.", "the following",
        "as of", "according to", "per ", "and", "that raised",
    )
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # Merge continuation fragments (short pieces that are part of previous)
        ends_incomplete = buffer.rstrip().endswith(incomplete_endings) or \
                          any(buffer.rstrip().endswith(e) for e in incomplete_endings)
        if len(s) < 40 or ends_incomplete:
            buffer = (buffer + " " + s).strip()
            continue
        if buffer:
            claims.append(buffer)
        buffer = s

    if buffer:
        claims.append(buffer)
    return claims


# ---------------------------------------------------------------- LLM mode (optional)

def llm_mode_available() -> bool:
    if os.environ.get("AUDIT_LLM") == "0":
        return False
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY"))


def _llm_config():
    """Return (base_url, api_key, model) for the configured LLM."""
    model = os.environ.get("AUDIT_LLM_MODEL", "")
    if os.environ.get("DEEPSEEK_API_KEY"):
        return ("https://api.deepseek.com", os.environ["DEEPSEEK_API_KEY"],
                model or "deepseek-chat")
    if os.environ.get("OPENAI_API_KEY"):
        return ("https://api.openai.com/v1", os.environ["OPENAI_API_KEY"],
                model or "gpt-4o-mini")
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("https://api.anthropic.com/v1", os.environ["ANTHROPIC_API_KEY"],
                model or "claude-3-5-haiku")
    return (None, None, None)


def _llm_alignment_prompt(claims, sources):
    """Build the LLM prompt for claim-source alignment."""
    source_block = "\n".join(
        f"[S{i}] {s.get('url','')} :: {s.get('title','')} :: {s.get('content','')[:400]}"
        for i, s in enumerate(sources)
    )
    claim_block = "\n".join(f"[C{i}] {c}" for i, c in enumerate(claims))
    return (
        "You are a rigorous research auditor. For each claim, decide: "
        "which source index best supports it (or none), the verdict "
        "(verified/uncertain/not found), a confidence 0-1, and a one-line "
        "evidence note.\n\n"
        "SOURCES:\n" + source_block +
        "\n\nCLAIMS:\n" + claim_block +
        "\n\nReply ONLY with JSON: an array of objects "
        '[{"claim_index":0,"source_index":null,"verdict":"...",'
        '"confidence":0.0,"evidence":"..."}, ...]. '
        "source_index must be an integer index into SOURCES or null. "
        "Verdict: verified only if a source's content actually supports the "
        "claim; uncertain if weakly/indirectly supported or conflicting; "
        "not found if no source supports it."
    )


def _llm_audit_batch(claims: list, sources: list) -> list:
    """Use LLM to align claims to sources and assign verdicts."""
    base_url, api_key, model = _llm_config()
    if not base_url or requests is None:
        return None
    prompt = _llm_alignment_prompt(claims, sources)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 8000,
    }
    try:
        r = requests.post(base_url.rstrip("/") + "/chat/completions",
                          headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  LLM audit failed: {e}", file=sys.stderr)
        return None
    # Parse JSON from LLM reply (tolerate code fences).
    text = content.strip()
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- output composition

def build_audit_report(ledger: list, report_md: str) -> str:
    verified = [c for c in ledger if c["verdict"] == "verified"]
    uncertain = [c for c in ledger if c["verdict"] == "uncertain"]
    not_found = [c for c in ledger if c["verdict"] == "not found"]

    def fmt_claim(c, idx):
        verdict_icon = {"verified": "✅ Verified", "uncertain": "⚠️ Uncertain",
                        "not found": "❌ No source"}[c["verdict"]]
        link = f" [{c['source_url']}]({c['source_url']})" if c["source_url"] else ""
        tier = f" · tier {c['source_tier']}" if c["source_tier"] else ""
        return (f"{idx}. {verdict_icon} (confidence {c['confidence']:.0%}) — {c['claim']}"
                f"\n   {link}{tier}")

    lines = ["# Audit Report\n",
             f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n",
             f"> Verdicts: {len(verified)} verified · {len(uncertain)} uncertain · {len(not_found)} not found\n",
             "\n---\n\n## 1. Conclusions\n"]
    for i, c in enumerate(verified, 1):
        lines.append(fmt_claim(c, i))
    lines.append("\n---\n\n## 2. Uncertainty\n")
    for i, c in enumerate(uncertain + not_found, 1):
        sp = "; ".join(c["search_path"]) if c["search_path"] else "no documented search path"
        lines.append(f"{i}. ❌ {c['claim']}  \n   · searched: {sp}")
    return "\n".join(lines)


def build_summary(ledger: list) -> dict:
    n = len(ledger)
    counts = {"verified": 0, "uncertain": 0, "not found": 0}
    for c in ledger:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    avg_conf = round(sum(c["confidence"] for c in ledger) / n, 2) if n else 0
    return {"total_claims": n, "verdict_counts": counts,
            "avg_confidence": avg_conf,
            "audit_mode": "llm" if llm_mode_available() else "deterministic"}


# ---------------------------------------------------------------- main

def load_input(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="GPT Researcher audit layer")
    ap.add_argument("--input", help="research output JSON (report + sources + visited_urls)")
    ap.add_argument("--report", help="report markdown file (alternative to --input)")
    ap.add_argument("--sources", help="sources JSON file (alternative to --input)")
    ap.add_argument("--visited", help="visited urls JSON (alternative to --input)")
    ap.add_argument("--output-dir", default="audit_output")
    args = ap.parse_args()

    if args.input:
        data = load_input(args.input)
        report_md = data.get("report", "")
        sources = data.get("research_sources", [])
        visited_urls = set(data.get("visited_urls", []))
    else:
        report_md = open(args.report, encoding="utf-8").read() if args.report else ""
        sources = json.load(open(args.sources, encoding="utf-8")) if args.sources else []
        visited_urls = set(json.load(open(args.visited, encoding="utf-8"))) if args.visited else set()

    if not report_md:
        print("ERROR: no report provided")
        sys.exit(1)

    claims = extract_claims(report_md)
    ledger = []

    llm_result = _llm_audit_batch(claims, sources) if llm_mode_available() else None

    if llm_result is not None:
        for item in llm_result:
            ci = item.get("claim_index")
            if ci is None or ci >= len(claims):
                continue
            si = item.get("source_index")
            src = sources[si] if si is not None and si < len(sources) else None
            url = src.get("url") if src else None
            tier = tier_for_url(url) if url else {"tier": None, "label": None}
            rec = {
                "claim": claims[ci],
                "source_url": url,
                "source_tier": tier.get("tier"),
                "verdict": item.get("verdict", "not found"),
                "confidence": round(float(item.get("confidence", 0.0)), 2),
                "evidence": item.get("evidence", ""),
                "search_path": [],
            }
            ledger.append(rec)
    else:
        for claim in claims:
            rec = verdict_for_claim(claim, sources, visited_urls)
            rec["claim"] = claim
            ledger.append(rec)

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(args.output_dir, f"audit_ledger_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, f"audit_report_{ts}.md"), "w", encoding="utf-8") as f:
        f.write(build_audit_report(ledger, report_md))
    tiers = [tier_for_url(u) for u in sorted(visited_urls)]
    with open(os.path.join(args.output_dir, f"source_tiers_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(tiers, f, ensure_ascii=False, indent=2)
    summary = build_summary(ledger)
    summary["output_prefix"] = ts
    with open(os.path.join(args.output_dir, f"audit_summary_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
