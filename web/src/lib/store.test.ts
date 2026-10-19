import 'fake-indexeddb/auto';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import { deleteDataset, getDataset, putDataset } from './idb';
import { logout, resetForTests, setScope, start, store } from './store';
import { APP_VERSION } from './version';
import type { DataPayload, StatusResponse, SystemResponse } from './types';

const fetchMock = vi.fn();

function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

function status(over: Partial<StatusResponse> = {}): StatusResponse {
  return {
    app_version: APP_VERSION,
    data_version: 7,
    newest_data_at: null,
    items: [],
    syncing: false,
    ...over,
  };
}

const system = { app_version: APP_VERSION, recovered_items: [] } as unknown as SystemResponse;

interface Server {
  status: StatusResponse | Error | number;
  data: DataPayload;
}

function serve(server: Server) {
  fetchMock.mockImplementation(async (path: string) => {
    if (path === '/api/status') {
      if (server.status instanceof Error) throw server.status;
      if (typeof server.status === 'number') return json(server.status, { error: { code: 'unauthorized', message: '' } });
      return json(200, server.status);
    }
    if (path === '/api/data') return json(200, server.data);
    if (path === '/api/system') return json(200, system);
    if (path === '/api/auth/logout') return json(204);
    return json(404);
  });
}

const paths = () => fetchMock.mock.calls.map((c) => c[0]);

describe('store', () => {
  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] });
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    localStorage.clear();
    resetForTests();
    await deleteDataset();
  });
  afterEach(() => {
    resetForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('renders the cached dataset before any network call completes', async () => {
    await putDataset({ payload: makePayload(), fetched_at: '2026-10-19T00:00:00Z' });
    let release: (r: Response) => void = () => {};
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const started = start();
    await vi.waitFor(() => expect(get(store).payload?.data_version).toBe(7));
    expect(get(store).fetchedAt).toBe('2026-10-19T00:00:00Z');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/status');
    release(json(200, status()));
    await started;
  });

  it('does not refetch data when data_version matches', async () => {
    await putDataset({ payload: makePayload(), fetched_at: 'x' });
    serve({ status: status({ data_version: 7 }), data: makePayload() });
    await start();
    expect(paths()).not.toContain('/api/data');
    expect(get(store).online).toBe(true);
    expect(get(store).lastOkPoll).not.toBeNull();
  });

  it('fetches data when the version differs and replaces the cache', async () => {
    await putDataset({ payload: makePayload(), fetched_at: 'x' });
    const fresh = { ...makePayload(), data_version: 8 };
    serve({ status: status({ data_version: 8 }), data: fresh });
    await start();
    expect(get(store).payload?.data_version).toBe(8);
    expect((await getDataset())?.payload.data_version).toBe(8);
  });

  it('fetches data when there is no cache', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    expect(get(store).payload?.data_version).toBe(7);
    expect(get(store).needsLogin).toBe(false);
  });

  it('flags updateAvailable when app_version differs', async () => {
    serve({ status: status({ app_version: 'other' }), data: makePayload() });
    await start();
    expect(get(store).updateAvailable).toBe(true);
  });

  it('does not flag updateAvailable when versions match', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    expect(get(store).updateAvailable).toBe(false);
  });

  it('fetches system on start and every 10 minutes when online', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    await vi.waitFor(() => expect(get(store).system).not.toBeNull());
    const count = () => paths().filter((p) => p === '/api/system').length;
    expect(count()).toBe(1);
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(count()).toBe(2);
  });

  it('polls every 60 s while visible', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    const count = () => paths().filter((p) => p === '/api/status').length;
    expect(count()).toBe(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(count()).toBe(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(count()).toBe(3);
  });

  it('skips interval polls while hidden and polls on becoming visible', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    const count = () => paths().filter((p) => p === '/api/status').length;
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await vi.advanceTimersByTimeAsync(120_000);
    expect(count()).toBe(1);
    visibility.mockReturnValue('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(count()).toBe(2);
    visibility.mockRestore();
  });

  it('goes offline on a network error, keeps the payload, recovers on the next poll', async () => {
    await putDataset({ payload: makePayload(), fetched_at: 'x' });
    const server: Server = { status: new TypeError('offline'), data: makePayload() };
    serve(server);
    await start();
    expect(get(store).online).toBe(false);
    expect(get(store).payload?.data_version).toBe(7);
    server.status = status();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get(store).online).toBe(true);
  });

  it('treats a status timeout as offline', async () => {
    fetchMock.mockImplementation(
      (_p: string, init: RequestInit) =>
        new Promise((_res, rej) => {
          init.signal!.addEventListener('abort', () => rej(new DOMException('a', 'AbortError')));
        }),
    );
    const started = start();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(5001);
    await started;
    expect(get(store).online).toBe(false);
  });

  it('401 clears the payload, the cache and asks for login', async () => {
    await putDataset({ payload: makePayload(), fetched_at: 'x' });
    serve({ status: 401, data: makePayload() });
    await start();
    await vi.waitFor(() => expect(get(store).needsLogin).toBe(true));
    expect(get(store).payload).toBeNull();
    await vi.waitFor(async () => expect(await getDataset()).toBeNull());
    const before = paths().length;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(paths().length).toBe(before);
  });

  it('logout posts, deletes the cache and needs login', async () => {
    serve({ status: status(), data: makePayload() });
    await start();
    await logout();
    const call = fetchMock.mock.calls.find((c) => c[0] === '/api/auth/logout')!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers['X-BizViz']).toBe('1');
    expect(get(store).needsLogin).toBe(true);
    expect(get(store).payload).toBeNull();
    expect(await getDataset()).toBeNull();
  });

  it('persists the scope in localStorage and defaults to mine', () => {
    expect(get(store).scope).toBe('mine');
    setScope('household');
    expect(localStorage.getItem('bizviz.scope')).toBe('household');
    expect(get(store).scope).toBe('household');
  });

  it('reads the persisted scope on creation', async () => {
    localStorage.setItem('bizviz.scope', 'household');
    resetForTests();
    expect(get(store).scope).toBe('household');
  });
});
