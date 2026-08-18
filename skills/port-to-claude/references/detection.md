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

## What the scan misses

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
