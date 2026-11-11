"""Ordered schema migrations. Append new entries; never edit applied ones."""

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    task_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, id);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    input TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    route TEXT,
    status TEXT NOT NULL,
    error TEXT,
    result TEXT,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX idx_tasks_started ON tasks(started_at DESC);

CREATE TABLE execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    tool TEXT NOT NULL,
    arguments TEXT,
    level INTEGER NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL,
    result_preview TEXT,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX idx_exec_task ON execution_logs(task_id);
CREATE INDEX idx_exec_created ON execution_logs(created_at DESC);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'groq',
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    quota_scope TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    key_hint TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    health TEXT NOT NULL DEFAULT 'unknown',
    last_success_at REAL,
    last_error TEXT,
    last_error_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE quota_state (
    scope TEXT NOT NULL,
    model TEXT NOT NULL,
    metric TEXT NOT NULL,
    limit_value REAL,
    remaining REAL,
    reset_at REAL,
    frozen_until REAL,
    source TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, model, metric)
);

CREATE TABLE usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    status_code INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    audio_seconds REAL NOT NULL DEFAULT 0,
    latency_ms REAL,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX idx_usage_created ON usage(created_at);
CREATE INDEX idx_usage_scope_model ON usage(scope, model, created_at);
CREATE INDEX idx_usage_provider ON usage(provider_id, created_at)
"""),
]
