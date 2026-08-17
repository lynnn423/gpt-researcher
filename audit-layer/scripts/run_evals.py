#!/usr/bin/env python3
"""Drive gpt-researcher through the 3 evals, exporting JSON for audit-layer.

Run: python run_evals.py  (requires DEEPSEEK_API_KEY + TAVILY_API_KEY)
Output: evals/run-<ts>/eval-<id>/research_output.json
"""
import asyncio
import json
import os
import sys
from datetime import datetime

# Use custom config: DeepSeek LLM + Tavily retriever (avoids default openai:gpt-5.4)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["CONFIG_PATH"] = os.path.join(_REPO_ROOT, "audit-layer", "evals", "config.deepseek.json")
sys.path.insert(0, _REPO_ROOT)

from gpt_researcher import GPTResearcher

EVALS = [
    {
        "id": 1,
        "name": "due-diligence-company",
        "query": "Conduct due diligence on the AI company Anthropic: verify founding year, total funding raised, current CEO, and whether it has any confirmed regulatory penalties or major lawsuits in 2025-2026.",
    },
    {
        "id": 2,
        "name": "competitive-landscape",
        "query": "Analyze the competitive landscape of AI coding assistants in 2026: list the main products (e.g. Claude Code, Cursor, Copilot, Windsurf), compare pricing and positioning, and note any market-share figures with their sources.",
    },
    {
        "id": 3,
        "name": "market-research-verification",
        "query": "Estimate the total addressable market size for AI-powered market research services in 2026. Note where different sources give conflicting numbers and which figures could not be confirmed.",
    },
]


async def run_single(query: str) -> dict:
    researcher = GPTResearcher(query=query, report_type="research_report", verbose=False)
    await researcher.conduct_research()
    report = await researcher.write_report()
    sources = researcher.get_research_sources()
    visited = researcher.get_source_urls()
    return {"report": report, "research_sources": sources, "visited_urls": visited}


async def main():
    out_root = os.path.join(os.path.dirname(__file__), f"run-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(out_root, exist_ok=True)
    for ev in EVALS:
        print(f"=== eval {ev['id']} ({ev['name']}) ===")
        try:
            data = await run_single(ev["query"])
            # strip References section for audit (audit layer re-derives sources)
            data["report_body"] = data["report"]
            out_dir = os.path.join(out_root, f"eval-{ev['id']}")
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "research_output.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  OK: {len(data['research_sources'])} sources, report {len(data['report'])} chars")
            # minimal rate-limit gap
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  FAIL: {e}")
    print(f"DONE -> {out_root}")


if __name__ == "__main__":
    asyncio.run(main())
