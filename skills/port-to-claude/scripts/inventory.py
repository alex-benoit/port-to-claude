#!/usr/bin/env python3
"""Inventory the LLM call sites in a repo.

Mechanical work only: find the call sites, resolve module-level prompt constants to the
sites that use them, record declared output schemas. Judgment about what each call site
*is* — task shape, latency sensitivity, failure cost — belongs to the agent reading this
output, not here.

Stdlib only. The skill must run without installing anything.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", ".nuxt", "target", "vendor", ".mypy_cache", ".pytest_cache", ".tox",
    "site-packages", ".terraform", "coverage", ".ruff_cache",
}
CODE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".rb",
    ".java", ".kt", ".scala", ".cs", ".php", ".rs", ".swift", ".ex", ".exs", ".sh", ".vue",
    ".svelte",
}
# Model ids and provider hosts frequently live in config rather than code (wrangler.toml
# [vars], k8s manifests, terraform). Scanned for signals only — no call sites are claimed
# from these, because a config file does not call anything.
CONFIG_SUFFIXES = {".toml", ".yaml", ".yml", ".ini", ".cfg", ".tf", ".tfvars", ".properties"}
MAX_BYTES = 2_000_000

# An OpenAI-shaped endpoint path. Deliberately not anchored to a quote: these arrive as
# template literals (`${BASE}/v1/embeddings`), concatenations, and route constants.
ENDPOINT_PATH = re.compile(
    r"(?:/v1)?/(?:chat/completions|completions|responses|embeddings|moderations"
    r"|audio/(?:transcriptions|translations|speech)"
    r"|images/(?:generations|edits|variations))\b"
)

# (framework, regex). Order matters only for reporting; every match is recorded.
PATTERNS = [
    ("pydantic-ai", r"\bOpenAIChatModel\s*\(|\bOpenAIModel\s*\(|\bOpenAIProvider\s*\("),
    ("pydantic-ai", r"\bAgent\s*\("),
    ("openai-sdk", r"\b(?:Async)?OpenAI\s*\(|\bAzureOpenAI\s*\("),
    ("openai-sdk", r"\.chat\.completions\.create\b|\.responses\.create\b|\.completions\.create\b"),
    ("openai-embeddings", r"\.embeddings\.create\b"),
    ("langchain", r"\bChatOpenAI\s*\(|\bAzureChatOpenAI\s*\("),
    ("llamaindex", r"\bSettings\.llm\b|\bllama_index\b.*OpenAI\s*\("),
    ("vercel-ai-sdk", r"\bgenerateText\s*\(|\bstreamText\s*\(|\bgenerateObject\s*\(|\bstreamObject\s*\("),
    ("instructor", r"\binstructor\.from_openai\s*\(|\bfrom_openai\s*\("),
    ("litellm", r"\blitellm\.(?:a)?completion\s*\("),
    ("semantic-kernel", r"\bOpenAIChatCompletion\s*\("),
    ("go-openai", r"\bopenai\.NewClient\s*\(|\bCreateChatCompletion\b"),
    # SDK surfaces beyond chat: each is a real call site with its own port story.
    ("openai-moderation", r"\.moderations\.create\b"),
    ("openai-audio", r"\.audio\.transcriptions\.\w+|\.audio\.translations\.\w+|\.audio\.speech\.\w+"),
    ("openai-images", r"\.images\.(?:generate|edit|create_variation)\b"),
    ("openai-batch", r"\.batches\.create\b|\.fine_tuning\.jobs\.create\b"),
    # Raw HTTP: no SDK import to key off. The host names the provider; the endpoint path
    # names the call site. Both are needed — a house wrapper holds the host, and its
    # callers pass only the relative path.
    ("raw-http", r"api\.openai\.com|openai\.azure\.com|generativelanguage\.googleapis\.com"
                 r"|api\.mistral\.ai|api\.cohere\.(?:ai|com)|api\.deepseek\.com"),
    ("raw-http", ENDPOINT_PATH.pattern),
    # Other providers named in the skill description.
    ("google-genai", r"\bgenerativeai\b|\bGenerativeModel\s*\(|\bgenai\.Client\s*\(|\bChatGoogleGenerativeAI\s*\("),
    ("mistral", r"\bMistral(?:Client|AsyncClient)?\s*\(|\bChatMistralAI\s*\("),
    ("cohere", r"\bcohere\.(?:Client|ClientV2)\s*\(|\bChatCohere\s*\("),
    ("ollama", r"\bollama\.(?:chat|generate)\s*\(|\bChatOllama\s*\("),
]
COMPILED = [(name, re.compile(rx)) for name, rx in PATTERNS]

SCHEMA_KWARGS = ("output_type", "result_type", "response_model", "response_format", "text_format")

# Construction of a provider/client object is where the migration diff lands; the
# invocation is what you reason about. Same scan, different jobs — keep both, labeled.
CONSTRUCTION = re.compile(
    r"\b(?:Async)?OpenAI\s*\(|\bAzureOpenAI\s*\(|\bOpenAIChatModel\s*\(|\bOpenAIModel\s*\("
    r"|\bOpenAIProvider\s*\(|\bChatOpenAI\s*\(|\bAzureChatOpenAI\s*\(|\bOpenAIChatCompletion\s*\("
    r"|\bopenai\.NewClient\s*\(|\binstructor\.from_openai\s*\("
    r"|api\.openai\.com|openai\.azure\.com|generativelanguage\.googleapis\.com"
    r"|api\.mistral\.ai|api\.cohere\.(?:ai|com)|api\.deepseek\.com"
    r"|\bMistral(?:Client|AsyncClient)?\s*\(|\bcohere\.(?:Client|ClientV2)\s*\("
    r"|\bGenerativeModel\s*\(|\bgenai\.Client\s*\("
)


def classify(func_src):
    """Construction is where the diff lands; invocation is what you reason about.

    An endpoint path wins over a provider host: a line may carry both (a direct
    `post("https://api.openai.com/v1/chat/completions")`), and that is a call site, not
    a client being built."""
    if ENDPOINT_PATH.search(func_src):
        return "invocation"
    return "client_construction" if CONSTRUCTION.search(func_src + "(") else "invocation"
MODEL_HINT = re.compile(
    r"""["'`](gpt-[\w.\-]+|o\d[\w.\-]*|chatgpt-[\w.\-]+|text-embedding-[\w.\-]+"""
    r"""|whisper-[\w.\-]+|tts-[\w.\-]+|dall-e-[\w.\-]+|omni-moderation-[\w.\-]+"""
    r"""|text-moderation-[\w.\-]+|claude-[\w.\-]+|gemini-[\w.\-]+|mistral-[\w.\-]+"""
    r"""|voyage-[\w.\-]+|command-[\w.\-]+|llama[\w.\-]*)["'`]""",
    re.IGNORECASE,
)


def estimate_tokens(text):
    """Rough characters/4 heuristic. Anything derived from this must be labeled estimated."""
    return max(1, round(len(text) / 4))


def iter_files(root, suffixes=None):
    suffixes = CODE_SUFFIXES if suffixes is None else suffixes
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield path


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def python_constants(tree):
    """Module-level `NAME = "..."` string constants, so prompts can be resolved by name."""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def kwarg_source(call, name, src):
    for kw in call.keywords:
        if kw.arg == name:
            return ast.get_source_segment(src, kw.value)
    return None


def analyze_python(path, src, rel):
    """AST pass: resolve prompts and schemas that a regex cannot see."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    consts = python_constants(tree)
    sites = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_src = ast.get_source_segment(src, node.func) or ""
        framework = next(
            (name for name, rx in COMPILED if rx.search(func_src + "(")), None
        )
        if framework is None:
            continue

        prompt_expr = kwarg_source(node, "system_prompt", src) or kwarg_source(node, "system", src)
        prompt_text, prompt_source = None, None
        if prompt_expr:
            bare = prompt_expr.strip()
            if bare in consts:
                prompt_text, prompt_source = consts[bare], f"module constant {bare}"
            else:
                for kw in node.keywords:
                    if kw.arg in ("system_prompt", "system") and isinstance(kw.value, ast.Constant):
                        if isinstance(kw.value.value, str):
                            prompt_text, prompt_source = kw.value.value, "inline literal"

        schema = None
        for kwname in SCHEMA_KWARGS:
            value = kwarg_source(node, kwname, src)
            if value:
                schema = f"{kwname}={value}"
                break

        model_expr = kwarg_source(node, "model", src)
        if model_expr is None and node.args:
            first = ast.get_source_segment(src, node.args[0])
            if first and ("model" in first.lower() or MODEL_HINT.search(first)):
                model_expr = first

        site = {
            "file": rel,
            "line": node.lineno,
            "framework": framework,
            "role": classify(func_src),
            "call": func_src[:120],
            "model_expr": model_expr,
            "output_schema": schema,
            "structured_output": schema is not None,
            "prompt_source": prompt_source,
            "prompt_chars": len(prompt_text) if prompt_text else None,
            "prompt_tokens_estimated": estimate_tokens(prompt_text) if prompt_text else None,
            "prompt_preview": (prompt_text[:400] + "…") if prompt_text and len(prompt_text) > 400 else prompt_text,
            # Full text, not the preview: the spot-check must send the real prompt or the
            # comparison is measuring something the app never runs.
            "prompt_full": prompt_text,
            "prompt_resolved": prompt_text is not None,
        }
        if prompt_expr and prompt_text is None:
            site["prompt_unresolved_expr"] = prompt_expr[:120]
        sites.append(site)

    return sites


# `import type {...} from "openai/resources/chat/completions"` contains an endpoint-shaped
# path but calls nothing. Module specifiers are never call sites in any language.
# Must match module specifiers only. `export const openai = new OpenAI({` is a real
# construction site, so a bare leading `export` is not enough to skip a line.
IMPORT_LINE = re.compile(
    r"^\s*import\b"                              # ES/py import, incl. `import type {...}`
    r"|^\s*export\s+(?:type\s+)?[*{][^=]*\bfrom\b"  # re-export, never an assignment
    r"|^\s*from\s+\S+\s+import\b"                # python
    r"|\brequire\s*\(|^\s*use\s+\S+;|^\s*#include\b"
)


def analyze_generic(path, src, rel):
    """Line-regex pass. Coarser by design; the agent reads these."""
    sites = []
    lines = src.splitlines()
    for i, line in enumerate(lines, start=1):
        if IMPORT_LINE.search(line):
            continue
        for framework, rx in COMPILED:
            if not rx.search(line):
                continue
            model_match = MODEL_HINT.search(line)
            sites.append({
                "file": rel,
                "line": i,
                "framework": framework,
                "role": classify(line),
                "call": line.strip()[:120],
                "model_expr": model_match.group(1) if model_match else None,
                "output_schema": None,
                "structured_output": bool(re.search(r"\bschema\b|generateObject|response_format", line)),
                "prompt_source": None,
                "prompt_resolved": False,
            })
            break
    return sites


# Weak signals: evidence a provider is in use that is not, on its own, a call site.
# The strong patterns above cannot cover every way an API gets called — a custom
# transport, a gateway URL in an env var, a generated client. These give the agent a
# thread to pull when the structured scan comes back thin.
WEAK_SIGNALS = [
    ("api-key-env", re.compile(
        r"\b(?:OPENAI|AZURE_OPENAI|GEMINI|GOOGLE_API|MISTRAL|COHERE|DEEPSEEK|GROQ|"
        r"TOGETHER|ANYSCALE|FIREWORKS|OPENROUTER)_API_KEY\b")),
    ("provider-host", re.compile(
        r"api\.openai\.com|openai\.azure\.com|generativelanguage\.googleapis\.com"
        r"|api\.mistral\.ai|api\.cohere\.(?:ai|com)|api\.groq\.com|openrouter\.ai"
        r"|api\.together\.xyz|api\.deepseek\.com")),
    ("base-url-override", re.compile(
        r"\b(?:base_url|baseURL|OPENAI_BASE_URL|OPENAI_API_BASE|api_base)\b")),
    ("model-id-literal", MODEL_HINT),
    ("bearer-auth", re.compile(r"Authorization[\"\':\s]+Bearer")),
]


def sweep_weak_signals(root, claimed):
    """Provider evidence outside the recorded call sites.

    `claimed` is the set of (file, line) already reported as call sites, so this only
    surfaces what the structured patterns did not already explain."""
    hits = []
    suffixes = CODE_SUFFIXES | CONFIG_SUFFIXES | {".json", ".env", ".txt", ".md", ""}
    for path in iter_files(root, suffixes):
        if path.name.startswith(".env") or path.suffix in suffixes:
            src = read(path)
            if src is None:
                continue
            rel = str(path.relative_to(root))
            for i, line in enumerate(src.splitlines(), start=1):
                if (rel, i) in claimed or len(line) > 400:
                    continue
                for kind, rx in WEAK_SIGNALS:
                    if rx.search(line):
                        hits.append({
                            "file": rel, "line": i, "signal": kind,
                            "text": line.strip()[:160],
                        })
                        break
        if len(hits) >= 400:
            hits.append({"file": "…", "line": 0, "signal": "truncated",
                         "text": "sweep capped at 400 hits; narrow the scan by hand"})
            break
    return hits


def find_env_models(root):
    """Model names sitting in config/env files — often the real deployed value."""
    found = {}
    for name in (".env", ".env.example", ".env.local", ".env.sample"):
        for path in root.rglob(name):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            text = read(path)
            if not text:
                continue
            for line in text.splitlines():
                if re.match(r"^\s*[A-Z_]*MODEL[A-Z_]*\s*=", line):
                    key, _, value = line.partition("=")
                    found[key.strip()] = value.strip().strip("\"'")
    return found


def main():
    ap = argparse.ArgumentParser(description="Inventory LLM call sites in a repo.")
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path, help="write JSON here (default: stdout)")
    args = ap.parse_args()

    root = args.repo.resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    sites, scanned = [], 0
    for path in iter_files(root):
        src = read(path)
        if src is None:
            continue
        scanned += 1
        rel = str(path.relative_to(root))
        found = analyze_python(path, src, rel) if path.suffix == ".py" else []
        seen = {(s["file"], s["line"]) for s in found}
        # The line scan catches what an AST callee match cannot: raw HTTP, URLs and
        # endpoint paths passed as arguments, calls through house wrappers.
        found += [s for s in analyze_generic(path, src, rel)
                  if (s["file"], s["line"]) not in seen]
        sites.extend(found)

    deduped, seen_rows = [], set()
    for s in sites:
        key = (s["file"], s["line"], s["framework"], s["role"])
        if key not in seen_rows:
            seen_rows.add(key)
            deduped.append(s)
    sites = deduped

    claimed = {(s["file"], s["line"]) for s in sites}
    weak = sweep_weak_signals(root, claimed)

    frameworks = sorted({s["framework"] for s in sites})
    report = {
        "repo": str(root),
        "files_scanned": scanned,
        "call_sites": sites,
        "weak_signals": weak,
        "summary": {
            "total": len(sites),
            "frameworks": frameworks,
            "invocations": sum(1 for s in sites if s["role"] == "invocation"),
            "edit_points": sum(1 for s in sites if s["role"] == "client_construction"),
            "structured_output": sum(1 for s in sites if s["structured_output"]),
            "prompts_resolved": sum(1 for s in sites if s.get("prompt_resolved")),
            "embeddings": sum(1 for s in sites if s["framework"] == "openai-embeddings"),
            "weak_signal_files": len({h["file"] for h in weak}),
            "model_env_vars": find_env_models(root),
        },
        "caveats": [
            "Token counts here are a characters/4 heuristic. Label anything derived from "
            "them as estimated.",
            "Unresolved prompts are built at runtime — read those call sites by hand.",
            "model_expr is the source expression, not the deployed value. Compare it "
            "against summary.model_env_vars — a mismatch means the code default is not "
            "what runs in production.",
            "role=client_construction rows are where the diff lands; role=invocation rows "
            "are what you reason about.",
            "Embedding call sites have no Anthropic equivalent, but they are not a dead "
            "end — see references/model-mapping.md for the Voyage redirect.",
            "weak_signals is provider evidence that is not itself a call site. If it names "
            "files with no call site in them, a call pattern was missed — read those files "
            "before concluding the inventory is complete.",
            "A repo with provider API keys and zero call sites is a contradiction, not an "
            "empty result. Investigate before reporting nothing found.",
        ],
    }

    payload = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        inv = report["summary"]["invocations"]
        edits = report["summary"]["edit_points"]
        extra = f", {len(weak)} weak signals in {report['summary']['weak_signal_files']} files" if weak else ""
        print(f"{inv} invocations, {edits} edit points across {len(frameworks)} frameworks{extra} → {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
