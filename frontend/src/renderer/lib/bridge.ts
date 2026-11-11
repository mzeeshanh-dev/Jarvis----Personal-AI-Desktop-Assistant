// Typed access to the preload bridge (window.jarvis).
export type BackendStatus = 'stopped' | 'starting' | 'ready' | 'restarting' | 'failed';

export interface BackendInfo {
  status: BackendStatus;
  url: string | null;
  wsUrl: string | null;
  token: string | null;
  error: string | null;
  restarts: number;
}

export interface JarvisBridge {
  backendInfo(): Promise<BackendInfo>;
  restartBackend(): Promise<BackendInfo>;
  shutdown(emergency: boolean): Promise<void>;
  version(): Promise<{ version: string; electron: string; packaged: boolean }>;
  appMetrics(): Promise<{ pid: number; type: string; cpu: number; memoryMB: number }[]>;
  showMain(route?: string): Promise<void>;
  showFloat(): Promise<void>;
  hideFloat(): Promise<void>;
  minimize(): Promise<void>;
  applyDesktop(settings: Record<string, unknown>): Promise<{ ok: boolean; error?: string }>;
  shortcutStatus(): Promise<{ shortcut: string | null; error: string | null }>;
  openExternal(url: string): Promise<void>;
  openFolder(which: 'logs' | 'data' | 'screenshots'): Promise<string>;
  on(channel: 'backend:status' | 'navigate' | 'shortcut:status', fn: (payload: any) => void): () => void;
}

declare global {
  interface Window { jarvis?: JarvisBridge }
}

export const bridge = (): JarvisBridge => {
  if (!window.jarvis) throw new Error('Jarvis bridge unavailable (not running inside Electron)');
  return window.jarvis;
};
