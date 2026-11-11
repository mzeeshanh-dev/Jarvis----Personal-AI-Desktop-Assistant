// Animated voice/status indicator built around the emblem.
import { motion } from 'motion/react';
import { Emblem } from './Emblem';
import { cx } from './ui';

export type OrbState = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'executing' | 'speaking' | 'awaiting' | 'offline' | 'error';

export const ORB_LABEL: Record<OrbState, string> = {
  idle: 'Ready', listening: 'Listening', transcribing: 'Transcribing', thinking: 'Thinking', executing: 'Working',
  speaking: 'Speaking', awaiting: 'Needs your approval', offline: 'Connecting…', error: 'Microphone problem',
};

export function VoiceOrb({ state, level = 0, size = 112 }: { state: OrbState; level?: number; size?: number }) {
  const busy = state === 'thinking' || state === 'executing' || state === 'transcribing';
  const ring = state === 'listening' ? 1 + level * 0.35 : 1;
  const tone = state === 'awaiting' ? 'border-warm/50' : state === 'error' ? 'border-danger/50' : 'border-accent/40';
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <motion.div
        className={cx('absolute inset-0 rounded-full border', tone)}
        animate={{ scale: ring, opacity: state === 'idle' || state === 'offline' ? 0.25 : 0.9 }}
        transition={{ type: 'spring', stiffness: 260, damping: 22 }}
      />
      <motion.div
        className="absolute rounded-full bg-accent/10 blur-xl"
        style={{ inset: size * 0.12 }}
        animate={{ scale: state === 'listening' ? 1 + level * 0.6 : state === 'speaking' ? [1, 1.12, 1] : 1, opacity: state === 'idle' ? 0.3 : 0.8 }}
        transition={state === 'speaking' ? { duration: 1.2, repeat: Infinity } : { type: 'spring', stiffness: 200, damping: 20 }}
      />
      <div className={cx(state === 'offline' && 'opacity-40 grayscale', state === 'idle' && 'animate-breathe')}>
        <Emblem size={size * 0.72} spin={busy} glow={state === 'listening' ? 0.9 + level * 0.5 : 1} />
      </div>
    </div>
  );
}

export function Equalizer({ active }: { active: boolean }) {
  return (
    <span className="inline-flex h-3.5 items-center gap-[2px]" aria-hidden>
      {[0, 0.15, 0.3, 0.1].map((d, i) => (
        <span key={i} className={cx('w-[3px] rounded-full bg-accent', active ? 'eq-bar h-full' : 'h-1')} style={{ animationDelay: `${d}s` }} />
      ))}
    </span>
  );
}
