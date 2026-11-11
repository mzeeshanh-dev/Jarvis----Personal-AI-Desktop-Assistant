import { MessagesSquare, Search, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Badge, Button, Empty, cx } from '../components/ui';
import { client } from '../lib/api';
import { timeAgo } from '../lib/status';
import { useStore } from '../lib/store';

interface TaskRow { id: string; input: string; source: string; route: string | null; status: string; llm_calls: number; tool_calls: number; started_at: number; finished_at: number | null; error: string | null }

export function HistoryView({ onOpen }: { onOpen: () => void }) {
  const { state, actions } = useStore();
  const [tab, setTab] = useState<'conversations' | 'tasks'>('conversations');
  const [q, setQ] = useState('');
  const [tasks, setTasks] = useState<TaskRow[]>([]);

  useEffect(() => {
    const t = setTimeout(() => void actions.refreshConversations(q || undefined), 200);
    return () => clearTimeout(t);
  }, [q, actions]);

  useEffect(() => {
    if (tab === 'tasks') void client.get('/api/tasks?limit=200').then((r) => setTasks(r.history)).catch(() => {});
  }, [tab, state.lastTaskSummary]);

  const remove = async (id: string) => {
    await client.del(`/api/conversations/${id}`);
    if (state.conversationId === id) await actions.selectConversation(null);
    await actions.refreshConversations(q || undefined);
  };

  const clearAll = async () => {
    if (!window.confirm('Delete all conversations? This cannot be undone.')) return;
    await client.del('/api/conversations');
    await actions.selectConversation(null);
    await actions.refreshConversations();
    actions.toast('success', 'Conversation history cleared.');
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col px-8 py-6">
      <div className="mx-auto flex w-full max-w-4xl min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between">
          <h1 className="font-display text-[20px] font-semibold">History</h1>
          <div className="flex rounded-lg border border-ink-700 bg-ink-850 p-0.5">
            {(['conversations', 'tasks'] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={cx('h-7 rounded-md px-3 text-[12.5px] capitalize', tab === t ? 'bg-ink-700 text-fg' : 'text-muted hover:text-fg')}>{t === 'tasks' ? 'Task log' : t}</button>
            ))}
          </div>
        </div>

        {tab === 'conversations' ? (
          <>
            <div className="mt-4 flex items-center gap-2">
              <div className="flex h-9 flex-1 items-center gap-2 rounded-lg border border-ink-700 bg-ink-850 px-3">
                <Search size={14} className="text-faint" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search conversations" className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint" />
              </div>
              <Button variant="danger" onClick={clearAll} disabled={!state.conversations.length}>Clear all</Button>
            </div>
            <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-xl border border-ink-700 bg-ink-850">
              {state.conversations.length === 0 && <Empty icon={<MessagesSquare size={28} />} title={q ? 'No matches' : 'No conversations yet'}>Conversations are stored locally in SQLite on this computer.</Empty>}
              {state.conversations.map((c) => (
                <div key={c.id} className="group flex items-center gap-3 border-b border-ink-700/70 px-4 py-3 last:border-b-0 hover:bg-ink-800">
                  <button className="min-w-0 flex-1 text-left" onClick={() => { void actions.selectConversation(c.id); onOpen(); }}>
                    <div className="truncate text-[13px] text-fg">{c.title}</div>
                    <div className="text-[12px] text-faint">{c.message_count} messages · {timeAgo(c.updated_at * 1000)}</div>
                  </button>
                  <button aria-label="Delete conversation" onClick={() => void remove(c.id)} className="rounded-md p-1.5 text-faint opacity-0 group-hover:opacity-100 hover:bg-danger/10 hover:text-danger">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="mt-4 min-h-0 flex-1 overflow-y-auto rounded-xl border border-ink-700 bg-ink-850">
            <table className="w-full text-[12.5px]">
              <thead className="sticky top-0 bg-ink-850 text-left text-[11px] tracking-wide text-faint uppercase">
                <tr><th className="px-4 py-2 font-medium">Request</th><th className="px-2 font-medium">Route</th><th className="px-2 font-medium">Status</th><th className="px-2 font-medium">LLM</th><th className="px-2 font-medium">Tools</th><th className="px-2 font-medium">Time</th><th className="px-4 font-medium">When</th></tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="border-t border-ink-700/70" title={t.error ?? undefined}>
                    <td className="max-w-80 truncate px-4 py-2 text-fg">{t.source === 'voice' && <Badge className="mr-1.5">voice</Badge>}{t.input}</td>
                    <td className="px-2 text-muted">{t.route ?? '—'}</td>
                    <td className="px-2"><Badge tone={t.status === 'completed' ? 'good' : t.status === 'failed' ? 'bad' : 'neutral'}>{t.status}</Badge></td>
                    <td className="px-2 font-mono text-muted">{t.llm_calls}</td>
                    <td className="px-2 font-mono text-muted">{t.tool_calls}</td>
                    <td className="px-2 font-mono text-muted">{t.finished_at ? `${((t.finished_at - t.started_at)).toFixed(1)}s` : '—'}</td>
                    <td className="px-4 text-faint">{timeAgo(t.started_at * 1000)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!tasks.length && <Empty title="No tasks yet" />}
          </div>
        )}
      </div>
    </div>
  );
}
