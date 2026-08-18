#!/usr/bin/env python3
"""Render the PR body from the inventory and spot-check results.

Refuses to emit a cost column when the rates it needs are null, and stamps every table with
the tier that produced its numbers. A migration table that looks authoritative and is built
on stale or invented prices is worse than one that shows token counts and says so.

Stdlib only.
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def load_pricing(path):
    if not path.exists():
        return {}, None
    data = json.loads(path.read_text(encoding="utf-8"))
    models = {k: v for k, v in data.get("models", {}).items() if not k.startswith("_")}
    return models, data.get("verified_on")


def cost(model, rates, input_tokens, output_tokens):
    entry = rates.get(model)
    if not entry or entry.get("input") is None or entry.get("output") is None:
        return None
    return (input_tokens / 1e6) * entry["input"] + (output_tokens / 1e6) * entry["output"]


def money(value):
    if value is None:
        return "—"
    return f"${value:.6f}" if value < 0.01 else f"${value:.4f}"


def aggregate(results):
    grouped = defaultdict(list)
    for row in results:
        grouped[(row["case"], row["vendor"], row["model"])].append(row)

    out = {}
    for key, rows in grouped.items():
        ok = [r for r in rows if "error" not in r]
        if not ok:
            out[key] = {"error": rows[0].get("error", "all samples failed"), "n": len(rows)}
            continue
        schema_rows = [r for r in ok if "schema_ok" in r]
        out[key] = {
            "n": len(ok),
            "errors": len(rows) - len(ok),
            "input_tokens": round(statistics.mean(r["input_tokens"] for r in ok if r.get("input_tokens"))) if any(r.get("input_tokens") for r in ok) else None,
            "output_tokens": round(statistics.mean(r["output_tokens"] for r in ok if r.get("output_tokens"))) if any(r.get("output_tokens") for r in ok) else None,
            "latency_s": round(statistics.median(r["latency_s"] for r in ok), 2),
            "schema_pass": (sum(1 for r in schema_rows if r["schema_ok"]), len(schema_rows)) if schema_rows else None,
            "sample_text": ok[0].get("text", ""),
        }
    return out


def render(inventory, spotcheck, rates, verified_on):
    lines = []
    add = lines.append

    summary = inventory.get("summary", {})
    add("## What this PR does")
    add("")
    add(
        f"Switches the {summary.get('invocations', '?')} LLM call site(s) in this repo to "
        "Claude. **The diff is the provider switch and nothing else** — no refactors, no "
        "new files, no eval directory. The analysis below lives in this description so "
        "merging leaves no migration-tool residue in the codebase."
    )
    add("")

    add("## Call sites found")
    add("")
    add(f"- **{summary.get('invocations', 0)}** invocations, **{summary.get('edit_points', 0)}** client-construction points (where this diff lands)")
    add(f"- **{summary.get('structured_output', 0)}** use structured output — schema adherence is the risk that matters")
    add(f"- Frameworks: {', '.join(summary.get('frameworks', [])) or 'none'}")
    if summary.get("embeddings"):
        add(f"- **{summary['embeddings']} embedding call site(s) left untouched** — Anthropic does not serve embeddings")
    env_models = summary.get("model_env_vars") or {}
    if env_models:
        add(f"- Model config found in env: {', '.join(f'`{k}={v}`' for k, v in env_models.items())}")
    add("")

    invocations = [s for s in inventory.get("call_sites", []) if s.get("role") == "invocation"]
    if invocations:
        add("| Call site | Structured | System prompt | Notes |")
        add("|---|---|---|---|")
        for site in invocations:
            tokens = site.get("prompt_tokens_estimated")
            prompt = f"~{tokens} tok (est.)" if tokens else "runtime-built"
            note = "" if site.get("prompt_resolved") else "read by hand — prompt not statically resolvable"
            add(f"| `{site['file']}:{site['line']}` | {'yes' if site['structured_output'] else 'no'} | {prompt} | {note} |")
        add("")

    if not spotcheck:
        add("## Measurement")
        add("")
        add(
            "**Tier: estimated.** No spot-check was run, so there are no measured token "
            "counts, latencies, or outputs here. Prompt sizes above come from a "
            "characters/4 heuristic. Re-run with both API keys set for measured numbers."
        )
        add("")
        return "\n".join(lines)

    agg = aggregate(spotcheck.get("results", []))
    n = spotcheck.get("samples_per_case", 1)
    priced = bool(rates)

    add("## Side-by-side")
    add("")
    add(f"**Tier: measured.** {n} sample(s) per case on live keys.")
    if priced:
        add(f"Rates verified {verified_on or '**unverified — treat cost as indicative**'}.")
    else:
        add("**No verified pricing available, so the cost column is omitted.** Token counts below are real.")
    add("")

    header = "| Case | Vendor | Model | In tok | Out tok | Latency (med) | Schema"
    divider = "|---|---|---|---|---|---|---"
    if priced:
        header += " | Cost/call"
        divider += "|---"
    add(header + " |")
    add(divider + "|")

    for (case, vendor, model), stats in sorted(agg.items()):
        if "error" in stats:
            row = f"| {case} | {vendor} | `{model}` | — | — | — | **failed**"
            add(row + (" | — |" if priced else " |"))
            continue
        schema = "—"
        if stats["schema_pass"]:
            passed, total = stats["schema_pass"]
            schema = f"{passed}/{total}"
        row = (
            f"| {case} | {vendor} | `{model}` | {stats['input_tokens'] or '—'} | "
            f"{stats['output_tokens'] or '—'} | {stats['latency_s']}s | {schema}"
        )
        if priced:
            value = cost(model, rates, stats["input_tokens"] or 0, stats["output_tokens"] or 0)
            row += f" | {money(value)}"
        add(row + " |")
    add("")
    add(f"> {spotcheck.get('caveat', '')}")
    add("")

    add("<details><summary>Sample outputs</summary>")
    add("")
    for (case, vendor, model), stats in sorted(agg.items()):
        if "error" in stats:
            continue
        add(f"**{case} — {vendor} / {model}**")
        add("")
        add("```")
        text = stats["sample_text"] or ""
        add(text[:1200] + ("…" if len(text) > 1200 else ""))
        add("```")
        add("")
    add("</details>")
    add("")

    add("## Reproduce")
    add("")
    add("```bash")
    add("/plugin marketplace add alex-benoit/port-to-claude")
    add("/plugin install port-to-claude")
    add("```")
    add("")
    add("Then ask Claude Code to port this repo to Claude. Fixtures are synthesised in a temp")
    add("directory and never written here.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Render the migration PR body.")
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--results", type=Path, help="spot-check results (omit for estimated tier)")
    ap.add_argument("--pricing", type=Path, default=HERE / "pricing.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    spotcheck = json.loads(args.results.read_text(encoding="utf-8")) if args.results and args.results.exists() else None
    rates, verified_on = load_pricing(args.pricing)

    body = render(inventory, spotcheck, rates, verified_on)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"PR body → {args.out}")
    else:
        print(body)


if __name__ == "__main__":
    main()
