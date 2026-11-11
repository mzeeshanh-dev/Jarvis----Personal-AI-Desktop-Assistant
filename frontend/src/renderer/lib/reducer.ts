// Pure state transitions for server events (unit-tested without the DOM).
import type { BackendInfo } from './bridge';
import type { ActiveTask, Approval, ChatMessage, Conversation, Settings, ToolRun, VoiceInfo } from './types';
import type { PlaybackState } from './playback';
import type { ServerEvent } from './api';

export interface Toast { id: number; kind: 'info' | 'error' | 'success'; text: string }

export interface State {
  backend: BackendInfo | null;
  connected: boolean;
  conversationId: string | null;
  messages: ChatMessage[];
  conversations: Conversation[];
  tasks: Record<string, ActiveTask>;
  taskTools: Record<string, ToolRun[]>;
  drafts: Record<string, { streamId: string; text: string }>;
  approvals: Approval[];
  voice: VoiceInfo;
  playback: PlaybackState;
  settings: Settings | null;
  toasts: Toast[];
  providersVersion: number;
  gmailVersion: number;
  lastTaskSummary: { status: string; llmCalls: number; toolCalls: number; durationMs: number } | null;
}

export const initialState: State = {
  backend: null, connected: false, conversationId: null, messages: [], conversations: [], tasks: {}, taskTools: {},
  drafts: {}, approvals: [], voice: { state: 'idle', level: 0, wakeWord: false, paused: false }, playback: { status: 'idle' },
  settings: null, toasts: [], providersVersion: 0, gmailVersion: 0, lastTaskSummary: null,
};

export type Action =
  | { type: 'event'; event: ServerEvent }
  | { type: 'backend'; info: BackendInfo }
  | { type: 'conversation'; id: string | null; messages: ChatMessage[] }
  | { type: 'conversations'; list: Conversation[] }
  | { type: 'settings'; settings: Settings }
  | { type: 'playback'; playback: PlaybackState }
  | { type: 'toast'; toast: Toast }
  | { type: 'dismissToast'; id: number }
  | { type: 'approvalResolved'; id: string };

let toastSeq = 0;
export const makeToast = (kind: Toast['kind'], text: string): Toast => ({ id: ++toastSeq, kind, text });

function upsertTool(list: ToolRun[] = [], run: ToolRun): ToolRun[] {
  const i = list.findIndex((t) => t.callId === run.callId);
  if (i === -1) return [...list, run];
  const copy = list.slice();
  copy[i] = { ...copy[i], ...run };
  return copy;
}

function omit<T>(rec: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _drop, ...rest } = rec;
  return rest;
}

export function reduceEvent(s: State, e: ServerEvent): State {
  switch (e.type) {
    case 'ws.open': return { ...s, connected: true };
    case 'ws.close': return { ...s, connected: false };
    case 'hello': {
      const tasks: Record<string, ActiveTask> = {};
      for (const t of e.active_tasks ?? []) {
        tasks[t.id] = { id: t.id, conversationId: t.conversation_id, text: t.text, source: t.source, phase: t.phase, startedAt: t.started_at * 1000 };
      }
      const approvals = (e.approvals ?? []).map((a: Approval) => ({ ...a, receivedAt: Date.now() }));
      return { ...s, tasks, approvals, voice: e.voice ? { ...s.voice, state: e.voice.state, wakeWord: e.voice.wake_word_running, paused: e.voice.paused } : s.voice };
    }
    case 'task.started': {
      const conversationId = s.conversationId ?? e.conversation_id;
      const task: ActiveTask = { id: e.task_id, conversationId: e.conversation_id, text: e.text, source: e.source, phase: 'starting', startedAt: Date.now() };
      const messages = e.conversation_id === conversationId
        ? [...s.messages, { id: `u-${e.task_id}`, role: 'user' as const, content: e.text, taskId: e.task_id, tools: [], createdAt: Date.now() }]
        : s.messages;
      return { ...s, conversationId, messages, tasks: { ...s.tasks, [e.task_id]: task } };
    }
    case 'task.status': {
      const t = s.tasks[e.task_id];
      if (!t) return s;
      return { ...s, tasks: { ...s.tasks, [e.task_id]: { ...t, phase: e.phase, detail: e.detail } } };
    }
    case 'assistant.delta': {
      const d = s.drafts[e.task_id];
      const text = d && d.streamId === e.stream_id ? d.text + e.text : e.text;
      return { ...s, drafts: { ...s.drafts, [e.task_id]: { streamId: e.stream_id, text } } };
    }
    case 'tool.started':
      return { ...s, taskTools: { ...s.taskTools, [e.task_id]: upsertTool(s.taskTools[e.task_id], { callId: e.call_id, tool: e.tool, level: e.level, arguments: e.arguments, running: true }) } };
    case 'tool.finished':
      return { ...s, taskTools: { ...s.taskTools, [e.task_id]: upsertTool(s.taskTools[e.task_id], { callId: e.call_id, tool: e.tool, ok: e.ok, summary: e.summary, durationMs: e.duration_ms, running: false }) } };
    case 'assistant.message': {
      const msg: ChatMessage = {
        id: `a-${e.task_id}`, role: 'assistant', content: e.content, taskId: e.task_id, status: e.status,
        tools: (s.taskTools[e.task_id] ?? []).map((t) => ({ ...t, running: false })), createdAt: Date.now(),
      };
      const messages = e.conversation_id === s.conversationId ? [...s.messages.filter((m) => m.id !== msg.id), msg] : s.messages;
      return { ...s, messages, drafts: omit(s.drafts, e.task_id), taskTools: omit(s.taskTools, e.task_id) };
    }
    case 'task.finished':
      return {
        ...s, tasks: omit(s.tasks, e.task_id), approvals: s.approvals.filter((a) => a.task_id !== e.task_id),
        lastTaskSummary: { status: e.status, llmCalls: e.llm_calls, toolCalls: e.tool_calls, durationMs: e.duration_ms },
      };
    case 'task.cancelled':
      return { ...s, tasks: omit(s.tasks, e.task_id), drafts: omit(s.drafts, e.task_id), approvals: s.approvals.filter((a) => a.task_id !== e.task_id) };
    case 'approval.request':
      return { ...s, approvals: [...s.approvals.filter((a) => a.id !== e.id), { ...(e as unknown as Approval), receivedAt: Date.now() }] };
    case 'approval.expired':
      return { ...s, approvals: s.approvals.filter((a) => a.id !== e.id) };
    case 'voice.state':
      return { ...s, voice: { ...s.voice, state: e.state, wakeWord: !!e.wake_word, paused: !!e.paused, error: e.state === 'error' ? e.error : undefined, hint: e.state === 'listening' ? undefined : s.voice.hint, level: e.state === 'listening' ? s.voice.level : 0 } };
    case 'voice.level':
      return { ...s, voice: { ...s.voice, level: e.level } };
    case 'voice.transcript':
      return { ...s, voice: { ...s.voice, transcript: e.text, hint: undefined } };
    case 'voice.no_speech':
      return { ...s, voice: { ...s.voice, hint: "I didn't catch that." } };
    case 'voice.error':
      return { ...s, voice: { ...s.voice, error: e.error }, toasts: [...s.toasts, makeToast('error', e.error)] };
    case 'wakeword.detected':
      return { ...s, voice: { ...s.voice, hint: 'Wake word detected' } };
    case 'tts.playing':
      return { ...s, playback: { ...s.playback, status: e.playing ? 'playing' : 'idle' } };
    case 'providers.changed':
      return { ...s, providersVersion: s.providersVersion + 1 };
    case 'gmail.changed':
      return { ...s, gmailVersion: s.gmailVersion + 1 };
    case 'settings.changed':
      return { ...s, settings: e.settings };
    default:
      return s;
  }
}

export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case 'event': return reduceEvent(s, a.event);
    case 'backend': return { ...s, backend: a.info, connected: a.info.status === 'ready' ? s.connected : false };
    case 'conversation': return { ...s, conversationId: a.id, messages: a.messages };
    case 'conversations': return { ...s, conversations: a.list };
    case 'settings': return { ...s, settings: a.settings };
    case 'playback': return { ...s, playback: a.playback };
    case 'toast': return { ...s, toasts: [...s.toasts.slice(-3), a.toast] };
    case 'dismissToast': return { ...s, toasts: s.toasts.filter((t) => t.id !== a.id) };
    case 'approvalResolved': return { ...s, approvals: s.approvals.filter((x) => x.id !== a.id) };
  }
}

/** Convert stored DB rows into UI chat messages (tool calls folded into the final reply). */
export function rowsToMessages(rows: any[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  const pending: Record<string, ToolRun[]> = {};
  const byCall: Record<string, ToolRun> = {};
  for (const r of rows) {
    const taskId = r.task_id ?? `row-${r.id}`;
    if (r.role === 'user') {
      out.push({ id: `u-${r.id}`, role: 'user', content: r.content ?? '', taskId, tools: [], createdAt: r.created_at * 1000 });
    } else if (r.role === 'assistant' && r.tool_calls?.length) {
      for (const c of r.tool_calls) {
        let args: Record<string, unknown> | undefined;
        try { args = JSON.parse(c.function?.arguments || '{}'); } catch { args = undefined; }
        const run: ToolRun = { callId: c.id, tool: c.function?.name, arguments: args, running: false };
        byCall[c.id] = run;
        (pending[taskId] ??= []).push(run);
      }
    } else if (r.role === 'tool') {
      const run = byCall[r.tool_call_id];
      if (run) {
        const m = /"ok":\s*(true|false).*?"summary":\s*"((?:[^"\\]|\\.)*)"/s.exec(r.content ?? '');
        if (m) { run.ok = m[1] === 'true'; try { run.summary = JSON.parse(`"${m[2]}"`); } catch { run.summary = m[2]; } }
      }
    } else if (r.role === 'assistant') {
      out.push({ id: `a-${r.id}`, role: 'assistant', content: r.content ?? '', taskId, tools: pending[taskId] ?? [], createdAt: r.created_at * 1000 });
      delete pending[taskId];
    }
  }
  return out;
}
