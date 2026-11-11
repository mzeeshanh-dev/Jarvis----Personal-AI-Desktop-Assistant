import { Mail } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, Row } from '../components/ui';
import { client } from '../lib/api';
import { bridge } from '../lib/bridge';
import { useStore } from '../lib/store';

interface Status { client_configured: boolean; connected: boolean; account: string | null }

export function GmailSection() {
  const { state, actions } = useStore();
  const [st, setSt] = useState<Status | null>(null);
  const [json, setJson] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { void client.get<Status>('/api/gmail/status').then(setSt); }, []);
  useEffect(load, [load, state.gmailVersion]);

  const saveClient = async () => {
    setBusy(true);
    try {
      const trimmed = json.trim();
      setSt(await client.post('/api/gmail/client', trimmed.startsWith('{') ? { client_json: trimmed } : { client_id: trimmed.split(/\s+/)[0], client_secret: trimmed.split(/\s+/)[1] }));
      setJson('');
      actions.toast('success', 'Google OAuth client saved to Windows Credential Manager.');
    } catch (e) { actions.toast('error', (e as Error).message); } finally { setBusy(false); }
  };
  const connect = async () => {
    try {
      const r = await client.post('/api/gmail/connect');
      await bridge().openExternal(r.auth_url);
      actions.toast('info', 'Finish signing in in your browser. This page updates automatically.');
    } catch (e) { actions.toast('error', (e as Error).message); }
  };

  if (!st) return null;
  return (
    <>
      <Card title="Gmail account" description="Jarvis uses the official Gmail API with OAuth 2.0. It never asks for your Gmail password and never sends email: it can search, read and create drafts that you send yourself."
        actions={<Badge tone={st.connected ? 'good' : 'neutral'}>{st.connected ? 'Connected' : st.client_configured ? 'Not connected' : 'Not configured'}</Badge>}>
        {st.connected ? (
          <>
            <Row label={<span className="flex items-center gap-2"><Mail size={14} className="text-accent" /> {st.account ?? 'Connected account'}</span>} hint="Scopes: gmail.readonly, gmail.compose (drafts)">
              <Button size="sm" variant="danger" onClick={async () => { setSt(await client.post('/api/gmail/disconnect')); }}>Disconnect</Button>
            </Row>
          </>
        ) : st.client_configured ? (
          <Row label="OAuth client configured" hint="Opens Google sign-in in your default browser; the redirect returns to Jarvis on this computer only.">
            <Button variant="primary" size="sm" onClick={connect}>Connect Gmail</Button>
          </Row>
        ) : (
          <div className="flex flex-col gap-2">
            <textarea value={json} onChange={(e) => setJson(e.target.value)} rows={4} spellCheck={false}
              placeholder='Paste the contents of client_secret_….json (Desktop app OAuth client), or "CLIENT_ID CLIENT_SECRET"'
              className="selectable w-full resize-none rounded-lg border border-ink-600 bg-ink-800 p-3 font-mono text-[12px] outline-none placeholder:text-faint focus:border-accent/60" />
            <div className="flex justify-end"><Button variant="primary" size="sm" onClick={saveClient} disabled={busy || !json.trim()}>Save client</Button></div>
          </div>
        )}
        {st.client_configured && (
          <Row label="Remove OAuth client" hint="Deletes the client id/secret and any authorization from Credential Manager.">
            <Button size="sm" variant="ghost" onClick={async () => { setSt(await client.del('/api/gmail/client')); }}>Remove</Button>
          </Row>
        )}
      </Card>
      <Card title="One-time setup (about 5 minutes)">
        <ol className="list-decimal space-y-1.5 pl-5 text-[12.5px] leading-relaxed text-muted">
          <li>Open <a className="cursor-pointer text-accent underline" onClick={() => void bridge().openExternal('https://console.cloud.google.com/')}>Google Cloud Console</a> and create (or pick) a project.</li>
          <li>APIs &amp; Services → Library → enable <b className="text-fg">Gmail API</b>.</li>
          <li>OAuth consent screen → External → add your own Google account as a <b className="text-fg">test user</b>.</li>
          <li>Credentials → Create credentials → OAuth client ID → Application type <b className="text-fg">Desktop app</b> → Download JSON.</li>
          <li>Paste the JSON above, click Save client, then Connect Gmail.</li>
        </ol>
      </Card>
    </>
  );
}
