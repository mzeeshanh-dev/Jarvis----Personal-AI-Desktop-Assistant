# Database schema

SQLite file: `%APPDATA%\Jarvis\jarvis.db`. Code: `backend/storage/database.py`, `backend/storage/migrations.py`.

## Access model

- A single `sqlite3` connection opened with `check_same_thread=False` and autocommit (`isolation_level=None`), used only from **one dedicated worker thread** (`ThreadPoolExecutor(max_workers=1)`). Every call (`execute`, `fetchall`, `fetchone`, `transaction`) is awaited through that executor, so the asyncio loop never blocks and queries are serialised.
- PRAGMAs: `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`.
- `transaction(fn)` runs `fn(conn)` inside `BEGIN … COMMIT` (rolled back on any exception).
- JSON columns are written with `dumps()` (`ensure_ascii=False`, `default=str`) and read with `loads()`, which tolerates invalid JSON.
- Timestamps are Unix seconds (`REAL`), except `schema_migrations.applied_at` (SQLite `CURRENT_TIMESTAMP` text).

## Migrations

- `MIGRATIONS` is an ordered list of `(version, sql)`. At open, `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)` is created if missing. Each unapplied version runs in its own transaction: the SQL is split on `;` and executed statement by statement, then the version is recorded.
- Rules: **append** new versions and **never edit an applied migration**. Because statements are split on `;`, do not use semicolons inside string literals or trigger bodies in migration SQL.
- Current schema: **version 1** only.

## Tables (migration 1)

### `conversations`
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | uuid4 hex |
| `title` | TEXT NOT NULL | default `'New conversation'`; set from the first 60 chars of the first message |
| `created_at` | REAL NOT NULL | |
| `updated_at` | REAL NOT NULL | bumped on every message |

Index: `idx_conversations_updated (updated_at DESC)`.

### `messages`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `conversation_id` | TEXT NOT NULL | FK → `conversations(id)` **ON DELETE CASCADE** |
| `role` | TEXT NOT NULL | `user`, `assistant`, `tool` |
| `content` | TEXT | |
| `tool_calls` | TEXT | JSON list (assistant tool-call messages) |
| `tool_call_id` | TEXT | for `tool` rows |
| `name` | TEXT | tool name for `tool` rows |
| `task_id` | TEXT | |
| `created_at` | REAL NOT NULL | |

Index: `idx_messages_conversation (conversation_id, id)`.

### `tasks`
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | 16-hex |
| `conversation_id` | TEXT | FK → `conversations(id)` **ON DELETE SET NULL** |
| `input` | TEXT NOT NULL | |
| `source` | TEXT NOT NULL | default `'text'`; `text` or `voice` |
| `route` | TEXT | `direct` or `llm` |
| `status` | TEXT NOT NULL | `running`, `completed`, `failed`, `cancelled` |
| `error` | TEXT | |
| `result` | TEXT | final reply, first 2000 chars |
| `llm_calls` | INTEGER NOT NULL | default 0 |
| `tool_calls` | INTEGER NOT NULL | default 0 |
| `started_at` | REAL NOT NULL | |
| `finished_at` | REAL | |

Index: `idx_tasks_started (started_at DESC)`.

### `execution_logs` (tool audit log)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `task_id` | TEXT | not a FK |
| `tool` | TEXT NOT NULL | |
| `arguments` | TEXT | redacted JSON, ≤ 4000 chars |
| `level` | INTEGER NOT NULL | effective permission level |
| `decision` | TEXT NOT NULL | `auto`, `approved`, `declined`, `denied`, `rejected` |
| `status` | TEXT NOT NULL | `ok`, error code (`timeout`, `not_found`, `path_denied`, …), `invalid`, `blocked`, `denied`, `declined`, `cancelled` |
| `duration_ms` | REAL | |
| `result_preview` | TEXT | redacted summary, ≤ 400 chars |
| `error` | TEXT | redacted, ≤ 1000 chars |
| `created_at` | REAL NOT NULL | |

Indexes: `idx_exec_task (task_id)`, `idx_exec_created (created_at DESC)`.

### `settings`
| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PK | `app_settings` (whole `AppSettings` JSON), `gmail_account` (JSON string email) |
| `value` | TEXT NOT NULL | JSON |
| `updated_at` | REAL NOT NULL | |

On load, stored settings are deep-merged over defaults; `tool_overrides` and `local_limits` are replaced, not merged. Invalid stored settings fall back to defaults.

### `providers` (Groq keys; no secrets)
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | 12-hex |
| `kind` | TEXT NOT NULL | default `'groq'` (only kind loaded today) |
| `label` | TEXT NOT NULL | |
| `enabled` | INTEGER NOT NULL | default 1 |
| `quota_scope` | TEXT NOT NULL | |
| `credential_ref` | TEXT NOT NULL | `groq:<id>`, the Credential Manager entry name |
| `key_hint` | TEXT | e.g. `gsk_…3456` |
| `sort_order` | INTEGER NOT NULL | default 0; round-robin order |
| `health` | TEXT NOT NULL | default `'unknown'`; `healthy`, `degraded`, `invalid` |
| `last_success_at` | REAL | |
| `last_error` | TEXT | redacted |
| `last_error_at` | REAL | |
| `created_at` | REAL NOT NULL | |

### `quota_state`
| Column | Type | Notes |
|---|---|---|
| `scope` | TEXT NOT NULL | PK part |
| `model` | TEXT NOT NULL | PK part |
| `metric` | TEXT NOT NULL | PK part: `rpd`, `tpm`, `rpm`, `tpd`, `ash`, `asd`, `429` |
| `limit_value` | REAL | |
| `remaining` | REAL | |
| `reset_at` | REAL | |
| `frozen_until` | REAL | |
| `source` | TEXT NOT NULL | `provider`, `estimate`, `retry-after` |
| `updated_at` | REAL NOT NULL | |

Primary key `(scope, model, metric)`.

### `usage`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `provider_id` | TEXT NOT NULL | |
| `scope` | TEXT NOT NULL | |
| `model` | TEXT NOT NULL | |
| `kind` | TEXT NOT NULL | `chat`, `stt` |
| `status_code` | INTEGER | NULL for network/timeout errors |
| `prompt_tokens` | INTEGER NOT NULL | default 0 |
| `completion_tokens` | INTEGER NOT NULL | default 0 |
| `audio_seconds` | REAL NOT NULL | default 0 (STT: max(10, duration)) |
| `latency_ms` | REAL | |
| `error` | TEXT | redacted, ≤ 300 chars |
| `created_at` | REAL NOT NULL | |

Indexes: `idx_usage_created (created_at)`, `idx_usage_scope_model (scope, model, created_at)`, `idx_usage_provider (provider_id, created_at)`.

There is no automatic pruning of `usage`, `execution_logs` or `tasks`; use clear data (below).

## Clearing data

`POST /api/data/clear` (UI: Settings → Logs & diagnostics → Maintenance → *Clear application data*):

| Flag | Effect |
|---|---|
| `conversations` | `DELETE FROM messages`, `DELETE FROM conversations` |
| `task_history` | `DELETE FROM tasks`, `DELETE FROM execution_logs` |
| `usage` | `DELETE FROM usage`, `DELETE FROM quota_state`, and clears the in-memory quota entries and usage estimates |
| `settings` | Deletes `app_settings` and re-saves defaults (fires the settings-change listeners) |
| `browser_profile` | Closes the automation browser and deletes `%APPDATA%\Jarvis\browser-profile` |

The table deletions run in one transaction. If anything was cleared, `VACUUM` runs. The response is `{"cleared": [...]}`.

Not cleared by this endpoint: the `providers` rows and Credential Manager secrets (delete keys in Settings → Groq API keys), Gmail authorization (Settings → Gmail), and log files. Conversations can also be deleted individually or all at once in History (`DELETE /api/conversations[/{id}]`).
