# Call-site detection

`inventory.py` matches these patterns mechanically. This file is the reference for what it
looks for and — more importantly — **what it will miss**, so you know when to read code by
hand instead of trusting the scan.

## Direct SDK

| Language | Pattern | Notes |
|---|---|---|
| Python | `OpenAI(...)`, `AsyncOpenAI(...)`, `client.chat.completions.create`, `client.responses.create`, `client.embeddings.create` | Model is usually a literal or a settings attribute |
| Python | `AzureOpenAI(...)`, `azure_endpoint=` | Deployment name ≠ model name — read the deployment config |
| TS/JS | `new OpenAI(`, `openai.chat.completions.create`, `openai.responses.create` | |
| Go | `openai.NewClient(`, `CreateChatCompletion` | |

## Non-chat SDK surfaces

Chat completions are not the only call sites. Each of these has its own port story — see
`references/model-mapping.md` — and each still gets its own PR.

| Surface | Pattern | Port story |
|---|---|---|
| Embeddings | `.embeddings.create` | Third-party redirect (Voyage) |
| Moderation | `.moderations.create` | Full swap to a prompted classifier, usually recommend against |
| Audio | `.audio.transcriptions.*`, `.audio.speech.*` | Partial port — speech-to-text does not move |
| Images | `.images.generate`, `.images.edit` | No Claude equivalent; flag and leave |
| Batch / fine-tune | `.batches.create`, `.fine_tuning.jobs.create` | Not a like-for-like swap; flag |

## Raw HTTP — no SDK to key off

The hardest case and the easiest to miss entirely: the app calls the REST API with `fetch`,
`requests`, `httpx`, `curl`, or a generated client. There is no import to match on, so the
scan looks for two different things, and **it needs both**:

| Signal | Example | Recorded as |
|---|---|---|
| Provider host | `https://api.openai.com/v1`, `*.openai.azure.com` | `client_construction` — the diff lands here |
| Endpoint path literal | `"/chat/completions"`, `"/v1/embeddings"`, `"/audio/transcriptions"` | `invocation` — reason about these |

Matching only the host finds the house transport wrapper and **none of its callers**, because
callers pass a relative path. Matching only the path misses which provider it is. A repo
with one `openai.js` holding the base URL and six modules calling `post("/chat/completions")`
has one edit point and six call sites; report it that way.

## Other providers

| Provider | Pattern |
|---|---|
| Google Gemini | `generativeai`, `GenerativeModel(`, `genai.Client(`, `ChatGoogleGenerativeAI(`, `generativelanguage.googleapis.com` |
| Mistral | `MistralClient(`, `ChatMistralAI(`, `api.mistral.ai` |
| Cohere | `cohere.Client(`, `cohere.ClientV2(`, `ChatCohere(` |
| Ollama | `ollama.chat(`, `ChatOllama(` |
| OpenAI-compatible gateways | `base_url=` / `baseURL:` overrides pointing at Groq, Together, OpenRouter, Fireworks, DeepSeek |

A `base_url` override is worth stopping on. The SDK says OpenAI; the traffic may go somewhere
else entirely, and the incumbent you benchmark against must be the one actually serving the
requests.

## Framework wrappers

| Framework | Pattern | Prompt lives in |
|---|---|---|
| pydantic-ai | `Agent(`, `OpenAIChatModel(`, `OpenAIProvider(` | `system_prompt=` kwarg, usually a module constant |
| LangChain (py) | `ChatOpenAI(`, `ChatPromptTemplate` | Template objects, often assembled at call time |
| LangChain (js) | `new ChatOpenAI(` | |
| LlamaIndex | `OpenAI(model=`, `Settings.llm` | Global settings — one edit can cover many call sites |
| Vercel AI SDK | `openai(`, `generateText(`, `streamText(`, `generateObject(` | `system:` property |
| instructor | `instructor.from_openai(`, `response_model=` | Pydantic model is the schema |
| LiteLLM | `litellm.completion(`, `model="openai/..."` | Model string is provider-prefixed |
| Semantic Kernel | `OpenAIChatCompletion(` | Prompts often in separate `.prompt` files |

## Structured output

Worth flagging separately — these call sites are where migration risk actually lives,
because a schema failure breaks something downstream rather than reading slightly worse.

- pydantic-ai `output_type=` / `result_type=`
- OpenAI `response_format=`, `text_format=`, `tools=` + `tool_choice`
- instructor `response_model=`
- Vercel AI SDK `generateObject({ schema })`
- Raw JSON-mode prompts — grep for `"json_object"` and for prompts containing "respond with
  JSON", which are the hand-rolled version and the most fragile

## Supporting call patterns nobody anticipated

The pattern list above will never be complete — every repo is free to invent a new way to
call an API, and a scan that only reports what it recognises will confidently under-report.
So `inventory.py` also runs a **weak-signal sweep** across code *and* config, recording
evidence that a provider is in use without claiming it is a call site:

| Signal | Why it matters |
|---|---|
| `*_API_KEY` env names | A key with no call site means the scan missed the call |
| Provider hostnames | Traffic goes somewhere; find what sends it |
| `base_url` / `baseURL` / `api_base` | The provider may not be the one the SDK name implies |
| Model-id literals | Config files (`wrangler.toml` `[vars]`, k8s manifests, terraform) hold the deployed model even when no code mentions it |
| `Authorization: Bearer` | A hand-rolled request |

These land in `weak_signals` in the inventory JSON. **Reconcile them before reporting.** The
sweep excludes lines already recorded as call sites, so anything left is unexplained by
definition. Three rules:

1. **A file with weak signals and no call site in it must be opened.** It is either config
   the migration has to touch (a model id, a price table, a test asserting a model string) or
   a call pattern the scan does not know.
2. **Provider keys plus zero call sites is a contradiction, not an empty result.** Say so and
   investigate; never report "no call sites found" while an API key sits in the env.
3. **A provider SDK in the dependency manifest implies call sites.** If `openai` is in
   `requirements.txt` / `package.json` and the scan found nothing, the scan is wrong.

When you do find an unrecognised pattern, note it in the run so the pattern list can grow —
that is how coverage improves rather than being assumed.

## What the scan still misses

Read these by hand:

- **Prompts assembled at runtime** from f-strings, template files, or a database. The script
  resolves module-level constants only. If a call site shows an unresolved prompt, open it.
- **Model names from env or config tables** — the script records the expression, not the
  runtime value. Check what is actually deployed.
- **Indirection through a house wrapper** (`our_llm_client.py`). Find the wrapper, then find
  its callers; the call *sites* are the callers, and the migration edit is the wrapper.
- **Prompts in non-code files** — `.prompt`, `.jinja`, `.md`, YAML config.
- **Fine-tuned models.** A call site on a fine-tune is not a like-for-like swap. Flag it and
  recommend against migrating that site unless the user wants to discuss re-tuning.
- **Anything behind a feature flag** that decides model at runtime.
- **Generated or vendored clients** — an OpenAPI-generated SDK checked into the repo has
  method names that match nothing here. The weak-signal sweep will still catch its base URL.
- **Calls made from another service entirely** — a queue worker or a separate repo. The scan
  covers the tree it is given and nothing beyond it; say so rather than implying whole-system
  coverage.
