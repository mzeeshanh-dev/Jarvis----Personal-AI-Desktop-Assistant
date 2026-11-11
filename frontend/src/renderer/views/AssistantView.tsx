import { AppWindow, FileSearch, Globe, KeyRound, Mail, TerminalSquare } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Composer, MessageList, PlaybackBar } from '../components/Chat';
import { ORB_LABEL, VoiceOrb } from '../components/VoiceOrb';
import { Button, cx } from '../components/ui';
import { client } from '../lib/api';
import { bridge } from '../lib/bridge';
import { orbState, phaseLabel } from '../lib/status';
import { useStore } from '../lib/store';

const EXAMPLES = [
  { icon: AppWindow, text: 'Open VS Code' },
  { icon: Globe, text: 'Search Google for the Ryzen 5 5500U benchmarks' },
  { icon: FileSearch, text: 'Find PDFs in my Downloads from this week and summarize the newest one' },
  { icon: Mail, text: 'Find GreenBot emails and tell me which tasks are pending' },
  { icon: TerminalSquare, text: 'Run git status in my project folder on the Desktop' },
];

function shortcutLabel(s: string | undefined) {
  return (s ?? 'Control+Space').replace('Control', 'Ctrl').split('+').join(' + ');
}

function Welcome({ onOpenSettings }: { onOpenSettings: (s: string) => void }) {
  const { state, actions } = useStore();
  const [keys, setKeys] = useState<number | null>(null);
  useEffect(() => {
    if (!state.connected) return;
    void client.get('/api/state').then((s) => setKeys(s.providers_configured)).catch(() => {});
  }, [state.connected, state.providersVersion]);
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-8 pt-[8vh] pb-8 text-center">
      <h1 className="font-display text-[26px] font-semibold tracking-tight drop-shadow-[0_2px_12px_rgba(0,0,0,0.8)]">
        {state.settings?.general.user_name ? `Hello, ${state.settings.general.user_name}.` : 'How can I help?'}
      </h1>
      <p className="mt-2 text-[13.5px] text-muted">
        Press <kbd className="rounded-md border border-ink-600 bg-ink-800 px-1.5 py-0.5 font-mono text-[12px] text-fg">{shortcutLabel(state.settings?.general.shortcut)}</kbd> anywhere and speak, or type below.
      </p>
      {keys === 0 && (
        <div className="mt-6 flex w-full items-center justify-between gap-4 rounded-xl border border-warm/25 bg-warm/5 px-4 py-3 text-left">
          <div className="flex items-center gap-3">
            <KeyRound size={18} className="shrink-0 text-warm" />
            <div>
              <div className="text-[13px] font-medium">Add a Groq API key to enable the AI agent and voice transcription</div>
              <div className="text-[12px] text-muted">Simple commands like “open Chrome” already work without one.</div>
            </div>
          </div>
          <Button variant="primary" size="sm" onClick={() => onOpenSettings('groq')}>Add key</Button>
        </div>
      )}
      <div className="mt-8 grid w-full grid-cols-1 gap-2 text-left">
        {EXAMPLES.map((e) => (
          <button key={e.text} onClick={() => actions.send(e.text)}
            className="flex items-center gap-3 rounded-xl border border-accent/15 bg-ink-900/55 px-4 py-3 text-[13px] text-fg/90 backdrop-blur-md transition-colors hover:border-accent/40 hover:bg-ink-850/70">
            <e.icon size={16} className="shrink-0 text-accent" /> {e.text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function AssistantView({ onOpenSettings }: { onOpenSettings: (section: string) => void }) {
  const { state } = useStore();
  const orb = orbState(state);
  const tasks = Object.values(state.tasks);
  const current = tasks[0];
  const status = current ? phaseLabel(current.phase, current.detail) : state.voice.hint ?? ORB_LABEL[orb];
  const [shortcutError, setShortcutError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const b = bridge();
      void b.shortcutStatus().then((s) => setShortcutError(s.error));
      return b.on('shortcut:status', (s) => setShortcutError(s.error));
    } catch { return undefined; }
  }, []);

  const hasChat = state.messages.length > 0 || tasks.length > 0;
  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Artwork backdrop: vivid on the welcome screen, dimmed behind conversations. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-cover bg-center transition-opacity duration-700"
        style={{ backgroundImage: 'url(./brand/hero.jpg)', opacity: hasChat ? 0.16 : 0.62 }} />
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-linear-to-b from-ink-950/70 via-transparent to-ink-950/90" />
      <div className="relative z-10 flex items-center gap-4 border-b border-ink-800/80 bg-ink-950/40 px-8 py-3 backdrop-blur-sm">
        <VoiceOrb state={orb} level={state.voice.level} size={52} />
        <div className="min-w-0 flex-1">
          <div className={cx('text-[14px] font-medium', orb === 'awaiting' ? 'text-warm' : orb === 'error' ? 'text-danger' : 'text-fg')}>{ORB_LABEL[orb]}</div>
          <div className="truncate text-[12.5px] text-muted">
            {orb === 'listening' ? 'Speak now. I’ll stop when you pause.'
              : state.voice.transcript && orb === 'thinking' ? `“${state.voice.transcript}”`
                : orb === 'idle' && !state.voice.hint ? `Press ${shortcutLabel(state.settings?.general.shortcut)} to speak`
                  : status}
            {state.voice.wakeWord && orb === 'idle' && ' · Say “Hey Jarvis”'}
          </div>
        </div>
        <PlaybackBar />
      </div>
      {shortcutError && (
        <div className="relative z-10 mx-8 mt-3 flex items-center justify-between rounded-lg border border-warm/25 bg-ink-900/80 px-3 py-2 text-[12.5px] text-warm">
          <span>{shortcutError}</span>
          <button className="underline" onClick={() => onOpenSettings('general')}>Change shortcut</button>
        </div>
      )}
      <div className="relative z-10 flex min-h-0 flex-1 flex-col">
        <MessageList empty={<Welcome onOpenSettings={onOpenSettings} />} />
        <Composer />
      </div>
    </div>
  );
}
