import { describe, expect, it } from 'vitest';
import { initialState, reduceEvent, reducer, rowsToMessages, type State } from '../../frontend/src/renderer/lib/reducer';
import { orbState, phaseLabel, duration } from '../../frontend/src/renderer/lib/status';

const run = (events: any[], s: State = { ...initialState, connected: true }) => events.reduce(reduceEvent, s);

describe('task event flow', () => {
  it('adopts the conversation, streams, records tools and finalizes', () => {
    const s = run([
      { type: 'task.started', task_id: 't1', conversation_id: 'c1', text: 'find files', source: 'text' },
      { type: 'task.status', task_id: 't1', phase: 'thinking' },
      { type: 'assistant.delta', task_id: 't1', stream_id: 't1-0', text: 'Look' },
      { type: 'assistant.delta', task_id: 't1', stream_id: 't1-0', text: 'ing' },
      { type: 'tool.started', task_id: 't1', call_id: 'k1', tool: 'search_files', level: 2, arguments: { query: 'x' } },
      { type: 'tool.finished', task_id: 't1', call_id: 'k1', tool: 'search_files', ok: true, summary: 'Found 2', duration_ms: 12 },
    ]);
    expect(s.conversationId).toBe('c1');
    expect(s.messages).toHaveLength(1);
    expect(s.drafts.t1.text).toBe('Looking');
    expect(s.taskTools.t1[0]).toMatchObject({ ok: true, running: false, summary: 'Found 2' });
    expect(orbState(s)).toBe('thinking');

    const done = run([
      { type: 'assistant.message', task_id: 't1', conversation_id: 'c1', content: 'Found 2 files.', status: 'completed' },
      { type: 'task.finished', task_id: 't1', status: 'completed', llm_calls: 2, tool_calls: 1, duration_ms: 900 },
    ], s);
    expect(done.messages.at(-1)).toMatchObject({ role: 'assistant', content: 'Found 2 files.', tools: [{ tool: 'search_files' }] });
    expect(done.tasks).toEqual({});
    expect(done.drafts).toEqual({});
    expect(done.lastTaskSummary).toMatchObject({ llmCalls: 2 });
    expect(orbState(done)).toBe('idle');
  });

  it('a new stream id replaces the draft text', () => {
    const s = run([
      { type: 'task.started', task_id: 't', conversation_id: 'c', text: 'x', source: 'text' },
      { type: 'assistant.delta', task_id: 't', stream_id: 't-0', text: 'first' },
      { type: 'assistant.delta', task_id: 't', stream_id: 't-1', text: 'second' },
    ]);
    expect(s.drafts.t.text).toBe('second');
  });

  it('ignores messages for other conversations', () => {
    const s = run([{ type: 'task.started', task_id: 't', conversation_id: 'other', text: 'x', source: 'voice' }],
      { ...initialState, conversationId: 'mine' });
    expect(s.messages).toHaveLength(0);
    expect(s.tasks.t).toBeDefined();
  });

  it('approvals queue, expire and clear when the task ends', () => {
    let s = run([
      { type: 'task.started', task_id: 't', conversation_id: 'c', text: 'x', source: 'text' },
      { type: 'approval.request', id: 'a1', task_id: 't', tool: 'delete_file', level: 5 },
      { type: 'approval.request', id: 'a2', task_id: 't', tool: 'create_file', level: 3 },
    ]);
    expect(s.approvals.map((a) => a.id)).toEqual(['a1', 'a2']);
    expect(orbState(s)).toBe('awaiting');
    s = reduceEvent(s, { type: 'approval.expired', id: 'a1' });
    expect(s.approvals.map((a) => a.id)).toEqual(['a2']);
    s = reduceEvent(s, { type: 'task.cancelled', task_id: 't' });
    expect(s.approvals).toEqual([]);
  });

  it('voice state, level and errors', () => {
    let s = run([{ type: 'voice.state', state: 'listening', wake_word: true }, { type: 'voice.level', level: 0.7 }]);
    expect(orbState(s)).toBe('listening');
    expect(s.voice).toMatchObject({ level: 0.7, wakeWord: true });
    s = reduceEvent(s, { type: 'voice.error', error: 'Mic blocked' });
    expect(s.toasts.at(-1)?.text).toBe('Mic blocked');
    s = reduceEvent(s, { type: 'voice.state', state: 'idle' });
    expect(s.voice.level).toBe(0);
  });

  it('offline when not connected', () => {
    expect(orbState(initialState)).toBe('offline');
  });

  it('toasts are capped', () => {
    let s = initialState;
    for (let i = 0; i < 10; i++) s = reducer(s, { type: 'toast', toast: { id: i, kind: 'info', text: String(i) } });
    expect(s.toasts.length).toBeLessThanOrEqual(4);
  });
});

describe('history rows', () => {
  it('folds tool calls and results into the final reply', () => {
    const rows = [
      { id: 1, role: 'user', content: 'read notes', task_id: 't', created_at: 1 },
      { id: 2, role: 'assistant', content: null, task_id: 't', created_at: 2, tool_calls: [{ id: 'c1', function: { name: 'read_file', arguments: '{"path":"n.txt"}' } }] },
      { id: 3, role: 'tool', content: '<untrusted_content source="file:n.txt">\n{"ok": true, "summary": "Read 10 of 10 \\"chars\\"", "data": {}}\n</untrusted_content>', tool_call_id: 'c1', task_id: 't', created_at: 3 },
      { id: 4, role: 'assistant', content: 'It says hi.', task_id: 't', created_at: 4 },
    ];
    const msgs = rowsToMessages(rows);
    expect(msgs).toHaveLength(2);
    expect(msgs[1].tools[0]).toMatchObject({ tool: 'read_file', ok: true, summary: 'Read 10 of 10 "chars"', arguments: { path: 'n.txt' } });
  });
});

describe('labels', () => {
  it('formats phases and durations', () => {
    expect(phaseLabel('executing', 'search_files, read_file')).toBe('Running Search files, Read file');
    expect(phaseLabel('awaiting_approval', 'delete_file')).toBe('Waiting for approval · Delete');
    expect(duration(59)).toBe('59s');
    expect(duration(125)).toBe('2m 5s');
    expect(duration(3725)).toBe('1h 2m');
  });
});
