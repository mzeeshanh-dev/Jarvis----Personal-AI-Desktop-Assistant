"""Gmail API over HTTPX with OAuth 2.0 (installed-app flow, PKCE, loopback redirect).

Credentials (client id/secret, refresh token) live in Windows Credential
Manager. No Gmail password is ever requested. Scopes: read + compose (drafts).
Jarvis never sends email; it only creates drafts for the user to send.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import re
import secrets
import time
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from core.secrets import SecretStore
from storage.database import Database, dumps, loads

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]

REF_CLIENT_ID = "gmail:client_id"
REF_CLIENT_SECRET = "gmail:client_secret"
REF_REFRESH = "gmail:refresh_token"


class GmailError(Exception):
    pass


class GmailNotConnected(GmailError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", s)).strip()


def _decode(data: str | None) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def extract_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    htmls: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        if part.get("filename"):
            return
        if mime == "text/plain":
            plain.append(_decode(part.get("body", {}).get("data")))
        elif mime == "text/html":
            htmls.append(_decode(part.get("body", {}).get("data")))
        for p in part.get("parts", []) or []:
            walk(p)
    walk(payload)
    if plain and any(t.strip() for t in plain):
        return "\n".join(plain).strip()
    return _html_to_text("\n".join(htmls))


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _attachments(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        if part.get("filename"):
            out.append(part["filename"])
        for p in part.get("parts", []) or []:
            walk(p)
    walk(payload)
    return out


class GmailService:
    def __init__(self, db: Database, secrets_store: SecretStore, client: httpx.AsyncClient | None = None) -> None:
        self.db = db
        self.secrets = secrets_store
        self.http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._access: tuple[str, float] | None = None
        self._pending: dict[str, tuple[str, str, float]] = {}  # state -> (verifier, redirect_uri, created)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    # ----------------------------------------------------------- config/OAuth
    async def status(self) -> dict[str, Any]:
        has_client = bool(await self.secrets.get(REF_CLIENT_ID)) and bool(await self.secrets.get(REF_CLIENT_SECRET))
        connected = bool(await self.secrets.get(REF_REFRESH))
        row = await self.db.fetchone("SELECT value FROM settings WHERE key='gmail_account'")
        return {"client_configured": has_client, "connected": connected, "account": loads(row["value"]) if row else None}

    async def configure(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
            raise GmailError("That doesn't look like a Google OAuth Desktop client (client id must end with .apps.googleusercontent.com).")
        await self.secrets.set(REF_CLIENT_ID, client_id)
        await self.secrets.set(REF_CLIENT_SECRET, client_secret)

    async def start_auth(self, redirect_uri: str) -> str:
        client_id = await self.secrets.get(REF_CLIENT_ID)
        if not client_id:
            raise GmailError("Add your Google OAuth client first (Settings → Gmail).")
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(24)
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if now - v[2] < 600}
        self._pending[state] = (verifier, redirect_uri, now)
        return AUTH_URL + "?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        })

    async def complete_auth(self, code: str, state: str) -> str:
        pending = self._pending.pop(state, None)
        if not pending or time.time() - pending[2] > 600:
            raise GmailError("Sign-in session expired or is invalid. Start again from Jarvis settings.")
        verifier, redirect_uri, _ = pending
        resp = await self.http.post(TOKEN_URL, data={
            "code": code, "client_id": await self.secrets.get(REF_CLIENT_ID),
            "client_secret": await self.secrets.get(REF_CLIENT_SECRET), "redirect_uri": redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier,
        })
        if resp.status_code != 200:
            raise GmailError(f"Google rejected the sign-in ({resp.status_code}).")
        tok = resp.json()
        if not tok.get("refresh_token"):
            raise GmailError("Google did not return a refresh token. Remove Jarvis from your Google account permissions and try again.")
        await self.secrets.set(REF_REFRESH, tok["refresh_token"])
        self._access = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)))
        profile = await self._api("GET", "/profile")
        email = profile.get("emailAddress")
        await self.db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('gmail_account', ?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                              (dumps(email), time.time()))
        return email or ""

    async def disconnect(self) -> None:
        token = await self.secrets.get(REF_REFRESH)
        if token:
            try:
                await self.http.post(REVOKE_URL, data={"token": token})
            except httpx.HTTPError:
                pass
        await self.secrets.delete(REF_REFRESH)
        self._access = None
        await self.db.execute("DELETE FROM settings WHERE key='gmail_account'")

    async def remove_client(self) -> None:
        await self.disconnect()
        await self.secrets.delete(REF_CLIENT_ID)
        await self.secrets.delete(REF_CLIENT_SECRET)

    async def _token(self, force: bool = False) -> str:
        async with self._lock:
            if self._access and not force and self._access[1] - 60 > time.time():
                return self._access[0]
            refresh = await self.secrets.get(REF_REFRESH)
            if not refresh:
                raise GmailNotConnected("Gmail is not connected. Connect it in Settings → Gmail.")
            resp = await self.http.post(TOKEN_URL, data={
                "client_id": await self.secrets.get(REF_CLIENT_ID), "client_secret": await self.secrets.get(REF_CLIENT_SECRET),
                "refresh_token": refresh, "grant_type": "refresh_token"})
            if resp.status_code != 200:
                if resp.status_code in (400, 401):
                    raise GmailNotConnected("Gmail authorization expired or was revoked. Reconnect it in Settings.")
                raise GmailError(f"Token refresh failed ({resp.status_code}).")
            tok = resp.json()
            self._access = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)))
            return self._access[0]

    async def _api(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._token(force=attempt > 0)
            resp = await self.http.request(method, API + path, params=params, json=json, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401 and attempt == 0:
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise GmailError(f"Gmail API error {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise GmailError("Gmail API request failed after retry.")

    # ----------------------------------------------------------------- reads
    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        data = await self._api("GET", "/messages", params={"q": query, "maxResults": max_results})
        ids = [m["id"] for m in data.get("messages", [])]
        sem = asyncio.Semaphore(8)

        async def meta(mid: str) -> dict[str, Any]:
            async with sem:
                m = await self._api("GET", f"/messages/{mid}", params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]})
            h = _headers(m.get("payload", {}))
            return {"id": m["id"], "thread_id": m.get("threadId"), "from": h.get("from"), "to": h.get("to"),
                    "subject": h.get("subject"), "date": h.get("date"), "snippet": html.unescape(m.get("snippet", "")),
                    "unread": "UNREAD" in m.get("labelIds", []),
                    "gmail_url": f"https://mail.google.com/mail/u/0/#all/{m.get('threadId')}"}
        return list(await asyncio.gather(*(meta(i) for i in ids)))

    async def message(self, mid: str, max_chars: int = 8000) -> dict[str, Any]:
        m = await self._api("GET", f"/messages/{mid}", params={"format": "full"})
        payload = m.get("payload", {})
        h = _headers(payload)
        body = extract_body(payload)
        return {"id": m["id"], "thread_id": m.get("threadId"), "from": h.get("from"), "to": h.get("to"), "cc": h.get("cc"),
                "subject": h.get("subject"), "date": h.get("date"), "message_id_header": h.get("message-id"),
                "body": body[:max_chars], "truncated": len(body) > max_chars, "attachments": _attachments(payload),
                "gmail_url": f"https://mail.google.com/mail/u/0/#all/{m.get('threadId')}"}

    async def thread(self, tid: str, max_chars_per_message: int = 4000) -> dict[str, Any]:
        t = await self._api("GET", f"/threads/{tid}", params={"format": "full"})
        msgs = []
        for m in t.get("messages", []):
            payload = m.get("payload", {})
            h = _headers(payload)
            body = extract_body(payload)
            msgs.append({"id": m["id"], "from": h.get("from"), "date": h.get("date"), "subject": h.get("subject"),
                         "body": body[:max_chars_per_message], "truncated": len(body) > max_chars_per_message})
        return {"thread_id": tid, "messages": msgs, "gmail_url": f"https://mail.google.com/mail/u/0/#all/{tid}"}

    async def create_draft(self, to: list[str], subject: str, body: str, cc: list[str] | None = None,
                           reply_to_message_id: str | None = None) -> dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.set_content(body)
        draft: dict[str, Any] = {}
        if reply_to_message_id:
            orig = await self._api("GET", f"/messages/{reply_to_message_id}", params={"format": "metadata", "metadataHeaders": ["Message-ID", "References"]})
            h = _headers(orig.get("payload", {}))
            if h.get("message-id"):
                msg["In-Reply-To"] = h["message-id"]
                msg["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
            draft["threadId"] = orig.get("threadId")
        draft["raw"] = _b64url(msg.as_bytes())
        res = await self._api("POST", "/drafts", json={"message": draft})
        return {"draft_id": res.get("id"), "message_id": res.get("message", {}).get("id"),
                "gmail_url": "https://mail.google.com/mail/u/0/#drafts"}
