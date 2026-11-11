import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Markdown } from '../../frontend/src/renderer/lib/markdown';
import { BackendClient } from '../../frontend/src/renderer/lib/api';

describe('Markdown', () => {
  it('renders lists, code and emphasis', () => {
    const { container } = render(<Markdown text={'**Pending**\n- one\n- two `x`\n\n```\ncode\n```'} onLink={() => {}} />);
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.querySelector('strong')?.textContent).toBe('Pending');
    expect(container.querySelector('pre')?.textContent).toBe('code');
  });

  it('never injects HTML from model output', () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <script>alert(2)</script>'} onLink={() => {}} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<img');
  });

  it('links go through the handler, not navigation', () => {
    const onLink = vi.fn();
    const { container } = render(<Markdown text={'See [docs](https://example.com/a) and javascript:alert(1)'} onLink={onLink} />);
    const links = container.querySelectorAll('a');
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toBeNull();
    links[0].click();
    expect(onLink).toHaveBeenCalledWith('https://example.com/a');
  });
});

describe('BackendClient', () => {
  it('sends the token header and surfaces API errors', async () => {
    const c = new BackendClient();
    c.setInfo({ status: 'starting', url: null, wsUrl: null, token: null, error: null, restarts: 0 });
    await expect(c.get('/api/x')).rejects.toThrow('Backend is not ready');
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 422, text: async () => JSON.stringify({ detail: [{ loc: ['body', 'quota_threshold'], msg: 'too big' }] }) });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('WebSocket', class { close() {} } as any);
    c.setInfo({ status: 'ready', url: 'http://127.0.0.1:1', wsUrl: 'ws://127.0.0.1:1/ws', token: 'tkn', error: null, restarts: 0 });
    await expect(c.put('/api/settings', {})).rejects.toThrow('quota_threshold: too big');
    expect(fetchMock.mock.calls[0][1].headers['X-Jarvis-Token']).toBe('tkn');
    expect(c.mediaUrl('/api/tts/a.wav')).toBe('http://127.0.0.1:1/api/tts/a.wav?token=tkn');
    vi.unstubAllGlobals();
  });
});
