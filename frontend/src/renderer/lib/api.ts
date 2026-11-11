// Backend client: REST helpers and a reconnecting WebSocket event stream.
import type { BackendInfo } from './bridge';

export type ServerEvent = { type: string; ts?: number; [k: string]: any };
type Handler = (e: ServerEvent) => void;

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: unknown) { super(message); }
}

export class BackendClient {
  private info: BackendInfo | null = null;
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private retry = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  connected = false;

  setInfo(info: BackendInfo) {
    const changed = info.url !== this.info?.url || info.token !== this.info?.token;
    this.info = info;
    if (info.status === 'ready' && changed) this.connect();
    if (info.status !== 'ready') this.disconnect(false);
  }

  get ready() { return !!this.info?.url && this.info.status === 'ready'; }

  onEvent(fn: Handler) {
    this.handlers.add(fn);
    return () => { this.handlers.delete(fn); };
  }

  private emit(e: ServerEvent) {
    for (const h of this.handlers) {
      try { h(e); } catch (err) { console.error('event handler failed', err); }
    }
  }

  private connect() {
    this.disconnect(false);
    this.closed = false;
    if (!this.info?.wsUrl || !this.info.token) return;
    const ws = new WebSocket(`${this.info.wsUrl}?token=${encodeURIComponent(this.info.token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.connected = true;
      this.emit({ type: 'ws.open' });
    };
    ws.onmessage = (m) => {
      try { this.emit(JSON.parse(m.data)); } catch { /* ignore malformed */ }
    };
    ws.onclose = () => {
      this.connected = false;
      if (this.ws === ws) this.ws = null;
      this.emit({ type: 'ws.close' });
      if (!this.closed && this.ready) {
        const delay = Math.min(8000, 300 * 2 ** this.retry++);
        this.timer = setTimeout(() => this.connect(), delay);
      }
    };
  }

  private disconnect(permanent: boolean) {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
    if (permanent) this.handlers.clear();
  }

  send(msg: Record<string, unknown>): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  async request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
    if (!this.info?.url || !this.info.token) throw new ApiError('Backend is not ready', 0);
    const res = await fetch(this.info.url + path, {
      method,
      headers: { 'X-Jarvis-Token': this.info.token, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const detail = data?.detail;
      const msg = typeof detail === 'string' ? detail
        : Array.isArray(detail) ? detail.map((d: any) => `${(d.loc ?? []).slice(-1)[0]}: ${d.msg}`).join('; ')
          : `Request failed (${res.status})`;
      throw new ApiError(msg, res.status, detail);
    }
    return data as T;
  }

  get = <T = any>(path: string) => this.request<T>('GET', path);
  post = <T = any>(path: string, body: unknown = {}) => this.request<T>('POST', path, body);
  put = <T = any>(path: string, body: unknown) => this.request<T>('PUT', path, body);
  patch = <T = any>(path: string, body: unknown) => this.request<T>('PATCH', path, body);
  del = <T = any>(path: string) => this.request<T>('DELETE', path);

  mediaUrl(path: string) {
    if (!this.info?.url || !this.info.token) return '';
    return `${this.info.url}${path}?token=${encodeURIComponent(this.info.token)}`;
  }
}

export const client = new BackendClient();
