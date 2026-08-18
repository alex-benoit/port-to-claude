---
name: port-to-claude
description: This skill should be used when the user wants to evaluate or perform a migration of an application's LLM calls from another provider (OpenAI, Gemini, Mistral, Azure OpenAI) to Claude — e.g. "port this app to Claude", "what would it cost to switch to Claude", "compare our GPT calls against Claude", "should we migrate off OpenAI". Inventories call sites, recommends a Claude model per call site, proves the swap with side-by-side output and a cost delta, and opens a PR containing only the switch.
version: 0.1.0
---

# Port to Claude

Mechanical migration is already solved — an OpenAI-compatible endpoint and a dozen
adapters exist. **This skill is not a converter. It is a decision layer.** Its job is to
answer "should we, and where, and what will it cost" with evidence from the developer's
own repo and their own keys.

The deliverable is a PR whose diff is **only the provider switch** and whose *description*
carries the whole analysis. Nothing else lands in the target repo — no eval directories,
no fixtures, no scratch files. That is a hard constraint, not a preference: it is what
makes the tool safe to run on a codebase you care about, and it means merging leaves zero
migration-tool residue.

## Guardrails

Read these before starting. They are what separate this from vendor spam.

1. **Never auto-merge, never push without asking.** Draft the branch and the PR body, show
   them, ask. Pushing is outward-facing.
2. **Be willing to say "keep the incumbent here."** If a call site is cheaper, faster, or
   better served by what it already runs, say so in the table with the reason. A tool that
   always recommends migrating gets read as promotional and uninstalled. Honest losses are
   what get the other rows believed.
3. **Never invent prices or benchmark numbers.** Every figure is either measured in this
   run or read from `references/pricing.md` with its verification date shown. If a rate is
   unverified, label the column **estimated** in the output. Do not smooth over a gap with
   a plausible number.
4. **Never write fixtures or results into the target repo.** Use a temp directory.
5. **Keys stay where they are.** Read them from the target repo's environment / `.env`
   only to pass to the provider APIs. Never print a key, never send one anywhere but the
   provider it belongs to, never write one into the report.
6. **The user's Claude Code subscription cannot fund the benchmark.** Programmatic
   inference in the spot-check needs an Anthropic API key. Say this plainly rather than
   implying the subscription covers it.

## Phase 1 — Inventory (no keys, always runs)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/port-to-claude/scripts/inventory.py" <repo-path> --out "$TMP/inventory.json"
```

The script does the mechanical work: walks the tree, matches provider call-site patterns
across the frameworks in `references/detection.md`, resolves module-level prompt constants
to the call sites that use them, and records declared output schemas.

**You do the judgment the script cannot.** For each call site, read the surrounding code
and characterise it:

- **Task shape** — classification / extraction / short generation / long-context synthesis
  / agentic tool loop. This drives the model choice more than anything else.
- **Latency sensitivity** — is a user waiting on this response, or does it run after the
  fact? Anything not user-facing is a Batch API candidate.
- **Prompt reuse** — is the system prompt a long static block sent on every call? That is a
  prompt-caching candidate, and ignoring it understates the comparison badly.
- **Failure cost** — what breaks downstream if the output is malformed? A structured output
  that feeds a paid third-party API is worth more care than one that renders a suggestion.

If the script finds nothing, do not guess. Report that the repo has no detected call sites
and stop.

## Phase 2 — Recommend

Map each call site to a Claude model using `references/model-mapping.md`. Recommend **per
call site, not per repo.** Most repos run one model for every call, which is itself the
finding worth surfacing: a tagging task paying synthesis-model prices is overspend
regardless of vendor. Saying that builds the credibility that carries the rest of the table.

Where prompt caching or Batch changes the economics, say so on that row with the reason.

## Phase 3 — Prove it (bounded, opt-in)

Three fidelity tiers. Offer the highest the available keys support, and be explicit about
which one produced the numbers.

| Tier | Keys | Produces |
|---|---|---|
| **Estimated** | none | Token counts from a heuristic, cost from published pricing. Label every figure estimated. |
| **Baseline** | incumbent only | Real incumbent tokens/latency/cost. Claude side stays published-pricing estimate. |
| **Measured** | both | Real tokens, latency, cost and side-by-side output for both. The only tier that can speak to quality. |

Generate inputs by **synthesising representative fixtures in a temp directory** from the
repo's own types, tests, and schemas. Never pull production data, never write fixtures into
the repo.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/port-to-claude/scripts/spotcheck.py" --cases "$TMP/cases.json" --out "$TMP/results.json"
```

Keep it bounded — a handful of samples per call site. This is a spot-check for confidence,
not an eval suite. **Tell the user the sample size and that it is not statistical
evidence.** Automated eval generation is the expensive, deferred phase; do not let it creep
in here.

For structured-output call sites, the metric that matters is **schema adherence**, not
prose quality. Validate each response against the declared schema and report the pass rate.

If the user has no Anthropic key, this is the moment to point them at
`https://console.anthropic.com` — they are asking for the number that requires it.

## Phase 4 — The PR

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/port-to-claude/scripts/report.py" --inventory "$TMP/inventory.json" --results "$TMP/results.json" --out "$TMP/pr-body.md"
```

Then:

1. Branch from the current head.
2. Apply **only** the provider switch — client/model construction and config. No
   refactors, no drive-by fixes, no new files, no reformatting. If the switch needs a
   dependency added, that is in scope; nothing else is.
3. Verify the repo still builds / type-checks / passes tests. Report failures honestly.
4. Show the user the diff and the rendered PR body. **Ask before pushing.**

The PR body carries: the per-call-site table, which tier produced the numbers, the
side-by-side samples, what the tool recommends *against* switching, and how to reproduce.

## Reference files

- `references/detection.md` — call-site patterns per SDK and framework
- `references/model-mapping.md` — task shape → Claude model and when to use effort levels
- `references/pricing.md` — published rates with verification dates, caching and Batch maths
