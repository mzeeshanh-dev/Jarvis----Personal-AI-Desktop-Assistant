import { useEffect, useState } from 'react';
import { Logo } from '../components/Emblem';
import { Badge, Button, Card, CommitInput, Row, Select, Slider, Toggle, cx } from '../components/ui';
import { client } from '../lib/api';
import { bridge } from '../lib/bridge';
import { useStore } from '../lib/store';
import type { Settings } from '../lib/types';

export function useSettings(): [Settings, ReturnType<typeof useStore>['actions']['saveSettings']] {
  const { state, actions } = useStore();
  return [state.settings!, actions.saveSettings];
}

const num = (v: string, fallback: number) => (Number.isFinite(Number(v)) && v.trim() !== '' ? Number(v) : fallback);

// ------------------------------------------------------------------ general
function ShortcutRecorder({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [recording, setRecording] = useState(false);
  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      if (e.key === 'Escape') { setRecording(false); return; }
      if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
      const mods = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Super'].filter(Boolean) as string[];
      if (!mods.length) return;
      const key = e.code === 'Space' ? 'Space' : e.code.startsWith('Key') ? e.code.slice(3) : e.code.startsWith('Digit') ? e.code.slice(5) : /^F\d+$/.test(e.key) ? e.key : e.key.length === 1 ? e.key.toUpperCase() : e.key;
      setRecording(false);
      onChange([...mods, key].join('+'));
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [recording, onChange]);
  return (
    <button onClick={() => setRecording(true)}
      className={cx('h-8 min-w-40 rounded-lg border px-3 font-mono text-[12.5px]', recording ? 'border-accent/60 bg-accent/10 text-accent' : 'border-ink-600 bg-ink-800 text-fg hover:border-ink-500')}>
      {recording ? 'Press keys… (Esc)' : value.replace('Control', 'Ctrl')}
    </button>
  );
}

export function GeneralSection() {
  const [s, save] = useSettings();
  const g = s.general;
  return (
    <>
      <Card title="Activation" description="The global shortcut toggles push-to-talk from anywhere in Windows.">
        <Row label="Push-to-talk shortcut" hint="Press once to start listening; Jarvis stops when you pause, or press again to finish.">
          <ShortcutRecorder value={g.shortcut} onChange={(shortcut) => void save({ general: { shortcut } })} />
        </Row>
        <Row label="Show floating assistant when listening" hint="Compact status window while the main window is hidden.">
          <Toggle checked={g.show_float_on_voice} onChange={(v) => void save({ general: { show_float_on_voice: v } })} />
        </Row>
        <Row label="Keep floating assistant on top">
          <Toggle checked={g.float_always_on_top} onChange={(v) => void save({ general: { float_always_on_top: v } })} />
        </Row>
        <Row label="Floating assistant"><Button size="sm" onClick={() => void bridge().showFloat()}>Show now</Button></Row>
      </Card>
      <Card title="Windows">
        <Row label="Start Jarvis when I sign in to Windows" hint="Starts minimized to the tray. Only applies to the installed app.">
          <Toggle checked={g.launch_at_login} onChange={(v) => void save({ general: { launch_at_login: v } })} />
        </Row>
        <Row label="Closing the window keeps Jarvis running in the tray">
          <Toggle checked={g.close_to_tray} onChange={(v) => void save({ general: { close_to_tray: v } })} />
        </Row>
        <Row label="Minimize to the tray" hint="The shortcut, wake word and running tasks keep working while Jarvis is hidden.">
          <Toggle checked={g.minimize_to_tray} onChange={(v) => void save({ general: { minimize_to_tray: v } })} />
        </Row>
      </Card>
      <Card title="Personalization">
        <Row label="Your name" hint="Used in greetings and given to the assistant as context.">
          <CommitInput value={g.user_name} placeholder="Optional" onCommit={(user_name) => void save({ general: { user_name } })} className="w-56" />
        </Row>
      </Card>
    </>
  );
}

// -------------------------------------------------------------------- voice
interface Device { index: number; name: string; hostapi: string }

export function VoiceSection() {
  const [s, save] = useSettings();
  const { state } = useStore();
  const v = s.voice;
  const [devices, setDevices] = useState<Device[]>([]);
  const [devError, setDevError] = useState<string | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [mic, setMic] = useState<{ ok: boolean; error?: string; level?: number } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    void client.get('/api/voice/devices').then((r) => { setDevices(r.devices); setDevError(r.error ?? null); });
    void client.get('/api/voice/status').then(setStatus);
  }, [state.voice.wakeWord]);

  const testMic = async () => {
    setTesting(true);
    try { setMic(await client.post('/api/voice/test-mic')); } finally { setTesting(false); }
  };

  return (
    <>
      <Card title="Microphone" description="Audio is captured locally. Only a finished utterance is uploaded to Groq Whisper for transcription.">
        <Row label="Input device" hint={devError ?? undefined}>
          <Select value={String(v.input_device ?? '')} onChange={(x) => void save({ voice: { input_device: x === '' ? null : Number(x) } })}
            options={[{ value: '', label: 'System default' }, ...devices.map((d) => ({ value: String(d.index), label: d.name }))]} className="w-64" />
        </Row>
        <Row label="Test microphone" hint={mic ? (mic.ok ? `Working · level ${Math.round((mic.level ?? 0) * 100)}%` : mic.error) : 'Checks the device and Windows microphone permission.'}>
          <Button size="sm" onClick={testMic} disabled={testing}>{testing ? 'Listening…' : 'Test'}</Button>
        </Row>
        <Row label="Noise reduction" hint="Lightweight spectral gate applied once to each utterance before upload.">
          <Toggle checked={v.noise_reduction} onChange={(x) => void save({ voice: { noise_reduction: x } })} />
        </Row>
        <Row label="Language" hint="English is tuned; Urdu and auto-detect are passed to Whisper as-is.">
          <Select value={v.language} onChange={(language) => void save({ voice: { language } })}
            options={[{ value: 'en', label: 'English' }, { value: 'ur', label: 'Urdu' }, { value: 'auto', label: 'Auto-detect' }]} />
        </Row>
      </Card>
      <Card title="Speech detection" description={`Voice activity detection: ${status?.vad === 'silero' ? 'Silero VAD (local ONNX model)' : 'energy-based fallback'}.`}>
        <Row label="Speech sensitivity" hint="Higher values ignore more background noise.">
          <Slider value={v.vad_threshold} min={0.2} max={0.9} step={0.05} onCommit={(x) => void save({ voice: { vad_threshold: x } })} format={(x) => x.toFixed(2)} />
        </Row>
        <Row label="End of speech after silence">
          <Slider value={v.silence_ms} min={400} max={3000} step={100} onCommit={(x) => void save({ voice: { silence_ms: x } })} format={(x) => `${(x / 1000).toFixed(1)}s`} />
        </Row>
        <Row label="Give up if no speech within">
          <Slider value={v.no_speech_timeout_s} min={2} max={20} step={1} onCommit={(x) => void save({ voice: { no_speech_timeout_s: x } })} format={(x) => `${x}s`} />
        </Row>
        <Row label="Maximum recording length">
          <Slider value={v.max_record_s} min={5} max={120} step={5} onCommit={(x) => void save({ voice: { max_record_s: x } })} format={(x) => `${x}s`} />
        </Row>
      </Card>
      <Card title="Wake word" description="Say “Hey Jarvis”, wait for “Yes?”, then speak. Detection runs entirely on this computer (openWakeWord model); the microphone stays open while enabled."
        actions={status?.wake_word_running ? <Badge tone="good">Listening for wake word</Badge> : status?.wake_word_error ? <Badge tone="bad">Unavailable</Badge> : null}>
        <Row label="Enable wake word" hint={status?.wake_word_error ?? 'Runs continuously while enabled; see Settings → Performance for its CPU cost.'}>
          <Toggle checked={v.wake_word_enabled} onChange={(x) => void save({ voice: { wake_word_enabled: x } })} />
        </Row>
        <Row label="Detection threshold" hint="Raise if it triggers by mistake; lower if it misses you.">
          <Slider value={v.wake_word_threshold} min={0.2} max={0.9} step={0.05} onCommit={(x) => void save({ voice: { wake_word_threshold: x } })} format={(x) => x.toFixed(2)} />
        </Row>
      </Card>
      <Card title="Spoken responses" description={status?.piper_available ? `Piper neural voice installed (${v.piper_voice}).` : 'Piper voice model not found; the Windows system voice will be used.'}>
        <Row label="Voice engine">
          <Select value={v.tts_engine} onChange={(tts_engine) => void save({ voice: { tts_engine } })}
            options={[{ value: 'piper', label: 'Piper (local neural)' }, { value: 'system', label: 'Windows system voice' }, { value: 'off', label: 'Off' }]} />
        </Row>
        <Row label="Speak replies to voice commands"><Toggle checked={v.speak_responses} onChange={(x) => void save({ voice: { speak_responses: x } })} /></Row>
        <Row label="Also speak replies to typed commands"><Toggle checked={v.speak_typed_responses} onChange={(x) => void save({ voice: { speak_typed_responses: x } })} /></Row>
        <Row label="Speaking rate">
          <Slider value={v.tts_rate} min={0.7} max={1.5} step={0.05} onCommit={(x) => void save({ voice: { tts_rate: x } })} format={(x) => `${x.toFixed(2)}×`} />
        </Row>
        <Row label="Test voice"><Button size="sm" onClick={() => void client.post('/api/voice/speak', { text: 'Hello. Jarvis voice output is working.' })}>Play sample</Button></Row>
      </Card>
    </>
  );
}

// ------------------------------------------------------------ AI providers
const TIERS = [
  { key: 'simple', label: 'Simple commands', hint: 'Short single-step requests routed to the LLM.' },
  { key: 'complex', label: 'Complex reasoning & tools', hint: 'Multi-step tasks, planning and tool calling.' },
  { key: 'summarization', label: 'Summarization', hint: 'summarize_emails and other bulk summaries.' },
  { key: 'transcription', label: 'Speech-to-text', hint: 'Whisper model for voice transcription.' },
] as const;

export function ModelsSection() {
  const [s, save] = useSettings();
  const { state } = useStore();
  const p = s.providers;
  const [models, setModels] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => client.get('/api/models').then((r) => { setModels(r.models); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { void load(); }, [state.providersVersion]);

  const limits = p.local_limits;
  const modelNames = Array.from(new Set([p.models.simple, p.models.complex, p.models.summarization, p.models.transcription]));

  return (
    <>
      <Card title="Model preferences" description={models?.length ? `${models.length} models reported by Groq for your keys.` : error ?? 'Add a Groq key to load the models your account can use. You can still type a model id.'}
        actions={<Button size="sm" variant="ghost" onClick={() => void load()}>Refresh</Button>}>
        {TIERS.map((t) => {
          const value = p.models[t.key];
          const unavailable = models && models.length > 0 && !models.includes(value);
          return (
            <Row key={t.key} label={<span className="flex items-center gap-2">{t.label}{unavailable && <Badge tone="warn">not available</Badge>}</span>} hint={t.hint}>
              <div className="flex items-center gap-2">
                {models && models.length > 0 ? (
                  <Select value={value} className="w-64" onChange={(m) => void save({ providers: { models: { [t.key]: m } } })}
                    options={[...(models.includes(value) ? [] : [{ value, label: `${value} (current)` }]),
                      ...models.filter((m) => (t.key === 'transcription') === /whisper/i.test(m)).map((m) => ({ value: m, label: m }))]} />
                ) : (
                  <CommitInput value={value} mono className="w-64" onCommit={(m) => void save({ providers: { models: { [t.key]: m } } })} />
                )}
              </div>
            </Row>
          );
        })}
      </Card>
      <Card title="Reliability">
        <Row label="Request timeout"><Slider value={p.request_timeout_s} min={10} max={180} step={5} onCommit={(x) => void save({ providers: { request_timeout_s: x } })} format={(x) => `${x}s`} /></Row>
        <Row label="Max attempts per request" hint="Across keys, with exponential backoff on 5xx/timeouts.">
          <Slider value={p.max_attempts} min={1} max={8} step={1} onCommit={(x) => void save({ providers: { max_attempts: x } })} />
        </Row>
        <Row label="Wait for a quota window to reopen" hint="If every key is frozen but one recovers within this time (typical for per-minute token limits), wait instead of failing.">
          <Slider value={p.max_wait_s} min={0} max={60} step={5} onCommit={(x) => void save({ providers: { max_wait_s: x } })} format={(x) => `${x}s`} />
        </Row>
        <Row label="Reasoning effort (gpt-oss models)" hint="Lower is faster and uses fewer tokens.">
          <Select value={p.reasoning_effort} onChange={(reasoning_effort) => void save({ providers: { reasoning_effort } })}
            options={[{ value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' }]} />
        </Row>
      </Card>
      <Card title="Locally estimated limits"
        description="Groq reports RPD and TPM in response headers. RPM, TPD, audio-seconds-per-hour (ASH) and per-day (ASD) are not reported, so Jarvis estimates them from its own usage against the limits you enter here (copy them from console.groq.com → Settings → Limits). Leave blank to skip.">
        <table className="w-full text-[12.5px]">
          <thead className="text-left text-[11px] tracking-wide text-faint uppercase"><tr><th className="py-1.5 font-medium">Model</th>{['rpm', 'tpd', 'ash', 'asd'].map((k) => <th key={k} className="px-1 font-medium">{k}</th>)}</tr></thead>
          <tbody>
            {modelNames.map((m) => (
              <tr key={m} className="border-t border-ink-700/70">
                <td className="py-2 pr-2 font-mono text-[12px] text-muted">{m}</td>
                {(['rpm', 'tpd', 'ash', 'asd'] as const).map((k) => (
                  <td key={k} className="px-1">
                    <CommitInput type="number" value={limits[m]?.[k] ?? ''} className="w-24" placeholder="—"
                      onCommit={(x) => {
                        const cur = limits[m] ?? { rpm: null, tpd: null, ash: null, asd: null };
                        void save({ providers: { local_limits: { ...limits, [m]: { ...cur, [k]: x === '' ? null : num(x, 0) } } } });
                      }} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

// ----------------------------------------------------------------- browser
export function BrowserSection() {
  const [s, save] = useSettings();
  const b = s.browser;
  return (
    <>
      <Card title="Automation browser" description="Jarvis drives a dedicated browser profile stored in its own data folder. Your normal browser profile, cookies and saved passwords are never used.">
        <Row label="Browser" hint="Uses the copy already installed on Windows; nothing is downloaded.">
          <Select value={b.channel} onChange={(channel) => void save({ browser: { channel } })}
            options={[{ value: 'msedge', label: 'Microsoft Edge' }, { value: 'chrome', label: 'Google Chrome' }, { value: 'chromium', label: 'Playwright Chromium (if installed)' }]} />
        </Row>
        <Row label="Run headless" hint="Hide the automation browser window."><Toggle checked={b.headless} onChange={(x) => void save({ browser: { headless: x } })} /></Row>
        <Row label="Search engine">
          <Select value={b.search_engine} onChange={(search_engine) => void save({ browser: { search_engine } })}
            options={[{ value: 'google', label: 'Google' }, { value: 'bing', label: 'Bing' }, { value: 'duckduckgo', label: 'DuckDuckGo' }]} />
        </Row>
      </Card>
      <Card title="Use my existing Chrome session" description="Off by default. When enabled, Jarvis attaches to a Chrome you started yourself with remote debugging, which gives it access to every logged-in site in that Chrome. Start Chrome with: chrome.exe --remote-debugging-port=9222 --user-data-dir=<separate folder>">
        <Row label="Attach over CDP"><Toggle checked={b.use_existing_chrome} onChange={(x) => void save({ browser: { use_existing_chrome: x } })} /></Row>
        <Row label="DevTools endpoint"><CommitInput value={b.cdp_url} mono className="w-64" onCommit={(cdp_url) => void save({ browser: { cdp_url } })} /></Row>
      </Card>
    </>
  );
}

// -------------------------------------------------------------- automation
export function AutomationSection() {
  const [s, save] = useSettings();
  const a = s.automation;
  const perf = s.performance;
  return (
    <>
      <Card title="Agent loop">
        <Row label="Maximum tool-calling steps per request"><Slider value={a.max_iterations} min={2} max={20} step={1} onCommit={(x) => void save({ automation: { max_iterations: x } })} /></Row>
        <Row label="Run independent read-only tools in parallel"><Toggle checked={a.parallel_tools} onChange={(x) => void save({ automation: { parallel_tools: x } })} /></Row>
        <Row label="Concurrent tasks"><Slider value={a.max_concurrent_tasks} min={1} max={4} step={1} onCommit={(x) => void save({ automation: { max_concurrent_tasks: x } })} /></Row>
        <Row label="Default tool timeout"><Slider value={a.tool_timeout_s} min={10} max={300} step={10} onCommit={(x) => void save({ automation: { tool_timeout_s: x } })} format={(x) => `${x}s`} /></Row>
      </Card>
      <Card title="Commands">
        <Row label="Command timeout" hint="run_command processes are killed (with children) after this."><Slider value={a.command_timeout_s} min={10} max={1800} step={10} onCommit={(x) => void save({ automation: { command_timeout_s: x } })} format={(x) => `${x}s`} /></Row>
        <Row label="Captured output limit per process"><Slider value={a.max_output_kb} min={32} max={2048} step={32} onCommit={(x) => void save({ automation: { max_output_kb: x } })} format={(x) => `${x} KB`} /></Row>
      </Card>
      <Card title="Context" description="How much conversation history is sent with each request. Larger values remember more but use more tokens.">
        <Row label="History turns"><Slider value={perf.history_messages} min={2} max={60} step={1} onCommit={(x) => void save({ performance: { history_messages: x } })} /></Row>
        <Row label="Context budget (characters)"><Slider value={perf.context_char_budget} min={4000} max={100000} step={2000} onCommit={(x) => void save({ performance: { context_char_budget: x } })} format={(x) => `${Math.round(x / 1000)}k`} /></Row>
        <Row label="Tool result limit (characters)"><Slider value={perf.tool_result_char_limit} min={2000} max={40000} step={1000} onCommit={(x) => void save({ performance: { tool_result_char_limit: x } })} format={(x) => `${Math.round(x / 1000)}k`} /></Row>
      </Card>
    </>
  );
}

// ------------------------------------------------------------------- about
export function AboutSection() {
  const [info, setInfo] = useState<{ version: string; electron: string; packaged: boolean } | null>(null);
  const [state, setState] = useState<any>(null);
  useEffect(() => {
    void bridge().version().then(setInfo);
    void client.get('/api/state').then(setState);
  }, []);
  return (
    <Card>
      <div className="flex items-center gap-4 pb-4">
        <Logo size={64} />
        <div>
          <div className="font-display text-[17px] font-semibold tracking-[0.12em]">JARVIS</div>
          <div className="text-[12.5px] text-muted">Local-first desktop AI agent</div>
        </div>
      </div>
      <Row label="App version"><span className="font-mono text-[12.5px] text-muted">{info?.version}</span></Row>
      <Row label="Backend version"><span className="font-mono text-[12.5px] text-muted">{state?.version}</span></Row>
      <Row label="Electron"><span className="font-mono text-[12.5px] text-muted">{info?.electron}</span></Row>
      <Row label="Build"><span className="text-[12.5px] text-muted">{info?.packaged ? 'Installed' : 'Development'}</span></Row>
      <Row label="Data folder" hint={state?.data_dir}><Button size="sm" onClick={() => void bridge().openFolder('data')}>Open</Button></Row>
      <Row label="Speech & AI" hint="Groq (LLM and Whisper STT, cloud), Piper TTS, Silero VAD and openWakeWord (local)."><span /></Row>
    </Card>
  );
}
