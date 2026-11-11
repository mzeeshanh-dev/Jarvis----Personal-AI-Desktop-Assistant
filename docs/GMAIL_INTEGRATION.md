# Gmail integration

Code: `backend/integrations/gmail.py` (`GmailService`), `backend/tools/email.py` (tools), routes under `/api/gmail/*`, UI Settings → Gmail (`settings/Gmail.tsx`).

## Design

- Uses the official **Gmail REST API over HTTPX** (one reused `httpx.AsyncClient`, 30 s timeout, 10 s connect) instead of `google-api-python-client`. Only a handful of endpoints are needed, and this avoids a large dependency tree.
- OAuth 2.0 **installed-app flow with PKCE (S256) and a loopback redirect** to the backend. Jarvis never asks for a Gmail password.
- Scopes: **`https://www.googleapis.com/auth/gmail.readonly`** and **`https://www.googleapis.com/auth/gmail.compose`** (needed to create drafts).
- Client id, client secret and refresh token are stored in Windows Credential Manager (`gmail:client_id`, `gmail:client_secret`, `gmail:refresh_token`). The access token is held only in memory and refreshed 60 s before expiry. The connected account's email address is stored in the `settings` table under key `gmail_account`.
- **No send tool exists, by design.** Jarvis can search, read and summarize email and **create drafts**. You review and send drafts yourself in Gmail.

## Setup step by step

1. Open the **Google Cloud Console** (`console.cloud.google.com`) and create or select a project.
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **OAuth consent screen** → user type **External** → fill in the required app information → add your own Google account as a **test user**. In testing mode only test users can authorize the app.
4. Add the scopes `gmail.readonly` and `gmail.compose` to the consent screen. Jarvis requests both, so they must be allowed.
5. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → create → **Download JSON** (`client_secret_….json`).
6. In Jarvis: Settings → **Gmail** → paste the JSON file's contents, or `CLIENT_ID CLIENT_SECRET` separated by whitespace, → **Save client**.
   - `POST /api/gmail/client` accepts the `installed` (or `web`) block of the JSON. The client id must end in `.apps.googleusercontent.com` or the request is rejected.
7. Click **Connect Gmail**. Jarvis opens Google sign-in in your default browser (`shell.openExternal`, https only).
8. Choose the test-user account and grant access. Google redirects to `http://127.0.0.1:<backend port>/api/gmail/oauth/callback`, and the page shows "Gmail connected — Signed in as …". The Settings page updates on the `gmail.changed` event.

Desktop-app OAuth clients accept loopback redirects on any port, so the changing backend port is fine. The redirect URI is built from the port of the request that started the flow, and the same URI is sent in the token exchange.

## OAuth flow details

```
POST /api/gmail/connect
  verifier = base64url(48 random bytes); challenge = base64url(SHA-256(verifier))
  state = token_urlsafe(24); pending[state] = (verifier, redirect_uri, now)   (expire after 10 min)
  -> auth_url = accounts.google.com/o/oauth2/v2/auth?client_id&redirect_uri&response_type=code
               &scope="gmail.readonly gmail.compose"&access_type=offline&prompt=consent
               &code_challenge&code_challenge_method=S256&state
browser -> Google consent -> GET /api/gmail/oauth/callback?code&state      (public route; token not required)
  state must match a pending entry (< 10 min) -> POST oauth2.googleapis.com/token
     (code, client_id, client_secret, redirect_uri, grant_type=authorization_code, code_verifier)
  refresh_token required -> stored in Credential Manager; access token kept in memory
  GET /profile -> emailAddress saved as gmail_account
```

- `access_type=offline` plus `prompt=consent` makes Google return a **refresh token**. If none comes back, the error is: "Google did not return a refresh token. Remove Jarvis from your Google account permissions and try again."
- The pending `state` is held in memory, so restarting the backend mid-flow invalidates it ("Sign-in session expired or is invalid").
- **Disconnect**: revokes the refresh token at `oauth2.googleapis.com/revoke` (best effort), deletes it, and clears the account. **Remove client** does the same and also deletes the client id and secret.
- Token refresh failure with 400/401 → `GmailNotConnected` ("Gmail authorization expired or was revoked. Reconnect it in Settings.").

## API usage and retries

Base URL `https://gmail.googleapis.com/gmail/v1/users/me`. `_api()` makes 2 attempts: a 401 forces a token refresh and retry, and 429/5xx sleep `1.5 s × attempt` before retrying. Other ≥ 400 statuses raise `GmailError` (status + 200 chars of the body). In tools, `GmailError` is raised as a transient `ToolError`, which the idempotent read tools retry once.

| Operation | Endpoint |
|---|---|
| search | `GET /messages?q=&maxResults=`, then `GET /messages/{id}?format=metadata` for From/To/Subject/Date (up to 8 concurrently) |
| message | `GET /messages/{id}?format=full`; body = text/plain parts, else HTML converted to text; attachment names listed |
| thread | `GET /threads/{id}?format=full` (4000 chars per message) |
| draft | `POST /drafts` with a base64url RFC 822 message; replies set `In-Reply-To`, `References` and `threadId` |

Every result includes a `gmail_url` (`https://mail.google.com/mail/u/0/#all/<threadId>` or `#drafts`).

## Tools

| Tool | Level | Notes |
|---|---|---|
| `search_emails` | 0 | `query` uses Gmail search syntax (e.g. `from:alice is:unread`, `GreenBot newer_than:30d`); `max_results` 1–25 (default 10). Returns id, thread_id, from, to, subject, date, snippet, unread, gmail_url. **Untrusted** |
| `read_email` | 0 | `message_id` (`[A-Za-z0-9_-]`, 5–64 chars); body up to 8000 chars. **Untrusted** |
| `read_thread` | 0 | `thread_id`. **Untrusted** |
| `summarize_emails` | 0 | `query` or `message_ids` (≤ 20), `instruction`, `max_emails` ≤ 20 (default 10). Fetches messages (2500 chars each), sorts by date, and makes **one** chat call with the summarization model (temperature 0.2, max 1200 tokens) with the emails wrapped in `<untrusted_content>`. **Untrusted** |
| `create_email_draft` | 3 | `to` (1–20), `subject`, `body`, optional `cc`, `reply_to_message_id`. Addresses validated. Creates a **draft only** |

Because email content is untrusted, a draft created after reading email always asks for approval, even with the level-3 policy set to automatic.

## Not implemented

- **Sending email** (intentionally; no send tool and no `gmail.send` scope).
- Labels, archiving, deleting, attachment download.
- Multiple Gmail accounts (one account per installation).
