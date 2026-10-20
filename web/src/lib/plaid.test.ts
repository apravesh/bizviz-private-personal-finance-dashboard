import 'fake-indexeddb/auto';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import { confirmDialog, confirmRequest } from './dialog';
import {
  LINK_SCRIPT_URL,
  PENDING_KEY,
  duplicateMessage,
  linkProgress,
  loadLinkScript,
  openLink,
  retryLeftoverExchange,
  submitPendingExchange,
} from './plaid';
import { resetForTests } from './store';
import type { ItemStatus } from './types';

const fetchMock = vi.fn();

function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

const item = (over: Partial<ItemStatus>): ItemStatus => ({
  item_id: 'i1',
  institution_id: 'ins_1',
  institution_name: 'First Bank',
  status: 'ok',
  error_code: null,
  consent_expires_at: null,
  last_success_at: null,
  removed: false,
  ...over,
});

interface FakeHandler {
  open: ReturnType<typeof vi.fn>;
  exit: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
}

let created: { config: Parameters<NonNullable<typeof window.Plaid>['create']>[0]; handler: FakeHandler }[];

beforeEach(() => {
  created = [];
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  sessionStorage.clear();
  resetForTests();
  window.Plaid = {
    create: (config) => {
      const handler = { open: vi.fn(), exit: vi.fn(), destroy: vi.fn() };
      created.push({ config, handler });
      return handler;
    },
  };
  location.hash = '';
});
afterEach(() => {
  delete window.Plaid;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetForTests();
});

function linkTokenFetch() {
  fetchMock.mockImplementation(async (path: string) => {
    if (path === '/api/plaid/link-token') return json(200, { link_token: `tok-${created.length}` });
    return json(404);
  });
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('loadLinkScript', () => {
  it('injects the Plaid script once, by URL', async () => {
    delete window.Plaid;
    document.head.innerHTML = '';
    const first = loadLinkScript();
    const second = loadLinkScript();
    expect(first).toBe(second);
    const scripts = document.querySelectorAll(`script[src="${LINK_SCRIPT_URL}"]`);
    expect(scripts).toHaveLength(1);
    expect(scripts[0].textContent).toBe('');
    scripts[0].dispatchEvent(new Event('load'));
    await first;
  });
});

describe('openLink', () => {
  it('requests a link token for the mode and opens the handler', async () => {
    linkTokenFetch();
    await openLink({ mode: 'bank', items: [] });
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toBe('/api/plaid/link-token');
    expect(JSON.parse(call[1].body)).toEqual({ mode: 'bank' });
    expect(created[0].config.token).toBe('tok-0');
    expect(created[0].handler.open).toHaveBeenCalled();
  });

  it('sends item_id in update mode', async () => {
    linkTokenFetch();
    await openLink({ mode: 'update', itemId: 'i9', items: [] });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ mode: 'update', item_id: 'i9' });
  });

  it('update-mode success marks the item repaired and never guards duplicates', async () => {
    fetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/plaid/link-token') return json(200, { link_token: 't' });
      if (path === '/api/plaid/items/i9/repaired') return json(204);
      if (path === '/api/data') return json(200, makePayload());
      return json(404);
    });
    const confirm = vi.fn();
    const onUpdated = vi.fn();
    await openLink({ mode: 'update', itemId: 'i9', items: [item({})], confirm, onUpdated });
    created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1' });
    expect(created[0].handler.exit).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    created[0].config.onSuccess('pub', {});
    await vi.waitFor(() => expect(onUpdated).toHaveBeenCalled());
    const repaired = fetchMock.mock.calls.find((c) => c[0] === '/api/plaid/items/i9/repaired')!;
    expect(repaired[1].method).toBe('POST');
    expect(repaired[1].headers['X-BizViz']).toBe('1');
  });

  describe('duplicate guard', () => {
    for (const eventName of ['SELECT_INSTITUTION', 'MATCHED_SELECT_INSTITUTION']) {
      it(`exits and confirms on ${eventName} for a linked institution`, async () => {
        linkTokenFetch();
        const confirm = vi.fn().mockResolvedValue(false);
        await openLink({ mode: 'bank', items: [item({})], confirm });
        created[0].config.onEvent(eventName, { institution_id: 'ins_1', institution_name: 'First Bank' });
        expect(created[0].handler.exit).toHaveBeenCalled();
        expect(confirm).toHaveBeenCalledWith(duplicateMessage('First Bank', false));
        await flush();
        expect(created).toHaveLength(1);
      });
    }

    it('uses the already-linked wording for live items', () => {
      expect(duplicateMessage('First Bank', false)).toBe(
        "First Bank is already linked. If it stopped updating, use Fix in Settings instead. Linking again uses another of your 10 slots and is only right for a different login at the same institution (for example a spouse's login). Continue?",
      );
    });

    it('uses the removed wording when only removed items match', async () => {
      linkTokenFetch();
      const confirm = vi.fn().mockResolvedValue(false);
      await openLink({ mode: 'bank', items: [item({ removed: true })], confirm });
      created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1', institution_name: 'First Bank' });
      expect(confirm).toHaveBeenCalledWith(
        'First Bank was linked before and disconnected. Linking again uses another slot; overlapping history will be merged, not double-counted. Continue?',
      );
    });

    it('prefers the live wording if both removed and live items match', async () => {
      linkTokenFetch();
      const confirm = vi.fn().mockResolvedValue(false);
      await openLink({
        mode: 'bank',
        items: [item({ removed: true }), item({ item_id: 'i2' })],
        confirm,
      });
      created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1', institution_name: 'First Bank' });
      expect(confirm.mock.calls[0][0]).toContain('is already linked');
    });

    it('ignores institutions that are not linked', async () => {
      linkTokenFetch();
      const confirm = vi.fn();
      await openLink({ mode: 'bank', items: [item({})], confirm });
      created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_other' });
      created[0].config.onEvent('OPEN', { institution_id: 'ins_1' });
      expect(created[0].handler.exit).not.toHaveBeenCalled();
      expect(confirm).not.toHaveBeenCalled();
    });

    it('Continue reopens with a fresh token and skips the guard for that institution', async () => {
      linkTokenFetch();
      const confirm = vi.fn().mockResolvedValue(true);
      await openLink({ mode: 'bank', items: [item({})], confirm });
      created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1', institution_name: 'First Bank' });
      await vi.waitFor(() => expect(created).toHaveLength(2));
      expect(created[1].config.token).toBe('tok-1');
      expect(created[1].handler.open).toHaveBeenCalled();
      created[1].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1', institution_name: 'First Bank' });
      expect(created[1].handler.exit).not.toHaveBeenCalled();
      expect(confirm).toHaveBeenCalledTimes(1);
      created[1].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_2' });
    });

    it('default confirm uses the app dialog', async () => {
      linkTokenFetch();
      await openLink({ mode: 'bank', items: [item({})] });
      created[0].config.onEvent('SELECT_INSTITUTION', { institution_id: 'ins_1', institution_name: 'First Bank' });
      const request = get(confirmRequest)!;
      expect(request.message).toContain('already linked');
      request.resolve(false);
      await flush();
      expect(get(confirmRequest)).toBeNull();
      expect(await Promise.race([confirmDialog('x'), Promise.resolve('pending')])).toBe('pending');
      get(confirmRequest)!.resolve(true);
    });
  });
});

describe('exchange', () => {
  const successMetadata = { institution: { institution_id: 'ins_9', name: 'Nine Bank' } };

  function exchangeFetch(responses: (Response | Error)[]) {
    let n = 0;
    fetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/plaid/link-token') return json(200, { link_token: 't' });
      if (path === '/api/plaid/exchange') {
        const r = responses[Math.min(n++, responses.length - 1)];
        if (r instanceof Error) throw r;
        return r.clone();
      }
      if (path === '/api/data') return json(200, makePayload());
      return json(404);
    });
    return () => fetchMock.mock.calls.filter((c) => c[0] === '/api/plaid/exchange');
  }

  it('persists the pending exchange, posts it, clears it and navigates to settings', async () => {
    const calls = exchangeFetch([json(200, { item_id: 'x', duplicate: false })]);
    await openLink({ mode: 'investment', items: [] });
    created[0].config.onSuccess('public-abc', successMetadata);
    const stored = JSON.parse(sessionStorage.getItem(PENDING_KEY)!);
    expect(stored).toMatchObject({
      public_token: 'public-abc',
      institution_id: 'ins_9',
      institution_name: 'Nine Bank',
      mode: 'investment',
    });
    expect(typeof stored.saved_at).toBe('string');
    await vi.waitFor(() => expect(location.hash).toBe('#/settings'));
    expect(JSON.parse(calls()[0][1].body)).toEqual({
      public_token: 'public-abc',
      institution_id: 'ins_9',
      institution_name: 'Nine Bank',
      mode: 'investment',
    });
    expect(calls()[0][1].headers['X-BizViz']).toBe('1');
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('retries with 1,2,4,8,16 s backoff on network errors and 5xx, then succeeds', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const calls = exchangeFetch([
      new TypeError('down'),
      json(502, { error: { code: 'x', message: 'y' } }),
      new TypeError('down'),
      json(503),
      new TypeError('down'),
      json(200, { item_id: 'x', duplicate: false }),
    ]);
    sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ public_token: 'p', institution_id: 'i', institution_name: 'N', mode: 'bank', saved_at: new Date().toISOString() }),
    );
    const done = submitPendingExchange();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls()).toHaveLength(1);
    for (const [delay, expected] of [
      [999, 1],
      [1, 2],
      [2000, 3],
      [4000, 4],
      [8000, 5],
      [16000, 6],
    ] as const) {
      await vi.advanceTimersByTimeAsync(delay);
      expect(calls()).toHaveLength(expected);
    }
    expect(await done).toBe(true);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('gives up after the last retry and keeps the pending entry', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const calls = exchangeFetch([new TypeError('down')]);
    sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ public_token: 'p', institution_id: 'i', institution_name: 'N', mode: 'bank', saved_at: new Date().toISOString() }),
    );
    const done = submitPendingExchange();
    await vi.advanceTimersByTimeAsync(31_000);
    expect(await done).toBe(false);
    expect(calls()).toHaveLength(6);
    expect(sessionStorage.getItem(PENDING_KEY)).not.toBeNull();
    expect(get(linkProgress).state).toBe('error');
  });

  it('does not retry on 4xx and keeps the entry', async () => {
    const calls = exchangeFetch([json(400, { error: { code: 'bad', message: 'bad' } })]);
    sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ public_token: 'p', institution_id: 'i', institution_name: 'N', mode: 'bank', saved_at: new Date().toISOString() }),
    );
    expect(await submitPendingExchange()).toBe(false);
    expect(calls()).toHaveLength(1);
    expect(sessionStorage.getItem(PENDING_KEY)).not.toBeNull();
  });

  it('retries a leftover entry younger than 30 minutes on app start', async () => {
    const calls = exchangeFetch([json(200, { item_id: 'x', duplicate: false })]);
    const now = Date.parse('2026-10-19T12:00:00Z');
    sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ public_token: 'p', institution_id: 'i', institution_name: 'N', mode: 'bank', saved_at: '2026-10-19T11:40:00Z' }),
    );
    expect(await retryLeftoverExchange(now)).toBe(true);
    expect(calls()).toHaveLength(1);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('drops a leftover entry older than 30 minutes without calling the API', async () => {
    const calls = exchangeFetch([json(200, {})]);
    const now = Date.parse('2026-10-19T12:00:00Z');
    sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ public_token: 'p', institution_id: 'i', institution_name: 'N', mode: 'bank', saved_at: '2026-10-19T11:29:00Z' }),
    );
    expect(await retryLeftoverExchange(now)).toBe(false);
    expect(calls()).toHaveLength(0);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('does nothing without a leftover entry or with a corrupt one', async () => {
    const calls = exchangeFetch([json(200, {})]);
    expect(await retryLeftoverExchange()).toBe(false);
    sessionStorage.setItem(PENDING_KEY, '{nope');
    expect(await retryLeftoverExchange()).toBe(false);
    expect(calls()).toHaveLength(0);
  });
});
