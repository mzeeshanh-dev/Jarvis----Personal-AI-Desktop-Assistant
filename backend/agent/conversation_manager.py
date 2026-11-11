"""Conversation and message persistence."""
from __future__ import annotations

import time
import uuid
from typing import Any

from storage.database import Database, dumps, loads


class ConversationManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(self, conversation_id: str | None, first_text: str) -> str:
        if conversation_id:
            row = await self.db.fetchone("SELECT id FROM conversations WHERE id=?", (conversation_id,))
            if row:
                return conversation_id
        cid = conversation_id or uuid.uuid4().hex
        now = time.time()
        title = first_text.strip().replace("\n", " ")
        title = (title[:60] + "…") if len(title) > 60 else (title or "New conversation")
        await self.db.execute("INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)", (cid, title, now, now))
        return cid

    async def add(self, conversation_id: str, role: str, content: str | None, *, tool_calls: list[dict] | None = None,
                  tool_call_id: str | None = None, name: str | None = None, task_id: str | None = None) -> int:
        now = time.time()
        mid = await self.db.execute(
            "INSERT INTO messages(conversation_id, role, content, tool_calls, tool_call_id, name, task_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (conversation_id, role, content, dumps(tool_calls) if tool_calls else None, tool_call_id, name, task_id, now))
        await self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        return mid

    async def history(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (conversation_id, limit))
        for r in rows:
            r["tool_calls"] = loads(r["tool_calls"])
        return rows

    async def list(self, limit: int = 100, query: str | None = None) -> list[dict[str, Any]]:
        if query:
            return await self.db.fetchall(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.role IN ('user','assistant') AND m.content IS NOT NULL) AS message_count "
                "FROM conversations c WHERE c.title LIKE ? OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.content LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?", (f"%{query}%", f"%{query}%", limit))
        return await self.db.fetchall(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.role IN ('user','assistant') AND m.content IS NOT NULL) AS message_count "
            "FROM conversations c ORDER BY updated_at DESC LIMIT ?", (limit,))

    async def messages_for_ui(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT id, role, content, tool_calls, name, task_id, created_at FROM messages WHERE conversation_id=? ORDER BY id",
            (conversation_id,))
        out = []
        for r in rows:
            r["tool_calls"] = loads(r["tool_calls"])
            out.append(r)
        return out

    async def rename(self, conversation_id: str, title: str) -> None:
        await self.db.execute("UPDATE conversations SET title=? WHERE id=?", (title.strip()[:120] or "Conversation", conversation_id))

    async def delete(self, conversation_id: str) -> None:
        await self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    async def delete_all(self) -> None:
        def _do(conn: Any) -> None:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM conversations")
        await self.db.transaction(_do)
