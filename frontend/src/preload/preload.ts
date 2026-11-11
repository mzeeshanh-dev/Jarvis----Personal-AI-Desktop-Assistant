// Restricted bridge exposed to the renderer. No Node/OS primitives are
// exposed; every call maps to a single validated IPC handler in main.
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

type Listener = (payload: unknown) => void;
const ALLOWED_EVENTS = new Set(['backend:status', 'navigate', 'shortcut:status']);

const api = {
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  shutdown: (emergency: boolean) => ipcRenderer.invoke('app:shutdown', emergency === true),
  version: () => ipcRenderer.invoke('app:version'),
  appMetrics: () => ipcRenderer.invoke('app:metrics'),
  showMain: (route?: string) => ipcRenderer.invoke('window:show-main', route),
  showFloat: () => ipcRenderer.invoke('window:show-float'),
  hideFloat: () => ipcRenderer.invoke('window:hide-float'),
  minimize: () => ipcRenderer.invoke('window:minimize'),
  applyDesktop: (settings: Record<string, unknown>) => ipcRenderer.invoke('desktop:apply', settings),
  shortcutStatus: () => ipcRenderer.invoke('shortcut:status'),
  openExternal: (url: string) => ipcRenderer.invoke('shell:open-external', url),
  openFolder: (which: 'logs' | 'data' | 'screenshots') => ipcRenderer.invoke('shell:open-folder', which),
  on: (channel: string, fn: Listener) => {
    if (!ALLOWED_EVENTS.has(channel)) throw new Error(`event ${channel} not allowed`);
    const wrapped = (_e: IpcRendererEvent, payload: unknown) => fn(payload);
    ipcRenderer.on(channel, wrapped);
    return () => { ipcRenderer.removeListener(channel, wrapped); };
  },
};

contextBridge.exposeInMainWorld('jarvis', api);
export type JarvisBridge = typeof api;
