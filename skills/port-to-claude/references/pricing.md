# Pricing

## Rates are not baked into this skill, on purpose

`pricing.json` ships with **null rates**. Per-token prices, model names, and discount terms
move faster than a plugin gets updated, and a migration tool that quietly computes a
persuasive table from stale numbers is worse than one that refuses — the output looks
authoritative and is wrong.

So: **Phase 0 of the skill fetches current rates and writes them into `pricing.json`
before anything else runs.** `report.py` refuses to emit a cost column when the rates it
needs are null, and labels every figure with the `verified_on` date it used. Re-fetch every
run — a `pricing.json` left over from last month is the stale-number failure this exists to
prevent.

Sources to fetch:

- Claude — `https://docs.claude.com/en/docs/about-claude/pricing`
- OpenAI — `https://openai.com/api/pricing/`
- Azure OpenAI — regional, per-deployment; ask the user rather than guessing

If a rate cannot be fetched, ask the user for it or drop the cost column entirely and
present the token counts alone. Token counts measured on real calls are still a real
finding; an invented price is not.

## The maths

Cost per call, all figures per million tokens:

```
base       = (input_tokens  / 1e6) * input_rate
           + (output_tokens / 1e6) * output_rate
```

**Prompt caching.** A static system prompt resent on every call is the case that matters.
Cache writes cost more than base input; cache reads cost substantially less. Over N calls
sharing one prefix, you pay the write roughly once and the read N−1 times:

```
cached     = (prefix_tokens / 1e6) * cache_write_rate
           + (prefix_tokens / 1e6) * cache_read_rate * (N - 1)
           + (variable_input_tokens / 1e6) * input_rate * N
           + (output_tokens / 1e6) * output_rate * N
```

Model this whenever a call site has a long static system prompt. Omitting it understates
the comparison badly, and it is the single most common way these tables get the answer
wrong.

**Batch.** Applies to call sites with no user waiting. Apply the published batch discount to
both input and output for those rows only, and mark the row so the reader knows the
tradeoff is latency.

**Cache TTL is a real constraint.** The discount only lands if calls actually arrive inside
the window. A call site invoked a few times an hour may never hit a warm cache — check the
call frequency before claiming the saving.

## Counting tokens honestly

- **Anthropic** — use the token-counting endpoint. Exact, and it does not run inference.
  Count per model: Claude 4.7 and later use a newer tokenizer producing roughly 30% more
  tokens for the same text, so counts do not transfer between model generations and a cost
  estimate cannot be scaled from one to another.
- **Incumbent** — take `usage` off a real response when a key is available.
- **Neither** — `inventory.py` falls back to a characters/4 heuristic. It is a heuristic;
  anything derived from it must be labeled **estimated** in the output, including in the PR
  body. Do not present it in the same column as measured numbers without the label.

## Reporting rules

1. Show which tier produced each number (estimated / baseline / measured).
2. Show the `verified_on` date for the rates used.
3. Never blend a heuristic token count and a measured one into one figure.
4. Volume assumptions are the user's, not yours. If you project a monthly saving, state the
   call volume you assumed and where it came from — or ask.
