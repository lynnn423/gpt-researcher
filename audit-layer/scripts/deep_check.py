#!/usr/bin/env python3
"""Deep-check orchestration for the audit layer.

Stage 2 of the pipeline: select which claims from the audit ledger get a full
bullshit-detector fact-check, prepare them for the fact-checking workflow, and
merge the deep-check results back into the audit report.

This is an ORCHESTRATOR, not a replacement for bullshit-detector. It decides
WHAT to deep-check, prepares the input contract, and merges verdicts back.
The actual per-claim verification runs through the bullshit-detector skill
workflow (web search + evidence + verdict), which the agent executes.

Selection policy (configurable):
  - weak: claims with verdict 'uncertain' or 'not found' (evidence-poor rows).
  - load-bearing: top-N highest-confidence verified claims (the report's core
    conclusions, --top-n default 10).
  - Combined by default; use --only to restrict.

Usage:
    python deep_check.py --ledger audit_ledger_<ts>.json \
        --report audit_report_<ts>.md --output-dir deep_check_output/
    python deep_check.py --ledger ... --only load-bearing
    python deep_check.py --ledger ... --top-n 5

Outputs:
    deep_check_output/deep_check_plan.json    — the claims selected + why
    deep_check_output/verdicts.json           — merged deep-check verdicts
    deep_check_output/report_with_deepcheck.md — audit report + deep-check
                                                 column in body + appendix
"""

import argparse
import json
import os
from datetime import datetime

# Claim selection: which verdicts are "weak" (candidates for deep-check)
WEAK_VERDICTS = {"uncertain", "not found"}


def select_claims(ledger: list, only: str, load_bearing_max: float, top_n: int) -> list:
    """Select claims for deep-check.

    Default ('all'): deep-check every weak row (uncertain/not found) PLUS the
    top-N highest-confidence verified claims (the report's core conclusions).
    This spends the expensive fact-check on decision-critical + evidence-poor
    claims, not on every ordinary verified row.
    """
    plan = []
    seen = set()
    for i, rec in enumerate(ledger):
        verdict = rec.get("verdict")
        reasons = []
        if only in ("all", "weak") and verdict in WEAK_VERDICTS:
            reasons.append("weak")
        if reasons:
            plan.append(_plan_row(i, rec, reasons))
            seen.add(i)

    if only in ("all", "load-bearing") and top_n > 0:
        # pick top-N verified by confidence (core conclusions), skip already selected
        verified = [(i, rec) for i, rec in enumerate(ledger)
                    if rec.get("verdict") == "verified" and i not in seen]
        verified.sort(key=lambda x: x[1].get("confidence", 0), reverse=True)
        for i, rec in verified[:top_n]:
            plan.append(_plan_row(i, rec, ["load-bearing"]))
    return plan


def _plan_row(i, rec, reasons):
    return {
        "claim_index": i,
        "claim": rec["claim"],
        "audit_verdict": rec.get("verdict"),
        "audit_confidence": rec.get("confidence", 0.0),
        "source_url": rec.get("source_url"),
        "source_tier": rec.get("source_tier"),
        "select_reasons": reasons,
    }


def build_plan_markdown(plan: list) -> str:
    """Render the deep-check plan as a markdown list for the agent to execute."""
    lines = ["# Deep-Check Plan", "",
             "These claims need a full fact-check via the bullshit-detector "
             "workflow (per-claim web search + evidence + verdict). "
             "For each claim run the bullshit-detector skill: extract, verify "
             "with independent web search, and assign its verdict scale "
             "(confirmed / plausible / misleading / false / unverifiable).\n"]
    for i, p in enumerate(plan, 1):
        reasons = " + ".join(p["select_reasons"])
        src = f" (audit source: {p['source_url']})" if p["source_url"] else ""
        lines.append(
            f"{i}. **[{p['claim_index']}]** {p['claim']}  \n"
            f"   · audit: {p['audit_verdict']} conf {p['audit_confidence']} · "
            f"selected: {reasons}{src}")
    return "\n".join(lines)


def merge_verdicts(report_md: str, plan: list, verdicts: dict) -> str:
    """Merge deep-check verdicts into the audit report body + appendix.

    verdicts: {claim_index_str: {verdict, evidence, sources}}
    """
    lines = report_md.split("\n")
    out = []
    deep_rows = []
    for ln in lines:
        out.append(ln)
    # Append deep-check appendix
    out.append("\n---\n\n## Independent Fact-Check Appendix\n")
    for p in plan:
        idx = str(p["claim_index"])
        v = verdicts.get(idx)
        if not v:
            out.append(f"- **[{idx}]** {p['claim'][:90]}… — *not verified*")
            continue
        verdict = v.get("verdict", "unverifiable")
        icon = {"confirmed": "✅", "plausible": "🟡", "misleading": "🟠",
                "false": "❌"}.get(verdict, "❓")
        ev = v.get("evidence", "")
        srcs = "; ".join(s.get("url", "") for s in v.get("sources", []))
        out.append(
            f"- **[{idx}]** {p['claim']}  \n"
            f"  · {icon} {verdict} — {ev}  \n"
            f"  · deep-check sources: {srcs or '—'}")
        deep_rows.append((idx, verdict))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Deep-check orchestration for audit layer")
    ap.add_argument("--ledger", required=True, help="audit ledger JSON from run_audit.py")
    ap.add_argument("--report", required=True, help="audit report MD from run_audit.py")
    ap.add_argument("--output-dir", default="deep_check_output")
    ap.add_argument("--only", choices=["all", "load-bearing", "weak"], default="all")
    ap.add_argument("--top-n", type=int, default=10,
                    help="deep-check top-N highest-confidence verified claims (core conclusions, default 10)")
    ap.add_argument("--load-bearing-max", type=float, default=1.0,
                    help="(legacy) deep-check verified claims below this confidence; kept for compat")
    args = ap.parse_args()

    ledger = json.load(open(args.ledger, encoding="utf-8"))
    report_md = open(args.report, encoding="utf-8").read()
    plan = select_claims(ledger, args.only, args.load_bearing_max, args.top_n)

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(args.output_dir, f"deep_check_plan_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, f"deep_check_plan_{ts}.md"), "w", encoding="utf-8") as f:
        f.write(build_plan_markdown(plan))

    # If a verdicts.json already exists (from a prior run of the same plan), merge it.
    verdicts = {}
    verdicts_path = os.path.join(args.output_dir, "verdicts.json")
    if os.path.exists(verdicts_path):
        verdicts = json.load(open(verdicts_path, encoding="utf-8"))
    merged = merge_verdicts(report_md, plan, verdicts)
    with open(os.path.join(args.output_dir, f"report_with_deepcheck_{ts}.md"), "w", encoding="utf-8") as f:
        f.write(merged)

    print(json.dumps({
        "plan_claims": len(plan),
        "output_prefix": ts,
        "merged_verdicts": len(verdicts),
        "plan_md": f"deep_check_plan_{ts}.md",
        "merged_report": f"report_with_deepcheck_{ts}.md",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
