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

## Phase 0 — Pricing (first, always)

**Populate `scripts/pricing.json` before anything else.** It ships with null rates, and
every cost figure in the output depends on it. Doing this first means the cost column is
available from the first table you show, rather than discovered to be missing at the end.

Fetch both providers' current published rates and write them in, with today's date as
`verified_on`:

- Claude — `https://platform.claude.com/docs/en/about-claude/pricing`
- The incumbent's pricing page (OpenAI: `https://developers.openai.com/api/docs/pricing`)

Record per model: `input`, `output`, `cache_write_5m`, `cache_read`, `batch_input`,
`batch_output`. Use the exact model ids the repo calls, so `report.py` can match them.

If a rate cannot be fetched — page moved, model not listed, Azure or a negotiated
enterprise rate — **ask the user for that number rather than guessing**. A missing rate
means the cost column is dropped for that model, which is the designed behaviour and far
better than a confident wrong figure.

Re-fetch on every run. Rates move, and a cached `pricing.json` from last month is exactly
the stale-number failure this phase exists to prevent.

### Tokenizers differ between model generations

Claude 4.7 and later use a newer tokenizer that produces roughly 30% more tokens for the
same text than earlier models. So a Sonnet 5 and a Haiku 4.5 quote for a byte-identical
prompt are not comparable on token count alone, and a cost estimate does not transfer
between them. Count tokens per model — never scale one model's count by a price ratio.

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

### Finding the keys

Look for the incumbent key in the environment, then in the repo's `.env` / `.env.local`.
Look for `ANTHROPIC_API_KEY` the same way.

**If there is no Anthropic key, stop and ask the user — do not silently fall back to the
estimated tier.** They may well have a key you cannot see, and the choice of whether to
spend a few cents proving the migration is theirs, not yours. Ask with these options:

- **Paste a key and run the real comparison** — measured tier. State roughly how many calls
  it will make and that the cost is cents, so the tradeoff is legible.
- **Skip it, go with reasoning alone** — estimated tier. Say plainly that the PRs will then
  carry no measured numbers, and name the specific call site whose risk that leaves
  unresolved.
- **Get a key first** — point at `https://console.anthropic.com`, then re-run.

Take a pasted key through the environment for the run only. Never write it to a file,
never echo it, never put it in a PR body or commit.

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

### Every call site must show real output, side by side

**A cost table alone cannot support the decision.** The question is never "is Claude
cheaper" — it is "is this output worth this price", and that is unanswerable without seeing
the text. Every call site's PR body carries **at least one verbatim sample from the
incumbent and from each candidate Claude model**, on the same input, in a `<details>` block.
Never summarise or characterise an output in place of showing it.

Then actually read them, and report what you find:

- **Check each output against the constraints the system prompt states.** Prompts usually
  forbid specific things — a preamble, an imperative opening, naming example companies,
  ellipses in a quote. Grep the samples for those exact violations and report the rate. This
  is the highest-value check in the whole run and it is invisible to token counts and schema
  validation alike. A model can be schema-valid, cheap, and still ignore the brief.
- **Note verbosity differences**, since they drive cost. If a model is more expensive purely
  because it writes more, say so — that is a prompt fix, not a vendor verdict.
- **Say when the samples do not settle it.** Three samples of subjective prose quality is a
  prompt for the reviewer's judgement, not a verdict. Show the text and let them decide.

### Benchmark against what actually runs

Read the deployed model, not the code default — they diverge. Check the repo's `.env`, the
deployment platform's environment variables, and any config table, and reconcile them
before choosing a baseline. If they disagree, **report the mismatch as a finding** and ask
which is authoritative before running.

If the incumbent has a materially cheaper model in the same family, benchmark that too. The
honest comparison is against the best-value option the incumbent offers, not the one that
happens to be configured — and it changes the answer far more than the choice of Claude
model does.

If the user has no Anthropic key, this is the moment to point them at
`https://console.anthropic.com` — they are asking for the number that requires it.

## Phase 4 — The PRs

**One PR per call site, not one PR for the migration.** A single sweeping PR forces an
all-or-nothing decision on a change whose whole appeal is that it is reversible. Separate
PRs let a team migrate the cheapest call site, watch it in production for a week, and
continue — or stop. That is the shape that actually gets merged.

They stack, because the call-site changes share a base:

1. **Base PR — config and dependency.** Adds the Anthropic settings and the SDK/extra
   **without removing the incumbent**. No behaviour change; nothing calls it yet. This PR
   is safe to merge on its own and makes every later one small.
2. **One PR per call site**, each branched off the previous so its diff shows only its own
   change. Body carries that call site's own evaluation: task shape, model chosen and why,
   its own spot-check rows, and the specific failure mode a reviewer should look for.
3. **Cleanup PR — remove the incumbent** config, dependency, and now-stale references. Only
   valid once every call site has moved, so it goes last and merges last.

Rules for each PR in the stack:

- Set the base branch to the previous PR's branch, so reviewers see one change at a time.
- The diff is that call site's switch and nothing else. No refactors, no drive-by fixes.
- Verify the repo still builds / type-checks / passes tests **at every step in the stack**,
  not just at the end. A stack that only works at the tip is one PR wearing a costume.
- If two call sites live in the same file, they still get their own PR — stack them so the
  second rebases cleanly.

Generate each body from the shared analysis plus that call site's rows:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/port-to-claude/scripts/report.py" --inventory "$TMP/inventory.json" --results "$TMP/results.json" --site "app/foo.py:42" --out "$TMP/pr-foo.md"
```

Show the user the full stack — every branch, every diff, every body — and **ask before
pushing any of it.** Pushing is outward-facing, and a nine-PR stack lands on other people's
review queues.

## Reference files

- `references/detection.md` — call-site patterns per SDK and framework
- `references/model-mapping.md` — task shape → Claude model and when to use effort levels
- `references/pricing.md` — how to populate rates, caching and Batch maths
