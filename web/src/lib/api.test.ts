import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, NetworkError, apiFetch, setUnauthorizedHandler } from './api';

function respond(status: number, body?: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

describe('apiFetch', () => {
  const fetchMock = vi.fn();
  const unauthorized = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    unauthorized.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    setUnauthorizedHandler(unauthorized);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    setUnauthorizedHandler(null);
  });

  it('GET sends no CSRF header and no body, with same-origin credentials', async () => {
    fetchMock.mockResolvedValue(respond(200, { ok: true }));
    const result = await apiFetch('/api/status');
    expect(result).toEqual({ ok: true });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe('/api/status');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('same-origin');
    expect(init.headers['X-BizViz']).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it('non-GET sends X-BizViz and a JSON body', async () => {
    fetchMock.mockResolvedValue(respond(200, { data_version: 3 }));
    await apiFetch('/api/rules', { method: 'POST', body: { merchant_key: 'x' } });
    const init = fetchMock.mock.calls[0][1];
    expect(init.headers['X-BizViz']).toBe('1');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(init.body).toBe('{"merchant_key":"x"}');
  });

  it('sends X-BizViz on bodiless POST and DELETE', async () => {
    fetchMock.mockResolvedValue(respond(204));
    await apiFetch('/api/sync', { method: 'POST' });
    await apiFetch('/api/devices/1', { method: 'DELETE' });
    for (const call of fetchMock.mock.calls) expect(call[1].headers['X-BizViz']).toBe('1');
  });

  it('returns undefined for 204', async () => {
    fetchMock.mockResolvedValue(respond(204));
    expect(await apiFetch('/api/sync', { method: 'POST' })).toBeUndefined();
  });

  it('throws a typed ApiError from the error envelope', async () => {
    fetchMock.mockResolvedValue(respond(409, { error: { code: 'slot_limit', message: 'No slots' } }));
    const error = await apiFetch('/api/plaid/link-token', { method: 'POST' }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: 'slot_limit', message: 'No slots' });
  });

  it('survives non-JSON error bodies', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }));
    const error = await apiFetch('/api/data').catch((e) => e);
    expect(error).toMatchObject({ status: 500, code: 'http_error' });
  });

  it('keeps Retry-After on 429', async () => {
    fetchMock.mockResolvedValue(
      respond(429, { error: { code: 'rate_limited', message: 'slow down' } }, { 'Retry-After': '120' }),
    );
    const error = (await apiFetch('/api/auth/login', { method: 'POST', body: {} }).catch((e) => e)) as ApiError;
    expect(error.retryAfter).toBe('120');
  });

  it('calls the unauthorized handler on 401', async () => {
    fetchMock.mockResolvedValue(respond(401, { error: { code: 'unauthorized', message: 'no' } }));
    await expect(apiFetch('/api/data')).rejects.toBeInstanceOf(ApiError);
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it('does not treat a 401 from login as a logout', async () => {
    fetchMock.mockResolvedValue(respond(401, { error: { code: 'bad_password', message: 'no' } }));
    await expect(apiFetch('/api/auth/login', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 401,
    });
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it('does not log out on 400 bad_password from the password endpoint', async () => {
    fetchMock.mockResolvedValue(respond(400, { error: { code: 'bad_password', message: 'wrong' } }));
    await expect(apiFetch('/api/auth/password', { method: 'POST', body: {} })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it('maps fetch failures to NetworkError', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(apiFetch('/api/status')).rejects.toBeInstanceOf(NetworkError);
  });

  it('aborts and throws NetworkError after the timeout', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(
      (_path: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const pending = apiFetch('/api/status', { timeoutMs: 5000 }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(5001);
    expect(await pending).toBeInstanceOf(NetworkError);
  });
});
