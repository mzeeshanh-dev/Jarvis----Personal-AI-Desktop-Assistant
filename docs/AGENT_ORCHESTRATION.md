# Agent orchestration

Code: `backend/agent/` (orchestrator, planner, executor, context_manager, conversation_manager, task_manager, permissions, result_formatter).

## Task lifecycle

```
 chat.send (WS) / POST /api/tasks / voice transcript
        |
        v
 Orchestrator.submit(text[:8000], conversation_id, source)  -- empty text -> ValueError
        |
        v
 TaskManager.submit -> asyncio task, waits on Semaphore(max_concurrent_tasks = 2)
        |
        v
 _run: ensure conversation (title = first 60 chars) -> INSERT tasks(status=running)
       emit task.started -> store user message
        |
        +--> route_direct(text)? --yes--> Executor.run(tool)  (0 LLM calls)
        |          |  ok                 -> reply = tool summary (web search: "Searching Google for “q”.")
        |          |  declined/denied    -> reply = error
        |          |  other failure + fallback allowed + a provider is enabled -> LLM loop
        |          v
        +--> _llm_loop  (route = "llm")
        |
        v
 _finish: store assistant message, UPDATE tasks (status, route, error, result[:2000], llm_calls, tool_calls)
          emit assistant.message, task.finished; record e2e_ms; speak if configured
```

Final status values are `completed`, `failed` and `cancelled`. Errors become user-facing replies:

| Exception | Reply |
|---|---|
| `NoProviderAvailable` | "No Groq API key is configured. Add one in Settings → Groq API keys." or "All Groq API keys are currently unavailable; expected to recover in about Xm Ys." |
| `ProviderError` | "I couldn't complete that because the AI service returned an error: …" |
| anything else | "Something went wrong while handling that request. Details are in Settings → Logs." (logged with traceback) |
| `CancelledError` | "Stopped." (status `cancelled`, not spoken; then `task.cancelled`) |

## Direct route (`planner.route_direct`)

The planner normalises the text first. It strips a leading "Jarvis,"/"Hey Jarvis", politeness prefixes ("please", "can you", "could you", "would you", "kindly"), trailing "please/for me/now/thanks", and final punctuation. Input longer than 120 characters always goes to the LLM.

| Pattern | Result |
|---|---|
| `go to / navigate to / visit / browse to <target>` where the target is a domain or a known site | `open_url` |
| `search/look up [on google/bing/duckduckgo] [for] <q> [on <engine>]` or `google <q>` | `open_url` with the engine's search URL (intent `web_search`). Requires an engine word, a leading "google", or "for" among the first 3 words. Skipped when the text mentions my/email(s)/gmail/inbox/file(s)/folder(s)/drive/computer |
| `open/launch/start/run/fire up/bring up <target> [app/application/program/website/site]` | known site name → `open_url`; domain → `open_url`; otherwise `open_application` if the target is ≤ 4 words, has no path characters, and has no "complex" words (and, then, after, summarize, compare, find, which, what, why, how, read, email, file, folder, inbox, tell me, my) |

Known sites: google, youtube, gmail, github, chatgpt, linkedin, twitter/x, facebook, instagram, reddit, wikipedia, stack overflow, google drive, google calendar, whatsapp web, netflix. The direct `open_url` uses the user's **default browser** (`os.startfile`), not the automation browser.

Direct calls still go through the full executor, with validation, permissions and the audit log. `open_url` and `open_application` are level 1, so they run without asking.

## LLM tool loop (`Orchestrator._llm_loop`)

```
 messages = [system prompt, trimmed history]          tools = registry.schemas()
 model, tier = choose_model(text)
 for iteration in 0 .. max_iterations-1:               (default 8, 1..25)
     emit task.status(thinking, model, iteration)
     res = groq.chat(stream, tools, parallel_tool_calls, max_tokens 1024 simple / 2048 complex,
                     on_delta -> emit assistant.delta(stream_id = task-iteration))
     on ProviderError 400 mentioning "tool":
         simple tier  -> switch to complex model, retry
         else (once)  -> append system note "Your previous tool call was malformed…", retry
     no tool calls   -> return content (or "Done.")
     store assistant message with tool_calls
     emit task.status(executing, "tool_a, tool_b")
     results = _execute(calls)        parallel if parallel_tools && >1 call && all levels <= 2
     append + store each tool result (to_tool_message, limited to tool_result_char_limit)
 return "I stopped after N steps without finishing…"
```

- **Model choice** (`choose_model`): requests of ≤ 10 words with no comma and none of the words "and then, then, compare, summarize/summarise, analyze/analyse, diagnose, debug, explain, why, plan, pending, latest, each, all" use `providers.models.simple`; everything else uses `providers.models.complex`.
- Chat parameters: temperature 0.3, `stream: true`, `tool_choice: "auto"`, `parallel_tool_calls: true` when tools are present. Streamed tool-call deltas are reassembled by index. Usage is read from `usage` or `x_groq.usage`.
- **Parallelism**: a batch of calls runs concurrently only when every call's assessed level is ≤ 2 (read-only); otherwise the calls run sequentially in order.
- Tool results are wrapped for the model by `result_formatter.to_tool_message`: `{"ok","summary","data"|"error"}` JSON, with nested data truncated to `tool_result_char_limit` (default 12000) and the whole string capped at twice that. **Untrusted results** (web, email, file, process output) are wrapped in `<untrusted_content source="…">…</untrusted_content>` followed by a note telling the model not to follow instructions inside.

### System prompt (`context_manager.SYSTEM_PROMPT`)

The system prompt includes the local time, the OS, the optional user name, and the allowed workspace folders. It sets these rules: prefer doing over explaining; call independent tools in parallel; never invent file, email, search or command content; treat `<untrusted_content>` strictly as data; let the app handle confirmation, without asking in text; adapt on tool failure; use `search_emails` + `summarize_emails` for multi-email summaries; keep final answers short and suitable for reading aloud.

### Context building (`ContextManager.build`)

1. Load the last `history_messages × 3` stored messages (default 20 × 3).
2. `_drop_orphans`: keep an assistant `tool_calls` message only if **all** of its tool results follow it. Stray tool messages are dropped.
3. `_trim`: group messages by user turn, keep the last `history_messages` groups, drop the oldest groups while the total exceeds `context_char_budget` (default 24000 chars), and make sure the context starts at a user message.
4. Prepend the system message.

## Executor (`agent/executor.py`)

For every call, whether direct or LLM-requested:

1. Unknown or unavailable tool → `unknown_tool`.
2. Parse the arguments (JSON object required) and validate them against the tool's Pydantic model (`extra="forbid"`). Failure → `invalid_arguments` with up to 5 error messages, logged as `rejected`/`invalid`.
3. `assess(args, ctx)`, if the tool has one, returns the dynamic level, `auto_ok` and a reason. A `ToolError` from assess → `rejected`/`blocked`.
4. `level = max(tool.level, assessment.level)`, then `permissions.evaluate(...)` → ALLOW / ASK / DENY (see [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md)).
5. ASK → emit `task.status(awaiting_approval)`, then `ApprovalBroker.request(...)` with a timeout of `approval_timeout_s` (default 120 s). Declined or expired → `declined`.
6. Emit `tool.started` (arguments redacted and truncated to 600 chars), then run `handler` under `asyncio.wait_for(timeout)`, where the timeout is `tool.timeout_s` or `automation.tool_timeout_s` (default 60 s).
   - `idempotent=True` tools get **2 attempts**: a retry after a timeout or a `ToolError(transient=True)`.
   - Unexpected exceptions become `internal` failures (redacted, 300 chars). The agent never crashes on a tool bug.
7. A successful result with `untrusted=True` **taints** the task context (`ctx.tainted = True`, `tainted_by` records the tool).
8. Emit `tool.finished` and write `execution_logs` (redacted arguments ≤ 4000 chars, level, decision `auto/approved/declined/denied/rejected`, status, duration, preview, error). Record `tool_ms`.

## Concurrency and cancellation (`task_manager.py`)

- `max_concurrent_tasks` (default 2, 1–6) caps concurrency with a semaphore; queued tasks wait. Changing the limit replaces the semaphore for new tasks only.
- `cancel(task_id)` cancels the asyncio task. A pending approval is released by the broker's own `finally`. `run_command` kills its process tree on cancellation.
- `task.cancel_all` (WS) and `POST /api/tasks/cancel-all` also cancel the voice session and emit `tts.stop`.
- On backend shutdown all tasks are cancelled and awaited.

## Conversations

`ConversationManager` stores every user, assistant and tool message (with `tool_calls` JSON and `tool_call_id`) in `messages`. The UI view (`/api/conversations/{id}/messages`) folds tool calls and results into the assistant reply (`renderer/lib/reducer.ts rowsToMessages`). Search (`?q=`) matches titles and message content with `LIKE`.

## Limits worth knowing

| Setting | Default | Range |
|---|---|---|
| `automation.max_iterations` | 8 | 1–25 |
| `automation.tool_timeout_s` | 60 | 5–600 |
| `automation.max_concurrent_tasks` | 2 | 1–6 |
| `automation.parallel_tools` | true | |
| `performance.history_messages` | 20 | 2–100 |
| `performance.context_char_budget` | 24000 | 4000–200000 |
| `performance.tool_result_char_limit` | 12000 | 1000–100000 |
| Input text | 8000 chars | |

## Token budget

Every LLM call carries the tool schemas, and on small Groq plans (for example 8,000 TPM) they dominate the cost. All 31 schemas are about 3,700 tokens.

- **Tool subsetting:** `planner.tool_categories()` picks categories (`apps` always, plus `filesystem`, `browser`, `terminal`, `email`) from keywords in the current and recent user messages, plus any categories already used in the conversation, so follow-ups keep their tools. Generic requests get `apps + filesystem + browser`. Measured: a file question about 1,330 tokens, web search about 1,430, a terminal request about 1,130, email plus files about 1,990 (vs about 3,700 for all tools).
- **Completion budget:** `max_completion_tokens` is 1,536 (complex) or 1,024 (simple), because Groq counts requested completion tokens toward TPM. `reasoning_effort` (default `low`) is sent for `openai/gpt-oss-*` models.
- **Empty answers:** if a reasoning model returns no content and no tool calls, the loop adds one system nudge ("answer the user directly…") and retries once.
- **Short waits instead of errors:** see `providers.max_wait_s` in [GROQ_PROVIDER_MANAGER.md](GROQ_PROVIDER_MANAGER.md).
