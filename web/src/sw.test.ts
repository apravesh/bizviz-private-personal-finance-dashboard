import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(resolve(__dirname, '../public/sw.js'), 'utf-8').replaceAll('__BUILD_ID__', 'b1');

type Listener = (event: any) => void;

class FakeCache {
  entries = new Map<string, any>();
  async addAll(urls: string[]) {
    for (const u of urls) this.entries.set(new URL(u, 'https://bizviz.local').pathname, `body:${u}`);
  }
  async match(req: any) {
    const url = typeof req === 'string' ? req : req.url;
    return this.entries.get(new URL(url, 'https://bizviz.local').pathname);
  }
  async put(req: any, res: any) {
    this.entries.set(new URL(req.url).pathname, res);
  }
}

function boot(search = '?v=1.0.0%2Babc', existing: string[] = []) {
  const listeners: Record<string, Listener> = {};
  const stores = new Map<string, FakeCache>(existing.map((n) => [n, new FakeCache()]));
  const fetchMock = vi.fn(async (input: any) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url.endsWith('/asset-manifest.json')) {
      return { ok: true, json: async () => ({ assets: ['/assets/app-1.js', '/assets/app-1.css'] }) };
    }
    return { ok: true, type: 'basic', clone() { return this; }, tag: `net:${url}` };
  });
  const self = {
    location: { href: `https://bizviz.local/sw.js${search}`, origin: 'https://bizviz.local' },
    addEventListener: (type: string, fn: Listener) => (listeners[type] = fn),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => undefined) },
  };
  const caches = {
    open: async (name: string) => {
      if (!stores.has(name)) stores.set(name, new FakeCache());
      return stores.get(name)!;
    },
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
  };
  runInNewContext(source, { self, caches, fetch: fetchMock, URL, Promise, console });
  const wait = async (type: string, event: any = {}) => {
    let pending: Promise<unknown> = Promise.resolve();
    listeners[type]({ ...event, waitUntil: (p: Promise<unknown>) => (pending = p) });
    await pending;
  };
  const request = (path: string, init: Record<string, unknown> = {}) => ({
    url: `https://bizviz.local${path}`,
    method: 'GET',
    mode: 'cors',
    ...init,
  });
  const dispatchFetch = (req: any) => {
    let responded: Promise<any> | undefined;
    listeners.fetch({ request: req, respondWith: (p: Promise<any>) => (responded = p) });
    return responded;
  };
  return { self, stores, fetchMock, wait, request, dispatchFetch, listeners };
}

describe('service worker', () => {
  it('precaches the shell and the hashed assets under a versioned cache name', async () => {
    const sw = boot();
    await sw.wait('install');
    const name = 'bizviz-shell-1.0.0+abc-b1';
    expect([...sw.stores.keys()]).toEqual([name]);
    const cached = [...sw.stores.get(name)!.entries.keys()];
    expect(cached).toEqual(
      expect.arrayContaining(['/', '/index.html', '/manifest.webmanifest', '/assets/app-1.js', '/assets/app-1.css']),
    );
    expect(sw.self.skipWaiting).not.toHaveBeenCalled();
  });

  it('never intercepts /api/ requests', () => {
    const sw = boot();
    expect(sw.dispatchFetch(sw.request('/api/data'))).toBeUndefined();
    expect(sw.dispatchFetch(sw.request('/api/status?x=1'))).toBeUndefined();
  });

  it('ignores non-GET, cross-origin and update-check requests', () => {
    const sw = boot();
    expect(sw.dispatchFetch(sw.request('/assets/a.js', { method: 'POST' }))).toBeUndefined();
    expect(sw.dispatchFetch({ url: 'https://cdn.plaid.com/x.js', method: 'GET', mode: 'cors' })).toBeUndefined();
    expect(sw.dispatchFetch(sw.request('/version.json'))).toBeUndefined();
    expect(sw.dispatchFetch(sw.request('/sw.js'))).toBeUndefined();
  });

  it('serves shell assets cache-first and falls back to the network', async () => {
    const sw = boot();
    await sw.wait('install');
    expect(await sw.dispatchFetch(sw.request('/assets/app-1.js'))).toBe('body:/assets/app-1.js');
    expect(sw.fetchMock).toHaveBeenCalledTimes(1); // only the manifest during install
    const other = await sw.dispatchFetch(sw.request('/assets/new.js'));
    expect(other.tag).toBe('net:https://bizviz.local/assets/new.js');
  });

  it('answers navigations with the cached index.html, even with the network down', async () => {
    const sw = boot();
    await sw.wait('install');
    sw.fetchMock.mockRejectedValue(new TypeError('offline'));
    expect(await sw.dispatchFetch(sw.request('/', { mode: 'navigate' }))).toBe('body:/index.html');
    expect(await sw.dispatchFetch(sw.request('/anything', { mode: 'navigate' }))).toBe('body:/index.html');
  });

  it('skips waiting only on the SKIP_WAITING message', () => {
    const sw = boot();
    sw.listeners.message({ data: { type: 'other' } });
    expect(sw.self.skipWaiting).not.toHaveBeenCalled();
    sw.listeners.message({ data: { type: 'SKIP_WAITING' } });
    expect(sw.self.skipWaiting).toHaveBeenCalledTimes(1);
  });

  it('on activate deletes old shell caches only, then claims clients', async () => {
    const current = 'bizviz-shell-1.0.0+abc-b1';
    const sw = boot('?v=1.0.0%2Babc', ['bizviz-shell-0.9-old', current, 'unrelated']);
    await sw.wait('activate');
    expect([...sw.stores.keys()].sort()).toEqual([current, 'unrelated']);
    expect(sw.self.clients.claim).toHaveBeenCalled();
  });
});
