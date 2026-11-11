// Groq credential manager + quota dashboard.
import { KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, CommitInput, Empty, Meter, Row, Slider, Toggle, cx } from '../components/ui';
import { client } from '../lib/api';
import { duration, timeAgo } from '../lib/status';
import { useStore } from '../lib/store';
import { useSettings } from './Sections';

interface ProviderView {
  id: string; label: string; enabled: boolean; quota_scope: string; key_hint: string; health: string; has_secret: boolean;
  circuit: string; circuit_open_for_s: number; available: boolean; unavailable_reason: string | null; available_in_s: number | null;
  last_success_at: number | null; last_error: string | null; last_error_at: number | null; models: string[] | null;
  requests_24h: number; successful_24h: number; tokens_24h: number; audio_seconds_24h: number; active: boolean;
}
interface QuotaView { scope: string; model: string; metric: string; limit: number | null; remaining: number | null; used_fraction: number | null; reset_in_s: number | null; frozen: boolean; frozen_for_s: number; recovering: boolean; source: string; updated_at: number }
interface Dashboard {
  threshold: number; providers: ProviderView[]; scopes: string[]; quota: QuotaView[];
  estimates: { scope: string; model: string; rpm: number; rpd: number; tpm: number; tpd: number; ash: number; asd: number }[];
  errors: { provider_id: string; model: string; status_code: number | null; error: string; created_at: number }[];
}

const METRIC_LABEL: Record<string, string> = { rpd: 'Requests / day', tpm: 'Tokens / minute', rpm: 'Requests / minute', tpd: 'Tokens / day', ash: 'Audio s / hour', asd: 'Audio s / day', '429': 'Rate limited' };

function healthTone(p: ProviderView) {
  if (!p.enabled) return 'neutral';
  if (p.health === 'invalid') return 'bad';
  if (!p.available) return 'warn';
  return p.health === 'healthy' ? 'good' : p.health === 'degraded' ? 'warn' : 'neutral';
}

function AddKey({ scopes, onAdded }: { scopes: string[]; onAdded: () => void }) {
  const { actions } = useStore();
  const [label, setLabel] = useState('');
  const [key, setKey] = useState('');
  const [scope, setScope] = useState(scopes[0] ?? 'default');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      const r = await client.post('/api/providers', { label, api_key: key, quota_scope: scope });
      setKey(''); setLabel('');
      actions.toast(r.test.ok ? 'success' : 'error', r.test.ok ? `Key verified (${r.test.latency_ms} ms).` : `Key saved but test failed: ${r.test.error}`);
      onAdded();
    } catch (e) {
      actions.toast('error', (e as Error).message);
    } finally { setBusy(false); }
  };
  return (
    <Card title="Add a Groq API key" description="Keys are stored in Windows Credential Manager, never in the database, logs or this window. Create keys at console.groq.com/keys.">
      <div className="grid grid-cols-[1fr_1.6fr] gap-2">
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label (e.g. Personal)" className="h-9 rounded-lg border border-ink-600 bg-ink-800 px-3 text-[13px] outline-none placeholder:text-faint focus:border-accent/60" />
        <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="gsk_…" type="password" autoComplete="off" spellCheck={false} className="h-9 rounded-lg border border-ink-600 bg-ink-800 px-3 font-mono text-[12.5px] outline-none placeholder:text-faint focus:border-accent/60" />
      </div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <label className="flex flex-col gap-1 text-[12px] text-muted">
          Quota scope (organization)
          <input list="scopes" value={scope} onChange={(e) => setScope(e.target.value)} className="h-8 w-56 rounded-lg border border-ink-600 bg-ink-800 px-2.5 text-[13px] text-fg outline-none focus:border-accent/60" />
          <datalist id="scopes">{scopes.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
        <Button variant="primary" onClick={add} disabled={busy || key.trim().length < 8}><Plus size={14} /> {busy ? 'Verifying…' : 'Add & test'}</Button>
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-muted">
        Keys from the <b className="text-fg">same Groq organization share one quota</b>. Give them the same scope so Jarvis freezes them together; extra keys in one organization add failover, not extra quota. Use a different scope only for keys from a different organization.
      </p>
    </Card>
  );
}

function ProviderCard({ p, now, reload }: { p: ProviderView; now: number; reload: () => void }) {
  const { actions } = useStore();
  const [testing, setTesting] = useState(false);
  const patch = async (body: Record<string, unknown>) => {
    try { await client.patch(`/api/providers/${p.id}`, body); reload(); } catch (e) { actions.toast('error', (e as Error).message); }
  };
  const test = async () => {
    setTesting(true);
    try {
      const r = await client.post(`/api/providers/${p.id}/test`);
      actions.toast(r.ok ? 'success' : 'error', r.ok ? `${p.label}: OK, ${r.models.length} models, ${r.latency_ms} ms` : `${p.label}: ${r.error}`);
      reload();
    } finally { setTesting(false); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete key “${p.label}”? It will also be removed from Windows Credential Manager.`)) return;
    await client.del(`/api/providers/${p.id}`);
    reload();
  };
  const rotate = async () => {
    const key = window.prompt(`New API key for “${p.label}”:`);
    if (key && key.trim().length >= 8) { await patch({ api_key: key.trim() }); await test(); }
  };
  const tone = healthTone(p);
  const status = !p.enabled ? 'Disabled' : p.health === 'invalid' ? 'Invalid key' : p.available ? (p.health === 'healthy' ? 'Available' : 'Available (untested)') : 'Unavailable';
  const waitS = p.available_in_s != null ? Math.max(0, p.available_in_s - (now - (p as any)._fetched) / 1000) : null;
  return (
    <div className={cx('rounded-xl border bg-ink-850 p-4', p.active ? 'border-accent/35' : 'border-ink-700')}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-9 place-items-center rounded-lg bg-ink-750 text-accent"><KeyRound size={16} /></div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <CommitInput value={p.label} onCommit={(label) => void patch({ label })} className="h-7! w-44 border-transparent! bg-transparent! px-1! font-medium hover:border-ink-600!" />
              <Badge tone={tone}>{status}</Badge>
              {p.active && <Badge tone="accent">last used</Badge>}
            </div>
            <div className="mt-0.5 pl-1 font-mono text-[11.5px] text-faint">{p.key_hint} · circuit {p.circuit}{p.circuit === 'open' ? ` (${duration(p.circuit_open_for_s)})` : ''}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Toggle checked={p.enabled} onChange={(enabled) => void patch({ enabled })} label="Enabled" />
          <Button size="sm" variant="ghost" onClick={test} disabled={testing}><RefreshCw size={13} className={testing ? 'animate-spin' : ''} /> Test</Button>
          <Button size="sm" variant="ghost" onClick={rotate}>Replace key</Button>
          <button aria-label="Delete key" onClick={remove} className="rounded-md p-1.5 text-faint hover:bg-danger/10 hover:text-danger"><Trash2 size={14} /></button>
        </div>
      </div>
      {!p.available && p.unavailable_reason && (
        <div className="mt-3 rounded-lg bg-warm/8 px-3 py-2 text-[12px] text-warm">{p.unavailable_reason}{waitS != null && waitS > 0 ? ` · recovers in ${duration(waitS)}` : ''}</div>
      )}
      <div className="mt-3 grid grid-cols-4 gap-3 text-[12px]">
        <Stat label="Requests (24h)" value={`${p.successful_24h}/${p.requests_24h}`} hint="successful / total" />
        <Stat label="Tokens (24h)" value={p.tokens_24h.toLocaleString()} />
        <Stat label="Audio (24h)" value={`${p.audio_seconds_24h}s`} />
        <Stat label="Last success" value={p.last_success_at ? timeAgo(p.last_success_at * 1000) : 'never'} />
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 text-[12px]">
        <label className="flex items-center gap-2 text-muted">Quota scope
          <CommitInput value={p.quota_scope} onCommit={(quota_scope) => void patch({ quota_scope })} className="h-7! w-40" />
        </label>
        {p.last_error && <span className="truncate text-faint" title={p.last_error}>Last error {p.last_error_at ? timeAgo(p.last_error_at * 1000) : ''}: {p.last_error}</span>}
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-ink-900/70 px-3 py-2" title={hint}>
      <div className="text-[11px] text-faint">{label}</div>
      <div className="mt-0.5 font-mono text-[13px] text-fg tabular-nums">{value}</div>
    </div>
  );
}

export function GroqKeysSection() {
  const { state } = useStore();
  const [s, save] = useSettings();
  const [d, setD] = useState<(Dashboard & { fetched: number }) | null>(null);
  const [now, setNow] = useState(Date.now());
  const load = useCallback(() => {
    void client.get<Dashboard>('/api/providers').then((r) => {
      const fetched = Date.now();
      r.providers.forEach((p) => { (p as any)._fetched = fetched; });
      setD({ ...r, fetched });
    }).catch(() => {});
  }, []);
  // Event-driven refresh (providers.changed) plus a local 1 s tick for countdowns: no polling.
  useEffect(load, [load, state.providersVersion]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  if (!d) return null;
  const elapsed = (now - d.fetched) / 1000;
  const total = d.providers.reduce((n, p) => n + p.requests_24h, 0);
  const providerQuota = d.quota.filter((q) => q.metric !== '429' || q.frozen);

  return (
    <>
      <Card title="Quota protection" description="When a quota reaches this level Jarvis stops sending new requests to that quota scope and fails over to another scope, until the provider-reported reset passes and a probe request succeeds.">
        <Row label="Freeze threshold">
          <Slider value={s.providers.quota_threshold} min={0.5} max={1} step={0.01} onCommit={(x) => void save({ providers: { quota_threshold: x } })} format={(x) => `${Math.round(x * 100)}%`} />
        </Row>
      </Card>

      <AddKey scopes={d.scopes} onAdded={load} />

      {d.providers.length === 0
        ? <Card><Empty icon={<KeyRound size={26} />} title="No Groq keys yet">Add at least one key to enable the agent and voice transcription.</Empty></Card>
        : <div className="flex flex-col gap-3">{d.providers.map((p) => <ProviderCard key={p.id} p={p} now={now} reload={load} />)}</div>}

      {total > 0 && (
        <Card title="Request distribution (24h)" description="Round-robin over eligible keys only; frozen, invalid or disabled keys are skipped.">
          <div className="flex h-2.5 overflow-hidden rounded-full bg-ink-700">
            {d.providers.map((p, i) => p.requests_24h > 0 && (
              <div key={p.id} style={{ width: `${(p.requests_24h / total) * 100}%`, background: ['#4fd1f5', '#f0a742', '#e5566b', '#c79ad6', '#d6c38f'][i % 5] }} title={`${p.label}: ${p.requests_24h}`} />
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
            {d.providers.map((p, i) => (
              <span key={p.id} className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: ['#4fd1f5', '#f0a742', '#e5566b', '#c79ad6', '#d6c38f'][i % 5] }} />{p.label} {Math.round((p.requests_24h / total) * 100)}%</span>
            ))}
          </div>
        </Card>
      )}

      <Card title="Quota by scope and model" description={<>Bars marked <Badge tone="accent">provider</Badge> come from Groq’s rate-limit headers; <Badge>estimate</Badge> values are computed locally from Jarvis’s own usage.</>}>
        {providerQuota.length === 0 && <div className="py-2 text-[12.5px] text-muted">No quota data yet. It appears after the first request.</div>}
        <div className="flex flex-col gap-3">
          {providerQuota.map((q) => {
            const used = q.used_fraction ?? 0;
            const resetIn = q.reset_in_s != null ? Math.max(0, q.reset_in_s - elapsed) : null;
            const frozenFor = Math.max(0, q.frozen_for_s - elapsed);
            const tone = q.frozen && frozenFor > 0 ? 'bad' : used >= d.threshold * 0.85 ? 'warn' : 'good';
            return (
              <div key={`${q.scope}-${q.model}-${q.metric}`}>
                <div className="mb-1 flex items-center justify-between gap-2 text-[12px]">
                  <span className="flex items-center gap-2">
                    <span className="text-fg">{METRIC_LABEL[q.metric] ?? q.metric}</span>
                    <span className="font-mono text-faint">{q.scope} · {q.model}</span>
                    <Badge tone={q.source === 'estimate' ? 'neutral' : q.source === 'retry-after' ? 'warn' : 'accent'}>
                      {q.source === 'estimate' ? 'estimate' : q.source === 'retry-after' ? '429 retry-after' : 'provider'}
                    </Badge>
                    {q.frozen && frozenFor > 0 && <Badge tone="bad">frozen {duration(frozenFor)}</Badge>}
                    {q.recovering && !(q.frozen && frozenFor > 0) && <Badge tone="warn">revalidating</Badge>}
                  </span>
                  <span className="font-mono text-muted tabular-nums">
                    {q.limit != null && q.remaining != null ? `${(q.limit - q.remaining).toLocaleString()} / ${q.limit.toLocaleString()}` : '—'}
                    {resetIn != null && ` · resets in ${duration(resetIn)}`}
                  </span>
                </div>
                {q.limit != null && <Meter value={used} tone={tone} />}
              </div>
            );
          })}
        </div>
      </Card>

      {d.estimates.length > 0 && (
        <Card title="Local usage estimates" description="Counted by Jarvis from successful requests (sliding windows).">
          <table className="w-full text-[12px]">
            <thead className="text-left text-[11px] tracking-wide text-faint uppercase"><tr><th className="py-1 font-medium">Scope · model</th><th className="font-medium">RPM</th><th className="font-medium">RPD</th><th className="font-medium">TPM</th><th className="font-medium">TPD</th><th className="font-medium">ASH</th><th className="font-medium">ASD</th></tr></thead>
            <tbody className="font-mono text-muted">
              {d.estimates.map((e) => (
                <tr key={`${e.scope}-${e.model}`} className="border-t border-ink-700/70">
                  <td className="py-1.5 text-fg/80">{e.scope} · {e.model}</td><td>{e.rpm}</td><td>{e.rpd}</td><td>{e.tpm.toLocaleString()}</td><td>{e.tpd.toLocaleString()}</td><td>{Math.round(e.ash)}</td><td>{Math.round(e.asd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Error history" description="Most recent provider errors (secrets are redacted).">
        {d.errors.length === 0 ? <div className="py-1 text-[12.5px] text-muted">No errors recorded.</div> : (
          <div className="flex max-h-64 flex-col overflow-y-auto">
            {d.errors.map((e, i) => (
              <div key={i} className="flex items-baseline gap-3 border-t border-ink-700/70 py-1.5 text-[12px] first:border-t-0">
                <Badge tone={e.status_code === 429 ? 'warn' : 'bad'}>{e.status_code ?? 'net'}</Badge>
                <span className="w-24 shrink-0 truncate text-muted">{d.providers.find((p) => p.id === e.provider_id)?.label ?? e.provider_id}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-faint" title={e.error}>{e.error}</span>
                <span className="shrink-0 text-faint">{timeAgo(e.created_at * 1000)}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
