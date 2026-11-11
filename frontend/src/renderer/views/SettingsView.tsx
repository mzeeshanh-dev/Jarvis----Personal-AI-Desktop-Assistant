import { Activity, Bot, Globe, Info, KeyRound, Mail, Mic, ScrollText, Settings2, Shield, Workflow } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cx, Spinner } from '../components/ui';
import { useStore } from '../lib/store';
import { AboutSection, AutomationSection, BrowserSection, GeneralSection, ModelsSection, VoiceSection } from '../settings/Sections';
import { GroqKeysSection } from '../settings/GroqKeys';
import { PermissionsSection } from '../settings/Permissions';
import { LogsSection, PerformanceSection } from '../settings/Diagnostics';
import { GmailSection } from '../settings/Gmail';

const SECTIONS = [
  { id: 'general', label: 'General', icon: Settings2, C: GeneralSection },
  { id: 'voice', label: 'Voice', icon: Mic, C: VoiceSection },
  { id: 'providers', label: 'AI providers', icon: Bot, C: ModelsSection },
  { id: 'groq', label: 'Groq API keys', icon: KeyRound, C: GroqKeysSection },
  { id: 'permissions', label: 'Permissions', icon: Shield, C: PermissionsSection },
  { id: 'browser', label: 'Browser', icon: Globe, C: BrowserSection },
  { id: 'automation', label: 'Desktop automation', icon: Workflow, C: AutomationSection },
  { id: 'gmail', label: 'Gmail', icon: Mail, C: GmailSection },
  { id: 'performance', label: 'Performance', icon: Activity, C: PerformanceSection },
  { id: 'logs', label: 'Logs & diagnostics', icon: ScrollText, C: LogsSection },
  { id: 'about', label: 'About', icon: Info, C: AboutSection },
] as const;

export function SettingsView({ initial }: { initial?: string }) {
  const { state } = useStore();
  const [active, setActive] = useState<string>(initial && SECTIONS.some((s) => s.id === initial) ? initial : 'general');
  useEffect(() => { if (initial && SECTIONS.some((s) => s.id === initial)) setActive(initial); }, [initial]);
  const section = SECTIONS.find((s) => s.id === active)!;
  return (
    <div className="flex min-h-0 flex-1">
      <nav className="w-52 shrink-0 overflow-y-auto border-r border-ink-800 px-3 py-5">
        <div className="px-2.5 pb-3 font-display text-[18px] font-semibold">Settings</div>
        {SECTIONS.map((s) => (
          <button key={s.id} onClick={() => setActive(s.id)}
            className={cx('flex h-8 w-full items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors',
              active === s.id ? 'bg-ink-750 text-fg' : 'text-muted hover:bg-ink-850 hover:text-fg')}>
            <s.icon size={14} className={active === s.id ? 'text-accent' : ''} /> {s.label}
          </button>
        ))}
      </nav>
      <div className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          <h2 className="font-display text-[18px] font-semibold">{section.label}</h2>
          {state.settings && state.connected
            ? <section.C />
            : <div className="flex items-center gap-2 text-[13px] text-muted"><Spinner /> Waiting for the backend…</div>}
        </div>
      </div>
    </div>
  );
}
