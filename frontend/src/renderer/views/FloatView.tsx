// Compact always-on-top assistant (separate window, #/float).
import { Maximize2, Mic, Square, X } from 'lucide-react';
import { useState } from 'react';
import { ORB_LABEL, VoiceOrb } from '../components/VoiceOrb';
import { IconButton, cx } from '../components/ui';
import { bridge } from '../lib/bridge';
import { orbState, phaseLabel } from '../lib/status';
import { useStore } from '../lib/store';

export function FloatView() {
  const { state, actions } = useStore();
  const [text, setText] = useState('');
  const orb = state.playback.status === 'playing' && orbState(state) === 'idle' ? 'speaking' : orbState(state);
  const task = Object.values(state.tasks)[0];
  const busy = !!task || orb === 'listening' || orb === 'speaking' || orb === 'transcribing';
  const line = orb === 'listening' ? 'Listening…'
    : task ? phaseLabel(task.phase, task.detail)
      : state.voice.transcript && orb !== 'idle' ? `“${state.voice.transcript}”`
        : state.voice.hint ?? ORB_LABEL[orb];

  return (
    <div className="drag flex h-full items-center p-2">
      <div className="flex h-full w-full items-center gap-3 rounded-[22px] border border-ink-600/80 bg-ink-850/95 px-3 shadow-2xl backdrop-blur">
        <div className="no-drag"><VoiceOrb state={orb} level={state.voice.level} size={58} /></div>
        <div className="no-drag flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className={cx('size-1.5 rounded-full', orb === 'idle' ? 'bg-faint' : orb === 'awaiting' ? 'bg-warm' : 'bg-accent')} />
            <span className="truncate text-[12.5px] text-fg">{line}</span>
          </div>
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { actions.send(text); setText(''); } }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a command…"
              className="h-8 w-full rounded-lg border border-ink-700 bg-ink-900 px-2.5 text-[12.5px] outline-none placeholder:text-faint focus:border-ink-500" />
          </form>
        </div>
        <div className="no-drag flex flex-col gap-1">
          {busy
            ? <IconButton label="Stop" onClick={() => { actions.stopAll(); actions.stopSpeaking(); }} className="text-danger!"><Square size={14} fill="currentColor" /></IconButton>
            : <IconButton label="Speak" onClick={actions.toggleVoice}><Mic size={15} /></IconButton>}
          <div className="flex">
            <IconButton label="Open Jarvis" className="size-6" onClick={() => void bridge().showMain()}><Maximize2 size={12} /></IconButton>
            <IconButton label="Hide" className="size-6" onClick={() => void bridge().hideFloat()}><X size={13} /></IconButton>
          </div>
        </div>
      </div>
      {state.approvals.length > 0 && (
        <button className="no-drag absolute top-0 right-3 rounded-full bg-warm px-2 py-0.5 text-[11px] font-medium text-ink-950" onClick={() => void bridge().showMain()}>
          Approval needed
        </button>
      )}
    </div>
  );
}
