// Spoken-response playback: Piper WAV clips via <audio>, or the Windows
// system voice via speechSynthesis as a fallback. Only the main window plays.
export type PlaybackStatus = 'idle' | 'playing' | 'paused';
export interface PlaybackState { status: PlaybackStatus; text?: string; source?: 'piper' | 'system'; kind?: string }

type Listener = (s: PlaybackState) => void;

export class Playback {
  private audio: HTMLAudioElement | null = null;
  private last: { url?: string; text: string; rate: number; source: 'piper' | 'system'; kind?: string } | null = null;
  private listeners = new Set<Listener>();
  state: PlaybackState = { status: 'idle' };

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }

  private set(s: PlaybackState) {
    this.state = s;
    for (const l of this.listeners) l(s);
  }

  playClip(url: string, text: string, kind?: string) {
    this.stop(false);
    this.last = { url, text, rate: 1, source: 'piper', kind };
    const a = new Audio(url);
    this.audio = a;
    a.onended = () => { if (this.audio === a) this.set({ status: 'idle', text }); };
    a.onerror = () => { if (this.audio === a) this.set({ status: 'idle', text }); };
    a.play().then(() => this.set({ status: 'playing', text, source: 'piper', kind })).catch(() => this.set({ status: 'idle' }));
  }

  speakSystem(text: string, rate = 1, kind?: string) {
    this.stop(false);
    this.last = { text, rate, source: 'system', kind };
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.rate = rate;
    const voices = speechSynthesis.getVoices();
    const preferred = voices.find((v) => /en-GB/i.test(v.lang)) ?? voices.find((v) => /^en/i.test(v.lang));
    if (preferred) u.voice = preferred;
    u.onend = () => this.set({ status: 'idle', text });
    speechSynthesis.speak(u);
    this.set({ status: 'playing', text, source: 'system', kind });
  }

  pause() {
    if (this.state.status !== 'playing') return;
    if (this.audio) this.audio.pause(); else speechSynthesis.pause();
    this.set({ ...this.state, status: 'paused' });
  }

  resume() {
    if (this.state.status !== 'paused') return;
    if (this.audio) void this.audio.play(); else speechSynthesis.resume();
    this.set({ ...this.state, status: 'playing' });
  }

  stop(notify = true) {
    if (this.audio) {
      this.audio.pause();
      this.audio.src = '';
      this.audio = null;
    }
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    if (notify || this.state.status !== 'idle') this.set({ status: 'idle', text: this.state.text });
  }

  replay() {
    if (!this.last) return;
    if (this.last.source === 'piper' && this.last.url) this.playClip(this.last.url, this.last.text, this.last.kind);
    else this.speakSystem(this.last.text, this.last.rate, this.last.kind);
  }

  get canReplay() { return !!this.last; }
}

export const playback = new Playback();
