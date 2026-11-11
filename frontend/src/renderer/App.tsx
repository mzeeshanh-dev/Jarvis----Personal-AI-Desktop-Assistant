import { History, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, Power, Settings as SettingsIcon, Sparkles, Zap } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApprovalDialog, Toasts } from './components/Chat';
import { Emblem } from './components/Emblem';
import { Button, cx } from './components/ui';
import { bridge } from './lib/bridge';
import { timeAgo } from './lib/status';
import { useStore } from './lib/store';
import { AssistantView } from './views/AssistantView';
import { HistoryView } from './views/HistoryView';
import { SettingsView } from './views/SettingsView';

export type Route = 'assistant' | 'history' | 'settings';

/** Bottom-of-sidebar power control: graceful shutdown or emergency stop. */
function ShutdownControl({ collapsed }: { collapsed: boolean }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  return (
    <>
      <div className={cx('border-t border-ink-800 p-3', collapsed && 'flex justify-center px-2')}>
        <button onClick={() => setOpen(true)} title="Shut down Jarvis" aria-label="Shut down Jarvis"
          className={cx('flex h-9 items-center gap-2.5 rounded-lg text-[13px] text-muted transition-colors hover:bg-danger/10 hover:text-danger',
            collapsed ? 'w-9 justify-center' : 'w-full px-2.5')}>
          <Power size={15} className="shrink-0" />{!collapsed && 'Shut down Jarvis'}
        </button>
      </div>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-6" onClick={() => setOpen(false)}>
          <div role="dialog" aria-labelledby="shutdown-title" onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md rounded-2xl border border-ink-600 bg-ink-800 p-5 shadow-2xl">
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-xl bg-danger/15 text-danger"><Power size={18} /></div>
              <h2 id="shutdown-title" className="text-[15px] font-semibold">Shut down Jarvis?</h2>
            </div>
            <p className="mt-3 text-[13px] leading-relaxed text-muted">
              Jarvis stops listening, the microphone is released, running tasks are cancelled and the backend closes.
              Nothing runs until you open Jarvis again.
            </p>
            <p className="mt-2 text-[12px] text-faint">Emergency stop kills everything immediately, including commands Jarvis started. Shortcut anywhere: <kbd className="rounded border border-ink-600 px-1 font-mono">Ctrl+Alt+Shift+X</kbd></p>
            <div className="mt-5 flex justify-end gap-2">
              <Button onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="danger" onClick={() => void bridge().shutdown(true)}><Zap size={14} /> Emergency stop</Button>
              <Button variant="primary" autoFocus onClick={() => void bridge().shutdown(false)}><Power size={14} /> Shut down</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function BackendBanner() {
  const { state } = useStore();
  const b = state.backend;
  if (!b || b.status === 'ready') return null;
  const tone = b.status === 'failed' ? 'bg-danger/10 text-danger border-danger/20' : 'bg-ink-800 text-muted border-ink-700';
  return (
    <div className={cx('mx-4 mt-2 flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-[12.5px]', tone)}>
      <span>{b.status === 'starting' ? 'Starting Jarvis services…' : b.status === 'restarting' ? b.error ?? 'Restarting services…' : b.error ?? 'Backend stopped.'}</span>
      {b.status === 'failed' && <button className="font-medium underline" onClick={() => void bridge().restartBackend()}>Retry</button>}
    </div>
  );
}

export function App() {
  const { state, actions } = useStore();
  const [route, setRoute] = useState<Route>('assistant');
  const [section, setSection] = useState<string | undefined>();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('jarvis.sidebar') === 'collapsed'; } catch { return false; }
  });
  const toggleSidebar = () => setCollapsed((c) => {
    try { localStorage.setItem('jarvis.sidebar', c ? 'expanded' : 'collapsed'); } catch { /* storage unavailable */ }
    return !c;
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') { e.preventDefault(); toggleSidebar(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    try {
      return bridge().on('navigate', (r: string) => {
        if (r.startsWith('/settings')) { setRoute('settings'); setSection(r.split('/')[2]); }
        else setRoute('assistant');
      });
    } catch { return undefined; }
  }, []);

  const nav = [
    { id: 'assistant' as const, label: 'Assistant', icon: Sparkles },
    { id: 'history' as const, label: 'History', icon: History },
    { id: 'settings' as const, label: 'Settings', icon: SettingsIcon },
  ];
  const connected = state.connected;

  return (
    <div className="flex h-full flex-col">
      <header className="drag flex h-9.5 shrink-0 items-center gap-2.5 border-b border-ink-800 pl-4">
        <Emblem size={18} />
        <span className="font-display text-[13px] font-semibold tracking-[0.18em] text-fg">JARVIS</span>
        <span className={cx('ml-1 size-1.5 rounded-full', connected ? 'bg-accent' : 'bg-warm animate-breathe')} title={connected ? 'Connected' : 'Connecting'} />
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className={cx('flex shrink-0 flex-col border-r border-ink-800 bg-ink-900 transition-[width] duration-200', collapsed ? 'w-16' : 'w-60')}>
          <div className={cx('flex gap-2 p-3', collapsed && 'flex-col items-center')}>
            <button title="New conversation" aria-label="New conversation" onClick={() => { void actions.selectConversation(null); setRoute('assistant'); }}
              className={cx('flex h-9 items-center gap-2 rounded-lg border border-ink-700 bg-ink-850 text-[13px] text-fg hover:border-ink-600 hover:bg-ink-800',
                collapsed ? 'w-9 justify-center' : 'min-w-0 flex-1 px-3')}>
              <MessageSquarePlus size={15} className="shrink-0 text-accent" />{!collapsed && <span className="truncate">New conversation</span>}
            </button>
            <button onClick={toggleSidebar} title={collapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)'} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              className="grid size-9 shrink-0 place-items-center rounded-lg text-muted hover:bg-ink-800 hover:text-fg">
              {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </div>
          <nav className={cx('flex flex-col gap-0.5', collapsed ? 'items-center px-2' : 'px-3')}>
            {nav.map((n) => (
              <button key={n.id} onClick={() => setRoute(n.id)} title={collapsed ? n.label : undefined} aria-label={n.label}
                className={cx('flex h-8.5 items-center gap-2.5 rounded-lg text-[13px] transition-colors',
                  collapsed ? 'w-9 justify-center' : 'px-2.5',
                  route === n.id ? 'bg-ink-750 text-fg' : 'text-muted hover:bg-ink-800 hover:text-fg')}>
                <n.icon size={15} className={cx('shrink-0', route === n.id && 'text-accent')} />{!collapsed && n.label}
              </button>
            ))}
          </nav>
          {!collapsed && (
            <>
              <div className="mt-5 px-5 pb-1 text-[11px] font-medium tracking-wider text-faint uppercase">Recent</div>
              <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
                {state.conversations.slice(0, 40).map((c) => (
                  <button key={c.id} onClick={() => { void actions.selectConversation(c.id); setRoute('assistant'); }}
                    className={cx('group flex w-full flex-col rounded-lg px-2.5 py-1.5 text-left transition-colors',
                      state.conversationId === c.id && route === 'assistant' ? 'bg-ink-750' : 'hover:bg-ink-800')}>
                    <span className="truncate text-[12.5px] text-fg/90">{c.title}</span>
                    <span className="text-[11px] text-faint">{timeAgo(c.updated_at * 1000)}</span>
                  </button>
                ))}
                {!state.conversations.length && <div className="px-2.5 py-2 text-[12px] text-faint">No conversations yet.</div>}
              </div>
            </>
          )}
          {collapsed && <div className="flex-1" />}
          <ShutdownControl collapsed={collapsed} />
        </aside>
        <main className="flex min-w-0 flex-1 flex-col bg-ink-950">
          <BackendBanner />
          {route === 'assistant' && <AssistantView onOpenSettings={(s) => { setSection(s); setRoute('settings'); }} />}
          {route === 'history' && <HistoryView onOpen={() => setRoute('assistant')} />}
          {route === 'settings' && <SettingsView initial={section} />}
        </main>
      </div>
      <ApprovalDialog />
      <Toasts />
    </div>
  );
}
