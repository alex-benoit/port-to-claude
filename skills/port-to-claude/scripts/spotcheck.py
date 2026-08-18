#!/usr/bin/env python3
"""Run a bounded side-by-side spot-check across providers.

This is a confidence check, not an eval suite: a handful of samples per call site, on the
developer's own keys, with real token counts and latency. Anything that looks like a
statistical claim must be labeled with the sample size that produced it.

Stdlib only. Keys are read from the environment and sent to their own provider, nowhere
else, and never written to the output.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 120


def post(url, headers, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={**headers, "content-type": "application/json"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return None, f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def call_openai(model, system, user, key, max_tokens):
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }
    started = time.monotonic()
    data, err = post(OPENAI_URL, {"authorization": f"Bearer {key}"}, payload)
    elapsed = time.monotonic() - started
    if err:
        return {"error": err, "latency_s": round(elapsed, 3)}
    usage = data.get("usage") or {}
    choices = data.get("choices") or [{}]
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return {
        "text": (choices[0].get("message") or {}).get("content", ""),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "cached_input_tokens": cached,
        "latency_s": round(elapsed, 3),
        "finish_reason": choices[0].get("finish_reason"),
    }


def call_anthropic(model, system, user, key, max_tokens, thinking_budget=None):
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if thinking_budget:
        payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    started = time.monotonic()
    data, err = post(ANTHROPIC_URL, headers, payload)
    elapsed = time.monotonic() - started
    if err:
        return {"error": err, "latency_s": round(elapsed, 3)}
    usage = data.get("usage") or {}
    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )
    return {
        "text": text,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        "latency_s": round(elapsed, 3),
        "stop_reason": data.get("stop_reason"),
    }


def count_anthropic_tokens(model, system, user, key):
    """Exact input-token count without running inference."""
    payload = {"model": model, "system": system, "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    data, err = post(ANTHROPIC_COUNT_URL, headers, payload)
    if err:
        return None
    return data.get("input_tokens")


def validate_json(text, required_keys):
    """Schema adherence is the metric that matters for structured call sites."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1] if "```" in stripped[3:] else stripped[3:]
        stripped = stripped.removeprefix("json").strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return False, f"not valid JSON: {exc}"
    if required_keys:
        missing = [k for k in required_keys if k not in parsed]
        if missing:
            return False, f"missing keys: {', '.join(missing)}"
    return True, None


def main():
    ap = argparse.ArgumentParser(description="Bounded side-by-side provider spot-check.")
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--samples", type=int, default=1, help="runs per case per provider")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--dry-run", action="store_true", help="plan only, no API calls")
    args = ap.parse_args()

    spec = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = spec.get("cases", [])
    keys = {"openai": os.environ.get("OPENAI_API_KEY"), "anthropic": os.environ.get("ANTHROPIC_API_KEY")}

    planned = sum(len(c.get("providers", [])) for c in cases) * args.samples
    if args.dry_run:
        print(f"dry run: {len(cases)} cases, {planned} calls would be made")
        for case in cases:
            for prov in case.get("providers", []):
                print(f"  {case['id']:<28} {prov['vendor']:<10} {prov['model']}")
        return

    missing = sorted({p["vendor"] for c in cases for p in c.get("providers", [])} - {v for v, k in keys.items() if k})
    if missing:
        sys.exit(
            f"missing API key(s) for: {', '.join(missing)}. "
            "Set OPENAI_API_KEY / ANTHROPIC_API_KEY. An Anthropic key is required for "
            "measured Claude numbers — a Claude Code subscription does not cover "
            "programmatic inference. Get one at https://console.anthropic.com"
        )

    print(f"running {planned} calls across {len(cases)} cases…")
    results = []
    for case in cases:
        for prov in case.get("providers", []):
            vendor, model = prov["vendor"], prov["model"]
            for sample in range(args.samples):
                if vendor == "openai":
                    out = call_openai(model, case["system"], case["input"], keys["openai"], args.max_tokens)
                elif vendor == "anthropic":
                    out = call_anthropic(
                        model, case["system"], case["input"], keys["anthropic"],
                        args.max_tokens, prov.get("thinking_budget"),
                    )
                else:
                    out = {"error": f"unknown vendor: {vendor}"}

                if case.get("expect_json") and "text" in out:
                    ok, reason = validate_json(out["text"], case.get("required_keys"))
                    out["schema_ok"] = ok
                    out["schema_error"] = reason

                results.append({
                    "case": case["id"],
                    # Carried through so report.py --site can attribute rows to a call site.
                    "source": case.get("source", ""),
                    "vendor": vendor, "model": model,
                    "sample": sample, **out,
                })
                status = "ERROR" if "error" in out else f"{out.get('latency_s')}s"
                print(f"  {case['id']:<28} {vendor:<10} {model:<28} {status}")

    payload = {
        "samples_per_case": args.samples,
        "cases": len(cases),
        "results": results,
        "caveat": (
            f"Spot-check with {args.samples} sample(s) per case. Indicative of behaviour, "
            "not statistical evidence of quality parity."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    errors = sum(1 for r in results if "error" in r)
    print(f"\n{len(results)} results ({errors} errors) → {args.out}")


if __name__ == "__main__":
    main()
