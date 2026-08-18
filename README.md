# port-to-claude

A Claude Code skill that decides — with evidence from your own repo and your own keys —
whether to move an app's LLM calls to Claude, and then opens the PR.

```
/plugin marketplace add alex-benoit/port-to-claude
/plugin install port-to-claude
```

Then, in any repo: *"port this app to Claude"*.

## Why this exists

Mechanical migration is already solved. Anthropic ships an OpenAI-compatible endpoint;
adapters and format converters are abundant. Changing the code is not the hard part.

**The confidence to change it is.** Which model fits which call site? What does it cost?
Does the structured output still parse? This skill is the decision layer, not another
converter.

## What it does

1. **Inventory** — walks the repo, finds LLM call sites across the direct SDKs and the
   common frameworks (pydantic-ai, LangChain, LlamaIndex, Vercel AI SDK, instructor,
   LiteLLM, Semantic Kernel), resolves module-level prompt constants to the sites that use
   them, and records declared output schemas. No keys needed.
2. **Recommend** — a Claude model **per call site**, by task shape. Most repos run one
   model for everything, so the tagging call is usually paying synthesis-model prices.
3. **Prove it** — a bounded side-by-side spot-check on real keys: token counts, latency,
   and schema-adherence rate, with the outputs shown.
4. **PR** — a branch whose diff is *only* the provider switch, with the whole analysis in
   the description.

## What it will not do

- **Write anything into your repo but the switch.** Fixtures are synthesised in a temp
  directory. No eval folder, no results file, no scratch. Merging leaves zero residue.
- **Push or merge without asking.**
- **Invent prices.** `pricing.json` ships with null rates deliberately. Rates are fetched
  at run time and stamped with a verification date; if they can't be verified, the cost
  column is dropped and you get token counts instead. A persuasive table built on stale
  prices is worse than no table.
- **Always recommend migrating.** Embeddings call sites are left alone — Anthropic doesn't
  serve them. Fine-tuned models are flagged, not swapped. If a call site is better where it
  is, the table says so.

## Fidelity tiers

| Tier | Keys | You get |
|---|---|---|
| Estimated | none | Call-site inventory, heuristic token counts, published-pricing maths |
| Baseline | incumbent only | Real incumbent tokens, latency, cost |
| Measured | both | Both sides real, plus side-by-side output and schema pass rates |

A Claude Code subscription does not cover programmatic inference — the measured tier needs
an API key from [console.anthropic.com](https://console.anthropic.com).

## Layout

```
skills/port-to-claude/
├── SKILL.md                   the decision layer: phases and guardrails
├── references/
│   ├── detection.md           call-site patterns, and what the scan misses
│   ├── model-mapping.md       task shape → Claude model
│   └── pricing.md             cost maths: caching, batch, honest token counting
└── scripts/
    ├── inventory.py           find call sites, resolve prompts, record schemas
    ├── spotcheck.py           bounded both-provider run on real keys
    ├── report.py              render the PR body
    └── pricing.json           rates, null until verified at run time
```

Stdlib only — no `pip install` to run any of it.

## Status

v0.1.0. Automated eval generation — the thing that would actually prove quality parity — is
deliberately out of scope. The spot-check is a confidence check with a stated sample size,
not statistical evidence.
