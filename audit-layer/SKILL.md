---
name: audit-research-audit
description: Turn a research report into an auditable, decision-ready deliverable by labeling every claim with a confidence verdict (verified/uncertain/not found), mapping each to its source URL, grading sources by authority (T1-T4), documenting negative findings with search paths, and organizing results into an audit report (executive summary + per-section claims) in English or Chinese (--lang). Includes a two-stage pipeline: stage 1 (run_audit.py) labels traceability on top of research output; stage 2 (deep_check.py) selects load-bearing + weak claims and fact-checks them, merging confirmed/false/misleading verdicts into the report. This is the audit + fact-check layer of the audit-research system (research via the audit-research MCP deep_research tool, then audit with this skill). USE THIS whenever the user asks to audit a research report, make a deliverable "auditable"/"traceable"/"every claim verified", prepare due-diligence or IC/LP material where each conclusion must be verifiable, run the audit layer on research output, add confidence labels / source tiering / negative-evidence search paths to a report, run "audit-research" or "run the audit", OR asks to format a research report as an audited deliverable with an executive summary, per-section claim verification, detailed evidence notes, and bilingual (EN/zh) output. Do NOT use for the research itself (use competitive-analysis or weizhena, or the audit-research MCP deep_research tool), and do NOT use for standalone fact-checking of arbitrary content (use bullshit-detector) — this skill audits a finished report's traceability and fact-checks selected claims within it.
---

# Audit Research Audit Layer (audit-research)

This is the **audit + fact-check layer of the audit-research system**: research
is done by the audit-research MCP tool (deep_research / quick_search, backed by
GPT Researcher), then this skill audits the output's traceability and
fact-checks selected claims. GPT Researcher produces a cited report, but it does
**not** label confidence, document negative findings, or map every claim to its
exact source. This skill adds that trust layer as a post-processing stage. It is
designed to be **independent** — it reads the research output and produces an
audited deliverable, without touching the research engine's core logic.

The full pipeline is two stages:

1. **Audit layer** (`run_audit.py`) — traceability: every claim → source → verdict.
2. **Deep-check** (`deep_check.py`) — truth: select load-bearing + weak claims,
   fact-check them via the bullshit-detector workflow, merge verdicts back.

## When to use

- A client needs a report where **every claim is traceable to a source and labeled** (due diligence, IC/LP materials, compliance, litigation support).
- The deliverable must distinguish **conclusions from evidence from uncertainty**.
- You need to prove **what does NOT exist** (negative findings) with documented search paths.
- Critical claims must be **independently fact-checked**, not just source-attributed.
- You are packaging GPT Researcher output as a professional, auditable product.

## Output contract

Every audit run produces:

1. **Audit report (Markdown)** — the audited deliverable, organized by report
   sections, with executive summary, per-claim verdict + confidence + evidence
   + source link. Default English; `--lang zh` for Chinese UI labels.
2. **Audit ledger (JSON)** — per-claim record: `{claim, source_url, source_tier, verdict, confidence, evidence}`.
3. **Source tiering list** — every visited URL graded by authority tier.

The **raw research report** (from GPT Researcher) is preserved alongside and is
part of the deliverable: the client reads the raw report for content and the
audit report for traceability. Deliver both together.

## Pipeline

### 1. Collect GPT Researcher structured output

Run GPT Researcher with JSON export enabled. You need:

- `research_sources` — structured sources (title + content + url), from `get_research_sources()`.
- The final report markdown.
- `visited_urls` — the full set of visited URLs.

### 2. Extract claims from the report

Split the report body into discrete, atomic claims. One claim = one verifiable assertion. Do NOT split on every sentence mechanically — merge sentence fragments that form a single assertion (e.g., "X revenue was $10B in 2025" is one claim, not three).

### 3. Map each claim to a source

For every claim, identify which `research_source` supports it. A claim may map to zero, one, or multiple sources.

- **Zero sources** → verdict `not found` (fabricated or unsupported claim).
- **One or more sources** → verify the source content actually supports the claim (not just that a URL was visited). This is claim-source alignment, not URL existence.

### 4. Assign verdict + confidence

For each claim:

| Verdict | Meaning | Typical confidence |
|---|---|---|
| `verified` | Source supports claim, source is authoritative enough | 0.8-1.0 |
| `uncertain` | Source is weak/conflicting, or claim is inferred | 0.3-0.8 |
| `not found` | No source supports it, or it is false | 0-0.3 |

### 5. Source tiering

Grade every URL by authority:

| Tier | Sources |
|---|---|
| T1 | Official (.gov/.edu), company primary sources, peer-reviewed, primary documents |
| T2 | Reputable major media (Reuters, Bloomberg, WSJ, NYT, CNBC, The Verge...) |
| T3 | General media, reputable blogs |
| T4 | UGC, forums, social, low-authority |

### 6. Negative-evidence layer

For every `not found` claim, document:
- The exact search query used.
- The sources searched (which engines/databases).
- Number of attempts.
- Statement: "confirmed absent in searched sources" vs "could not confirm" — never overclaim absence beyond what was actually searched.

This is the layer AI tools cannot produce by themselves: proving something does not exist requires documenting the search path.

### 7. Inline claim→source links

In the audit report, render each claim with a bracketed inline link to its supporting source: `Claim text [T1: source-domain](url)`. Where no source exists, mark `[no source]`.

### 8. Compose the audit report

Three sections, strictly separated:

- **Conclusions** — what the research establishes, each with inline source links.
- **Evidence** — the supporting material, source-by-source, with tiers.
- **Uncertainty** — every `not found`/`uncertain` claim, with the negative-evidence search path.

## Scripts

- `scripts/run_audit.py` — the main audit runner. Reads research JSON + report markdown, produces ledger + report.

  **Usage:**
  ```bash
  # from the gpt-researcher repo root
  .venv/bin/python audit-layer/scripts/run_audit.py \
    --input <research_output.json> --output-dir audit_output/
  ```

  **Input JSON** (`--input`): `{"report": "...", "research_sources": [...], "visited_urls": [...]}`. Alt: `--report`, `--sources`, `--visited` separately.

  **LLM config** (for claim-source alignment):
  - `DEEPSEEK_API_KEY` -> DeepSeek (default `deepseek-chat`)
  - `OPENAI_API_KEY` -> OpenAI (default `gpt-4o-mini`)
  - `ANTHROPIC_API_KEY` -> Anthropic (default `claude-3-5-haiku`)
  - `AUDIT_LLM_MODEL` overrides the model.
  - `AUDIT_LLM=0` forces deterministic (heuristic) mode — no LLM, no API cost.
  - No key set -> deterministic mode automatically.

  **Outputs** (in `--output-dir`): `audit_ledger_<ts>.json`, `audit_report_<ts>.md`, `source_tiers_<ts>.json`, `audit_summary_<ts>.json`.

- `scripts/run_evals.py` — drives gpt-researcher through the evals (requires `DEEPSEEK_API_KEY` + `DASHSCOPE_API_KEY` for embeddings + `TAVILY_API_KEY`). Writes per-eval `research_output.json`. Uses `evals/config.deepseek.json` (CONFIG_PATH) so it runs on DeepSeek/Tavily/DashScope without OpenAI keys.

- `evals/evals.json` — the three eval prompts (due diligence / competitive / market-size).
- `workspace/iteration-N/` — skill-creator eval workspace: `eval-<id>/with_skill/` (audit outputs) vs `eval-<id>/baseline/` (raw gpt-researcher report).

## Deep-check stage (stage 2: truth)

After the audit layer labels traceability, optionally fact-check the claims that
matter. `deep_check.py` selects which claims to deep-check and merges the
fact-check verdicts back.

```bash
# 1. Select claims (weak + top-N core conclusions by default)
.venv/bin/python audit-layer/scripts/deep_check.py \
  --ledger audit_ledger_<ts>.json --report audit_report_<ts>.md \
  --output-dir deep_check_output/
#   -> writes deep_check_plan_<ts>.md: the claims to fact-check
```

```text
# 2. For each selected claim, run the bullshit-detector workflow
#    (independent web search + evidence + verdict scale:
#     confirmed / plausible / misleading / false / unverifiable).
#    Save results as deep_check_output/verdicts.json keyed by claim_index:
#    {"3": {"verdict":"confirmed","evidence":"...","sources":[{"url":...,"tier":2}]}}
```

```bash
# 3. Re-run deep_check.py — it picks up verdicts.json and merges
#    the deep-check verdicts into the report (body + appendix).
.venv/bin/python audit-layer/scripts/deep_check.py \
  --ledger ... --report ... --output-dir deep_check_output/
#   -> writes report_with_deepcheck_<ts>.md
```

**Selection policy** (`--only`, `--top-n`):
- `weak` (always on in `all`): claims verdict `uncertain` or `not found` — evidence-poor rows.
- `load-bearing` (top-N by confidence, `--top-n` default 10): the report's core conclusions.
- `--only load-bearing` / `--only weak` to restrict.

The deep-check stage does NOT replace bullshit-detector — it is the orchestrator
that decides WHAT to check and merges results. The per-claim verification itself
runs through the bullshit-detector skill workflow.

## Rules

- **Never invent a source.** If a claim has no source in `research_sources`, it is `not found` — do not fabricate a URL to make it pass.
- **Never overclaim absence.** "Not found in searched sources" ≠ "does not exist". State exactly what was searched.
- **Keep confidence honest.** Do not inflate confidence to make the report look better. A trustworthy audit labels weak evidence as weak.
- **The ledger is the source of truth.** The Markdown report is a rendering; the JSON ledger is what a reviewer should be able to audit claim by claim.

## Do not

- Do not re-run research inside this skill — the audit layer is a post-processor, not a researcher.
- Do not modify GPT Researcher core logic; this layer operates on its output.
- Do not merge the three report sections — the separation is the point.
