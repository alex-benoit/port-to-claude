# Task shape → Claude model

Recommend per call site. The single most common finding in a real repo is that every call
site runs the *same* model, which means the cheap ones are overpaying and the hard ones may
be underserved. That observation is true regardless of vendor, which is exactly why it earns
the credibility that carries the rest of the table.

Check `references/pricing.md` for the current model list before recommending — model names
change faster than this file does.

Every row here produces a PR with a working diff, including the rows whose recommendation is
against merging it. The recommendation goes in the body, not in the decision to write the
code. See Phase 4 of `SKILL.md` for the change-kind labels these rows map to.

## By shape

| Task shape | Looks like | Recommend | Why |
|---|---|---|---|
| **Classification / tagging / routing** | Short input, small fixed label set, structured out | Smallest current model (Haiku tier) | Nearly always the biggest saving in a repo — these usually run on a synthesis-tier model for no reason |
| **Extraction from long context** | Transcript/document in, schema out | Mid tier (Sonnet) | Needs the context handling; rarely needs the top tier |
| **Short generation** | A sentence, a title, a summary line | Small-to-mid | Cheap; judge on quality in the spot-check, not price |
| **Long-context synthesis** | Many documents in, reasoned analysis out | Mid-to-top tier | The one place paying up is usually right |
| **Agentic tool loop** | Multi-turn, tool calls, retries | Mid-to-top tier | Tool-use reliability dominates per-token cost — a cheap model that loops twice is not cheap |
| **Embeddings** | `.embeddings.create` | **Third-party redirect — Voyage** | Anthropic serves no embeddings model and recommends Voyage. `voyage-4-lite` is $0.02/M, at parity with `text-embedding-3-small`, so the swap is usually cost-neutral. Ship the diff, label it a third-party redirect, and say plainly it is not a move to Anthropic |
| **Audio transcription** | `.audio.transcriptions`, Whisper | **Partial port** | No Claude model accepts audio input, so the transcription itself does not move. The portable part is domain hinting — a `prompt` parameter correcting jargon, names, or codes is a language task, and a Claude pass over the raw transcript does it better. Say clearly that speech-to-text stays where it is |
| **Moderation** | `.moderations.create` | **Full swap, usually recommend against** | Portable to a prompted Claude classifier, but the incumbent endpoint is free and this is not. Ship the diff, show the per-call cost it introduces, and let the reviewer weigh that against the control over categories and thresholds it buys |
| **Fine-tuned model** | Custom model ID | **Flag, do not auto-switch** | Not a like-for-like swap |

## Extended thinking

Enable it where the task is genuinely reasoning-shaped — multi-constraint synthesis, tricky
extraction, planning. It costs output tokens, so it must earn its place in the spot-check.

Do **not** turn it on for classification, tagging, or short generation. It inflates cost on
exactly the call sites whose whole appeal is being cheap.

If the incumbent call site already sets a reasoning-effort parameter, that is a strong hint
the developer believes the task needs it — start there and verify.

## Things that change the economics more than model choice

Check these on every row before concluding a call site is expensive:

- **Prompt caching** — a long static system prompt resent on every call is the classic case.
  Model this; leaving it out understates the comparison badly.
- **Batch** — anything with no user waiting on it. Post-hoc extraction, nightly
  summarisation, backfills. Substantial discount for a latency tradeoff nobody feels.
- **Prompt length** — sometimes the honest recommendation is that the prompt is bloated and
  the fix is not a vendor change at all. Say it if you see it.
