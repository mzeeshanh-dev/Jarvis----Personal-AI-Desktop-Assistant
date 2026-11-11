import { FolderOpen, RefreshCw, RotateCcw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, Row, Toggle } from '../components/ui';
import { client } from '../lib/api';
import { bridge } from '../lib/bridge';
import { duration, timeAgo, toolLabel } from '../lib/status';
import { useStore } from '../lib/store';

const LATENCY_LABEL: Record<string, string> = {
  e2e_ms: 'End-to-end command', llm_ms: 'LLM request', stt_ms: 'Speech-to-text', tts_ms: 'Speech synthesis',
  tool_ms: 'Tool execution', voice_preprocess_ms: 'Audio preprocessing',
};

export function PerformanceSection() {
  const [perf, setPerf] = useState<any>(null);
  const [electron, setElectron] = useState<{ pid: number; type: string; cpu: number; memoryMB: number }[]>([]);
  const [auto, setAuto] = useState(true);
  const load = useCallback(async () => {
    const [p, e] = await Promise.all([client.get('/api/perf'), bridge().appMetrics()]);
    setPerf(p); setElectron(e);
  }, []);
  useEffect(() => {
    void load();
    if (!auto) return;
    // Refreshes only while this panel is open.
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [load, auto]);
  if (!perf) return null;
  const eMem = electron.reduce((n, m) => n + m.memoryMB, 0);
  const eCpu = electron.reduce((n, m) => n + m.cpu, 0);
  const childMem = perf.children.reduce((n: number, c: any) => n + c.memory_mb, 0);
  return (
    <>
      <Card title="Resource usage" actions={<label className="flex items-center gap-2 text-[12px] text-muted">Live <Toggle checked={auto} onChange={setAuto} /></label>}>
        <div className="grid grid-cols-4 gap-3">
          <Metric label="Backend CPU" value={`${perf.backend.cpu_percent.toFixed(1)}%`} hint="of one core" />
          <Metric label="Backend RAM" value={`${perf.backend.memory_mb} MB`} />
          <Metric label="Electron RAM" value={`${eMem} MB`} hint={`${electron.length} processes · ${eCpu.toFixed(1)}% CPU`} />
          <Metric label="Helpers RAM" value={`${Math.round(childMem)} MB`} hint={`${perf.children.length} child processes`} />
          <Metric label="System CPU" value={`${perf.system.cpu_percent.toFixed(0)}%`} />
          <Metric label="System memory" value={`${perf.system.memory_percent.toFixed(0)}%`} hint={`${perf.system.memory_available_mb} MB free`} />
          <Metric label="Backend startup" value={perf.backend.startup_ms ? `${Math.round(perf.backend.startup_ms)} ms` : '—'} />
          <Metric label="Uptime" value={duration(perf.backend.uptime_s)} />
        </div>
      </Card>
      <Card title="Latency" description="Measured in this session (last 200 samples each).">
        {Object.keys(perf.latency).length === 0 ? <div className="text-[12.5px] text-muted">No samples yet. Run a command first.</div> : (
          <table className="w-full text-[12.5px]">
            <thead className="text-left text-[11px] tracking-wide text-faint uppercase"><tr><th className="py-1 font-medium">Stage</th><th className="font-medium">Count</th><th className="font-medium">p50</th><th className="font-medium">p95</th><th className="font-medium">Last</th></tr></thead>
            <tbody className="font-mono text-muted">
              {Object.entries(perf.latency).map(([k, v]: [string, any]) => (
                <tr key={k} className="border-t border-ink-700/70"><td className="py-1.5 font-sans text-fg">{LATENCY_LABEL[k] ?? k}</td><td>{v.count}</td><td>{v.p50} ms</td><td>{v.p95} ms</td><td>{v.last} ms</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Activity">
        <Row label="Active tasks"><span className="font-mono text-muted">{perf.active_tasks}</span></Row>
        <Row label="Running managed processes"><span className="font-mono text-muted">{perf.managed_processes}</span></Row>
        <Row label="Automation browser"><Badge tone={perf.browser_running ? 'accent' : 'neutral'}>{perf.browser_running ? 'running' : 'closed'}</Badge></Row>
        <Row label="Microphone"><Badge tone={perf.voice?.mic_open ? 'warn' : 'neutral'}>{perf.voice?.mic_open ? 'open' : 'released'}</Badge></Row>
        <Row label="Wake word"><Badge tone={perf.voice?.wake_word_running ? 'accent' : 'neutral'}>{perf.voice?.wake_word_running ? 'listening' : 'off'}</Badge></Row>
        {perf.children.map((c: any) => <Row key={c.pid} label={<span className="font-mono text-[12px]">{c.name} <span className="text-faint">#{c.pid}</span></span>}><span className="font-mono text-[12px] text-muted">{c.memory_mb} MB</span></Row>)}
      </Card>
    </>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-ink-900/70 px-3 py-2.5">
      <div className="text-[11px] text-faint">{label}</div>
      <div className="mt-0.5 font-mono text-[15px] text-fg tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-faint">{hint}</div>}
    </div>
  );
}

interface LogRow { id: number; task_id: string; tool: string; level: number; decision: string; status: string; duration_ms: number; result_preview: string | null; error: string | null; created_at: number; arguments: string }

export function LogsSection() {
  const { state, actions } = useStore();
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [tail, setTail] = useState<string[]>([]);
  const [clear, setClear] = useState({ conversations: false, task_history: false, usage: false, settings: false, browser_profile: false });
  const load = useCallback(() => {
    void client.get('/api/logs?limit=200').then(setLogs);
    void client.get('/api/logs/backend?lines=200').then((r) => setTail(r.lines));
  }, []);
  useEffect(load, [load, state.lastTaskSummary]);

  const doClear = async () => {
    const chosen = Object.entries(clear).filter(([, v]) => v).map(([k]) => k);
    if (!chosen.length || !window.confirm(`Permanently clear: ${chosen.join(', ')}?`)) return;
    const r = await client.post('/api/data/clear', clear);
    actions.toast('success', `Cleared ${r.cleared.join(', ')}.`);
    if (clear.conversations) { await actions.selectConversation(null); await actions.refreshConversations(); }
    setClear({ conversations: false, task_history: false, usage: false, settings: false, browser_profile: false });
    load();
  };

  return (
    <>
      <Card title="Execution audit log" description="Every tool request, its permission decision and outcome. Arguments are redacted." actions={<Button size="sm" variant="ghost" onClick={load}><RefreshCw size={13} /> Refresh</Button>}>
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-ink-850 text-left text-[11px] tracking-wide text-faint uppercase"><tr><th className="py-1 font-medium">Tool</th><th className="font-medium">Level</th><th className="font-medium">Decision</th><th className="font-medium">Result</th><th className="font-medium">Time</th><th className="font-medium">When</th></tr></thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} className="border-t border-ink-700/70" title={`${l.arguments}\n${l.result_preview ?? l.error ?? ''}`}>
                  <td className="py-1.5 text-fg">{toolLabel(l.tool)}</td>
                  <td className="font-mono text-muted">L{l.level}</td>
                  <td><Badge tone={l.decision === 'auto' ? 'neutral' : l.decision === 'approved' ? 'accent' : 'warn'}>{l.decision}</Badge></td>
                  <td><Badge tone={l.status === 'ok' ? 'good' : 'bad'}>{l.status}</Badge></td>
                  <td className="font-mono text-muted">{Math.round(l.duration_ms)} ms</td>
                  <td className="text-faint">{timeAgo(l.created_at * 1000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!logs.length && <div className="py-3 text-[12.5px] text-muted">No tool executions yet.</div>}
        </div>
      </Card>
      <Card title="Backend log" actions={<Button size="sm" variant="ghost" onClick={() => void bridge().openFolder('logs')}><FolderOpen size={13} /> Open logs folder</Button>}>
        <pre className="selectable max-h-80 overflow-auto rounded-lg border border-ink-700 bg-ink-900 p-3 font-mono text-[11px] leading-relaxed text-muted">{tail.join('') || 'Empty.'}</pre>
      </Card>
      <Card title="Maintenance">
        <Row label="Restart backend" hint={`Restarts the Python service. Restarts this session: ${state.backend?.restarts ?? 0}.`}>
          <Button size="sm" onClick={() => void bridge().restartBackend()}><RotateCcw size={13} /> Restart</Button>
        </Row>
        <div className="border-t border-ink-700/70 pt-3">
          <div className="text-[13px]">Clear application data</div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-[12.5px]">
            {([['conversations', 'Conversation history'], ['task_history', 'Task history & audit log'], ['usage', 'Provider usage & quota state'], ['settings', 'Settings (reset to defaults)'], ['browser_profile', 'Automation browser profile (cookies, logins)']] as const).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 text-muted">
                <input type="checkbox" checked={clear[k]} onChange={(e) => setClear({ ...clear, [k]: e.target.checked })} className="accent-[var(--color-danger)]" /> {label}
              </label>
            ))}
          </div>
          <div className="mt-3 flex justify-end"><Button variant="danger" size="sm" onClick={doClear} disabled={!Object.values(clear).some(Boolean)}>Clear selected</Button></div>
          <p className="mt-2 text-[12px] text-faint">API keys and Gmail authorization live in Windows Credential Manager; remove them in Groq API keys / Gmail.</p>
        </div>
      </Card>
    </>
  );
}
