import { FolderPlus, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Badge, Button, Card, Row, Select, Slider, Toggle } from '../components/ui';
import { client } from '../lib/api';
import { toolLabel } from '../lib/status';
import { useSettings } from './Sections';

const LEVELS = [
  { n: 0, name: 'Read-only', policy: 'Automatic', ex: 'list apps, read web pages, search email' },
  { n: 1, name: 'Open apps & websites', policy: 'Automatic', ex: 'open Chrome, open a URL' },
  { n: 2, name: 'Read local files', policy: 'Automatic inside workspace folders', ex: 'search files, read a PDF' },
  { n: 3, name: 'Create & modify', policy: 'Your choice below', ex: 'create/edit/move files, click in browser, draft email' },
  { n: 4, name: 'Run commands', policy: 'Ask (allowlisted commands may run automatically)', ex: 'git, npm, python' },
  { n: 5, name: 'Irreversible', policy: 'Always ask', ex: 'delete files' },
];

function ListEditor({ items, onChange, placeholder, mono }: { items: string[]; onChange: (v: string[]) => void; placeholder: string; mono?: boolean }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (v && !items.includes(v)) onChange([...items, v]);
    setDraft('');
  };
  return (
    <div>
      <div className="flex flex-col gap-1">
        {items.map((it) => (
          <div key={it} className="group flex items-center justify-between rounded-lg bg-ink-900/70 px-3 py-1.5">
            <span className={mono ? 'font-mono text-[12px]' : 'text-[12.5px]'}>{it}</span>
            <button aria-label={`Remove ${it}`} onClick={() => onChange(items.filter((x) => x !== it))} className="text-faint opacity-0 group-hover:opacity-100 hover:text-danger"><X size={13} /></button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} placeholder={placeholder}
          className="h-8 flex-1 rounded-lg border border-ink-600 bg-ink-800 px-2.5 font-mono text-[12.5px] outline-none placeholder:text-faint focus:border-accent/60" />
        <Button size="sm" onClick={add} disabled={!draft.trim()}><FolderPlus size={13} /> Add</Button>
      </div>
    </div>
  );
}

interface ToolInfo { name: string; description: string; level: number; category: string; available: boolean }

export function PermissionsSection() {
  const [s, save] = useSettings();
  const p = s.permissions;
  const [tools, setTools] = useState<ToolInfo[]>([]);
  useEffect(() => { void client.get('/api/tools').then(setTools); }, []);
  const setOverride = (name: string, v: string) => {
    const next = { ...p.tool_overrides };
    if (v === 'default') delete next[name]; else next[name] = v as 'auto' | 'ask' | 'deny';
    void save({ permissions: { tool_overrides: next } });
  };
  return (
    <>
      <Card title="Permission levels" description="The model can only request tools. The backend validates every argument and applies these rules before anything runs. Content read from web pages, email or files cannot grant itself permissions: after such content is read, level 3+ actions in that task always ask.">
        <div className="flex flex-col">
          {LEVELS.map((l) => (
            <div key={l.n} className="flex items-baseline gap-3 border-t border-ink-700/70 py-2 first:border-t-0">
              <Badge tone={l.n >= 5 ? 'bad' : l.n >= 3 ? 'warn' : 'good'} className="w-8 justify-center">L{l.n}</Badge>
              <div className="min-w-0 flex-1"><div className="text-[13px]">{l.name}</div><div className="text-[12px] text-faint">{l.ex}</div></div>
              <div className="text-right text-[12px] text-muted">{l.policy}</div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Policies">
        <Row label="Level 3: create & modify" hint="Ask before Jarvis creates, edits or moves files, clicks or types in the browser, or drafts email.">
          <Select value={p.level3_policy} onChange={(level3_policy) => void save({ permissions: { level3_policy } })}
            options={[{ value: 'ask', label: 'Ask every time' }, { value: 'auto', label: 'Allow automatically' }]} />
        </Row>
        <Row label="Run allowlisted commands without asking" hint="Only exact prefixes from the allowlist below; commands that install, remove or publish always ask.">
          <Toggle checked={p.allowlisted_commands_auto} onChange={(x) => void save({ permissions: { allowlisted_commands_auto: x } })} />
        </Row>
        <Row label="Approval timeout" hint="Unanswered requests are denied.">
          <Slider value={p.approval_timeout_s} min={15} max={600} step={15} onCommit={(x) => void save({ permissions: { approval_timeout_s: x } })} format={(x) => `${x}s`} />
        </Row>
      </Card>
      <Card title="Workspace folders" description="File tools and command working directories are restricted to these folders. Windows, Program Files, AppData, Jarvis's own data and credential files (.ssh, .env, keys…) are always blocked.">
        <ListEditor items={p.workspace_roots} mono placeholder="C:\Users\you\Projects" onChange={(workspace_roots) => void save({ permissions: { workspace_roots } })} />
      </Card>
      <Card title="Command allowlist" description="Program + leading arguments, e.g. “git status”. Shells (cmd, PowerShell) and system tools are always blocked; commands run without a shell.">
        <ListEditor items={p.command_allowlist} mono placeholder="npm run build" onChange={(command_allowlist) => void save({ permissions: { command_allowlist } })} />
      </Card>
      <Card title="Per-tool policy" description="Override the level default for individual tools. Level 5 tools can never be set to automatic.">
        <div className="flex flex-col">
          {tools.map((t) => (
            <div key={t.name} className="flex items-center gap-3 border-t border-ink-700/70 py-2 first:border-t-0">
              <Badge tone={t.level >= 5 ? 'bad' : t.level >= 3 ? 'warn' : 'good'} className="w-8 justify-center">L{t.level}</Badge>
              <div className="min-w-0 flex-1">
                <div className="text-[13px]">{toolLabel(t.name)} <span className="font-mono text-[11px] text-faint">{t.name}</span></div>
                <div className="truncate text-[12px] text-faint">{t.description}</div>
              </div>
              <Select value={p.tool_overrides[t.name] ?? 'default'} onChange={(v) => setOverride(t.name, v)}
                options={[{ value: 'default', label: 'Default' }, { value: 'auto', label: 'Automatic', disabled: t.level >= 5 }, { value: 'ask', label: 'Ask' }, { value: 'deny', label: 'Disabled' }]} />
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
