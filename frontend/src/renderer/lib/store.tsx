// App-wide state: backend connection, conversations, tasks, voice, playback.
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react';
import { client } from './api';
import { bridge } from './bridge';
import { playback } from './playback';
import { initialState, makeToast, reducer, rowsToMessages, type State } from './reducer';
import type { DeepPartial, Settings } from './types';

interface Actions {
  send(text: string): void;
  cancelTask(taskId: string): void;
  stopAll(): void;
  respondApproval(id: string, approved: boolean): void;
  toggleVoice(): void;
  selectConversation(id: string | null): Promise<void>;
  refreshConversations(query?: string): Promise<void>;
  saveSettings(patch: DeepPartial<Settings>): Promise<Settings | null>;
  stopSpeaking(): void;
  toast(kind: 'info' | 'error' | 'success', text: string): void;
  dismissToast(id: number): void;
}

const Ctx = createContext<{ state: State; actions: Actions } | null>(null);

export function StoreProvider({ children, role }: { children: ReactNode; role: 'main' | 'float' }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;

  // Backend lifecycle from the main process.
  useEffect(() => {
    let off = () => {};
    try {
      const b = bridge();
      void b.backendInfo().then((info) => { client.setInfo(info); dispatch({ type: 'backend', info }); });
      off = b.on('backend:status', (info) => { client.setInfo(info); dispatch({ type: 'backend', info }); });
    } catch (e) {
      console.warn(e);
    }
    return off;
  }, []);

  // Server events.
  useEffect(() => client.onEvent((event) => {
    dispatch({ type: 'event', event });
    if (event.type === 'ws.open') {
      void client.get('/api/settings').then((settings) => dispatch({ type: 'settings', settings })).catch(() => {});
      if (role === 'main') void refreshConversations();
      client.send({ type: 'ui.conversation', conversation_id: stateRef.current.conversationId });
    }
    if (event.type === 'task.finished') void refreshConversations();
    if (role !== 'main') return;
    if (event.type === 'tts.ready') playback.playClip(client.mediaUrl(`/api/tts/${event.clip_id}.wav`), event.text, event.kind);
    else if (event.type === 'tts.system') playback.speakSystem(event.text, event.rate, event.kind);
    else if (event.type === 'tts.stop') playback.stop();
  }), [role]);

  // Playback state (main window owns audio and reports it for the float window).
  useEffect(() => playback.subscribe((p) => {
    dispatch({ type: 'playback', playback: p });
    if (role === 'main') client.send({ type: 'tts.state', playing: p.status === 'playing' });
  }), [role]);

  // Navigation requests from tray/main.
  const refreshConversations = useCallback(async (query?: string) => {
    if (!client.ready) return;
    try {
      const list = await client.get(`/api/conversations${query ? `?q=${encodeURIComponent(query)}` : ''}`);
      dispatch({ type: 'conversations', list });
    } catch { /* backend restarting */ }
  }, []);

  const actions = useMemo<Actions>(() => ({
    send(text) {
      const t = text.trim();
      if (!t) return;
      if (!client.send({ type: 'chat.send', text: t, conversation_id: stateRef.current.conversationId })) {
        dispatch({ type: 'toast', toast: makeToast('error', 'Not connected to the Jarvis backend yet.') });
      }
    },
    cancelTask(taskId) { client.send({ type: 'task.cancel', task_id: taskId }); },
    stopAll() {
      client.send({ type: 'task.cancel_all' });
      playback.stop();
    },
    respondApproval(id, approved) {
      client.send({ type: 'approval.respond', id, approved });
      dispatch({ type: 'approvalResolved', id });
    },
    toggleVoice() {
      if (!client.send({ type: 'voice.toggle', conversation_id: stateRef.current.conversationId })) {
        dispatch({ type: 'toast', toast: makeToast('error', 'Not connected to the Jarvis backend yet.') });
      }
    },
    async selectConversation(id) {
      if (!id) {
        dispatch({ type: 'conversation', id: null, messages: [] });
      } else {
        const rows = await client.get(`/api/conversations/${id}/messages`);
        dispatch({ type: 'conversation', id, messages: rowsToMessages(rows) });
      }
      client.send({ type: 'ui.conversation', conversation_id: id });
    },
    refreshConversations,
    async saveSettings(patch) {
      try {
        // Desktop settings (shortcut etc.) are applied by Electron first, so a
        // shortcut it rejects or can't register is never persisted.
        if (patch.general) {
          const current = stateRef.current.settings?.general ?? {};
          const r = await bridge().applyDesktop({ ...current, ...patch.general });
          if (!r.ok) {
            dispatch({ type: 'toast', toast: makeToast('error', r.error ?? 'Could not apply that setting.') });
            if (patch.general.shortcut && current && 'shortcut' in current) {
              await bridge().applyDesktop({ shortcut: (current as Settings['general']).shortcut });
            }
            return null;
          }
        }
        const settings = await client.put<Settings>('/api/settings', patch);
        dispatch({ type: 'settings', settings });
        return settings;
      } catch (e) {
        dispatch({ type: 'toast', toast: makeToast('error', (e as Error).message) });
        return null;
      }
    },
    stopSpeaking() {
      if (role === 'main') playback.stop();
      else void client.post('/api/tts/stop').catch(() => {});
    },
    toast(kind, text) { dispatch({ type: 'toast', toast: makeToast(kind, text) }); },
    dismissToast(id) { dispatch({ type: 'dismissToast', id }); },
  }), [refreshConversations, role]);

  // Keep the backend informed which conversation voice follow-ups belong to.
  useEffect(() => {
    if (role === 'main') client.send({ type: 'ui.conversation', conversation_id: state.conversationId });
  }, [state.conversationId, role]);

  return <Ctx.Provider value={{ state, actions }}>{children}</Ctx.Provider>;
}

export function useStore() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useStore outside StoreProvider');
  return v;
}
