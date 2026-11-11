import { AnimatePresence, motion } from 'motion/react';
import { ArrowUp, Check, ChevronDown, CircleSlash, Mic, Pause, Play, RotateCcw, Square, Volume2, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { bridge } from '../lib/bridge';
import { Markdown } from '../lib/markdown';
import { playback } from '../lib/playback';
import { phaseLabel, toolLabel } from '../lib/status';
import { useStore } from '../lib/store';
import type { ActiveTask, ChatMessage, ToolRun } from '../lib/types';
import { Badge, Button, IconButton, Spinner, cx } from './ui';
import { Equalizer } from './VoiceOrb';

const openLink = (url: string) => { void bridge().openExternal(url).catch(() => {}); };

export function ToolRuns({ tools }: { tools: ToolRun[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!tools.length) return null;
  return (
    <div className="mb-2 flex flex-col gap-1">
      {tools.map((t) => (
        <div key={t.callId} className="rounded-lg border border-ink-700 bg-ink-900/60">
          <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left" onClick={() => setOpen(open === t.callId ? null : t.callId)}>
            <span className={cx('grid size-4 place-items-center rounded-full', t.running ? 'text-accent' : t.ok === false ? 'text-danger' : 'text-accent')}>
              {t.running ? <Spinner className="size-3" /> : t.ok === false ? <X size={12} /> : <Check size={12} />}
            </span>
            <span className="text-[12px] font-medium text-fg">{toolLabel(t.tool)}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-muted">{t.summary ?? (t.running ? 'running…' : '')}</span>
            {t.durationMs != null && <span className="font-mono text-[11px] text-faint">{t.durationMs < 1000 ? `${t.durationMs}ms` : `${(t.durationMs / 1000).toFixed(1)}s`}</span>}
            <ChevronDown size={13} className={cx('text-faint transition-transform', open === t.callId && 'rotate-180')} />
          </button>
          {open === t.callId && t.arguments && (
            <pre className="selectable max-h-48 overflow-auto border-t border-ink-700 px-2.5 py-2 font-mono text-[11.5px] text-muted">{JSON.stringify(t.arguments, null, 2)}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  if (m.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="selectable max-w-[78%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink-700 px-3.5 py-2 text-[13.5px] leading-relaxed text-fg">{m.content}</div>
      </div>
    );
  }
  return (
    <div className="flex max-w-[88%] flex-col">
      <ToolRuns tools={m.tools} />
      <div className={cx('selectable text-[13.5px] leading-relaxed', m.status === 'failed' ? 'text-danger/90' : m.status === 'cancelled' ? 'text-muted' : 'text-fg')}>
        <Markdown text={m.content} onLink={openLink} />
      </div>
    </div>
  );
}

function LiveTask({ task }: { task: ActiveTask }) {
  const { state, actions } = useStore();
  const draft = state.drafts[task.id];
  const tools = state.taskTools[task.id] ?? [];
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex max-w-[88%] flex-col">
      <ToolRuns tools={tools} />
      {draft?.text && <div className="selectable mb-2 text-[13.5px] leading-relaxed text-fg"><Markdown text={draft.text} onLink={openLink} /></div>}
      <div className="flex items-center gap-2 text-[12.5px] text-muted">
        <Spinner className="size-3 text-accent" />
        <span>{phaseLabel(task.phase, task.detail)}</span>
        <button className="ml-1 rounded px-1.5 text-faint hover:text-danger" onClick={() => actions.cancelTask(task.id)}>Stop</button>
      </div>
    </motion.div>
  );
}

export function MessageList({ empty }: { empty: React.ReactNode }) {
  const { state } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const tasks = Object.values(state.tasks).filter((t) => t.conversationId === state.conversationId);
  const draftLen = Object.values(state.drafts).reduce((n, d) => n + d.text.length, 0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.messages.length, tasks.length, draftLen, state.taskTools]);
  if (!state.messages.length && !tasks.length) return <div className="flex-1 overflow-y-auto">{empty}</div>;
  return (
    <div ref={ref} className="flex-1 overflow-y-auto px-8 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-5">
        {state.messages.map((m) => <Bubble key={m.id} m={m} />)}
        {tasks.map((t) => <LiveTask key={t.id} task={t} />)}
      </div>
    </div>
  );
}

export function Composer({ autoFocus = true }: { autoFocus?: boolean }) {
  const { state, actions } = useStore();
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  const busy = Object.keys(state.tasks).length > 0;
  const listening = state.voice.state === 'listening';
  const speaking = state.playback.status !== 'idle';

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = '0px';
    el.style.height = `${Math.min(160, el.scrollHeight)}px`;
  }, [text]);

  const submit = () => {
    if (!text.trim()) return;
    actions.send(text);
    setText('');
  };

  return (
    <div className="px-8 pb-5">
      <div className="mx-auto max-w-3xl">
        <div className={cx('flex items-end gap-2 rounded-2xl border bg-ink-900/75 p-2 backdrop-blur-md transition-colors', listening ? 'border-accent/60 shadow-[0_0_20px_rgba(79,209,245,0.18)]' : 'border-ink-600/70 focus-within:border-accent/40')}>
          <IconButton label={listening ? 'Stop listening' : 'Speak (push-to-talk)'} active={listening} onClick={actions.toggleVoice}
            className={cx('size-9 shrink-0 rounded-xl', listening && 'bg-accent/15! text-accent!')}>
            {listening ? <Equalizer active /> : <Mic size={17} />}
          </IconButton>
          <textarea ref={ref} rows={1} value={text} autoFocus={autoFocus} placeholder={listening ? 'Listening…' : 'Ask Jarvis to do something…'}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
            className="max-h-40 min-h-9 flex-1 resize-none bg-transparent px-1 py-2 text-[13.5px] leading-snug outline-none placeholder:text-faint" />
          {(busy || speaking || listening) && (
            <IconButton label="Stop everything" onClick={actions.stopAll} className="size-9 shrink-0 rounded-xl text-danger! hover:bg-danger/10!">
              <Square size={15} fill="currentColor" />
            </IconButton>
          )}
          <button aria-label="Send" disabled={!text.trim()} onClick={submit}
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent text-ink-950 transition-opacity disabled:opacity-30">
            <ArrowUp size={17} strokeWidth={2.4} />
          </button>
        </div>
      </div>
    </div>
  );
}

export function PlaybackBar() {
  const { state } = useStore();
  const p = state.playback;
  if (p.status === 'idle' && !playback.canReplay) return null;
  return (
    <div className="flex items-center gap-1 rounded-full border border-ink-700 bg-ink-850 py-1 pr-3 pl-1">
      {p.status === 'playing'
        ? <IconButton label="Pause" className="size-7" onClick={() => playback.pause()}><Pause size={14} /></IconButton>
        : p.status === 'paused'
          ? <IconButton label="Resume" className="size-7" onClick={() => playback.resume()}><Play size={14} /></IconButton>
          : <IconButton label="Replay last response" className="size-7" onClick={() => playback.replay()}><RotateCcw size={14} /></IconButton>}
      <IconButton label="Stop speaking" className="size-7" disabled={p.status === 'idle'} onClick={() => playback.stop()}><Square size={12} /></IconButton>
      <Volume2 size={13} className={cx('ml-1', p.status === 'playing' ? 'text-accent' : 'text-faint')} />
      <span className="max-w-52 truncate text-[12px] text-muted">{p.status === 'idle' ? 'Last response' : p.text}</span>
    </div>
  );
}

const LEVEL_TONE = (l: number) => (l >= 5 ? 'bad' : l >= 4 ? 'warn' : 'accent') as 'bad' | 'warn' | 'accent';

export function ApprovalDialog() {
  const { state, actions } = useStore();
  const a = state.approvals[0];
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!a) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [a]);
  useEffect(() => {
    if (!a) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') actions.respondApproval(a.id, false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [a, actions]);
  const remaining = a ? Math.max(0, Math.round(a.timeout_s - (now - a.receivedAt) / 1000)) : 0;
  return (
    <AnimatePresence>
      {a && (
        <motion.div className="fixed inset-0 z-40 grid place-items-center bg-black/55 p-6" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <motion.div role="alertdialog" aria-labelledby="approval-title" className="w-full max-w-lg rounded-2xl border border-ink-600 bg-ink-800 shadow-2xl"
            initial={{ scale: 0.97, y: 8 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.98, opacity: 0 }}>
            <div className="flex items-start gap-3 px-5 pt-5">
              <div className={cx('grid size-9 shrink-0 place-items-center rounded-xl', a.level >= 5 ? 'bg-danger/15 text-danger' : 'bg-warm/15 text-warm')}>
                <CircleSlash size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h2 id="approval-title" className="text-[15px] font-semibold">Allow this action?</h2>
                  <Badge tone={LEVEL_TONE(a.level)}>Level {a.level} · {a.level_name}</Badge>
                </div>
                <p className="mt-1 text-[13.5px] text-fg">{a.summary}</p>
                <p className="mt-1 text-[12.5px] text-muted">{a.reason}</p>
              </div>
            </div>
            <pre className="selectable mx-5 mt-4 max-h-56 overflow-auto rounded-lg border border-ink-700 bg-ink-900 p-3 font-mono text-[11.5px] text-muted">{JSON.stringify(a.arguments, null, 2)}</pre>
            <div className="flex items-center justify-between gap-3 px-5 py-4">
              <span className="text-[12px] text-faint">Auto-denies in {remaining}s{state.approvals.length > 1 ? ` · ${state.approvals.length - 1} more waiting` : ''}</span>
              <div className="flex gap-2">
                <Button onClick={() => actions.respondApproval(a.id, false)}>Deny</Button>
                <Button variant={a.level >= 5 ? 'danger' : 'primary'} onClick={() => actions.respondApproval(a.id, true)}>Allow</Button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Toasts() {
  const { state, actions } = useStore();
  useEffect(() => {
    if (!state.toasts.length) return;
    const t = setTimeout(() => actions.dismissToast(state.toasts[0].id), 5000);
    return () => clearTimeout(t);
  }, [state.toasts, actions]);
  return (
    <div className="pointer-events-none fixed right-5 bottom-5 z-50 flex flex-col gap-2">
      <AnimatePresence>
        {state.toasts.map((t) => (
          <motion.div key={t.id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className={cx('pointer-events-auto flex max-w-sm items-start gap-2 rounded-xl border px-3.5 py-2.5 text-[12.5px] shadow-xl',
              t.kind === 'error' ? 'border-danger/30 bg-ink-800 text-fg' : 'border-ink-600 bg-ink-800 text-fg')}>
            <span className={cx('mt-1 size-1.5 shrink-0 rounded-full', t.kind === 'error' ? 'bg-danger' : t.kind === 'success' ? 'bg-accent' : 'bg-muted')} />
            <span className="flex-1">{t.text}</span>
            <button className="text-faint hover:text-fg" onClick={() => actions.dismissToast(t.id)}><X size={13} /></button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
