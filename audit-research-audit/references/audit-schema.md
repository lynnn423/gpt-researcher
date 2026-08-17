# Audit Layer Schema

## Audit Ledger (JSON)

Per-claim record produced by `run_audit.py`. This is the source of truth a reviewer audits claim by claim.

```json
{
  "claim": "Anthropic Q2 2026 revenue exceeded $11.5B",
  "source_url": "https://fortune.com/2026/08/15/anthropic-revenue-q2-11-5-billion-ipo-investors",
  "source_tier": "T2",
  "verdict": "verified",
  "confidence": 0.8,
  "evidence": "Fortune (Bloomberg feed) reports preliminary $11.5B+ Q2 figure",
  "search_path": []
}
```

| Field | Type | Meaning |
|---|---|---|
| `claim` | string | The atomic assertion being audited |
| `source_url` | string\|null | URL supporting the claim; null = `not found` |
| `source_tier` | T1-T4\|null | Authority grade of the supporting source |
| `verdict` | verified\|uncertain\|not found | Judgment |
| `confidence` | 0-1 float | Confidence in the verdict |
| `evidence` | string | Why the verdict was assigned (source content alignment) |
| `search_path` | string[] | Documented queries/sources searched for `not found` claims |

## Verdict semantics

| Verdict | Means | Not means |
|---|---|---|
| `verified` | A source supports the claim AND source content aligns with it | "A URL was visited" |
| `uncertain` | Weak/conflicting source, or inferred claim | "Probably true" |
| `not found` | No supporting source located | "Does not exist" — absence only within searched scope |

## Source tiers

| Tier | Scope |
|---|---|
| T1 | .gov/.edu, primary documents, company primary sources, peer-reviewed |
| T2 | Major reputable media (Reuters, Bloomberg, WSJ, NYT, CNBC, FT, The Verge, Wired, AP, Axios, Economist) |
| T3 | General media, reputable blogs |
| T4 | UGC, forums, social, low-authority |

Base confidence by tier: T1 0.9 · T2 0.8 · T3 0.6 · T4 0.35.

## Negative-evidence discipline

For every `not found` claim, `search_path` must state:
1. The exact query searched.
2. Which engines/databases.
3. Number of attempts.
4. Whether "confirmed absent in searched sources" or "could not confirm" — never claim global absence beyond what was searched.
