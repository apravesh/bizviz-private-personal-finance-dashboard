export class ApiError extends Error {
  status: number;
  code: string;
  retryAfter?: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export class NetworkError extends Error {
  constructor(message = 'Network request failed') {
    super(message);
    this.name = 'NetworkError';
  }
}

export interface ApiOptions {
  method?: string;
  body?: unknown;
  timeoutMs?: number;
}

const LOGIN_PATH = '/api/auth/login';
const DEFAULT_TIMEOUT_MS = 15000;

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export async function apiFetch<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = {};
  if (method !== 'GET') headers['X-BizViz'] = '1';
  let body: string | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    let response: Response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body,
        credentials: 'same-origin',
        signal: controller.signal,
      });
    } catch {
      throw new NetworkError();
    }

    if (response.status === 401 && path !== LOGIN_PATH) unauthorizedHandler?.();

    if (!response.ok) {
      let code = 'http_error';
      let message = `Request failed (${response.status})`;
      try {
        const data = await response.json();
        if (data?.error) {
          code = data.error.code ?? code;
          message = data.error.message ?? message;
        }
      } catch {
        // body was not JSON; keep the defaults
      }
      const error = new ApiError(response.status, code, message);
      const retryAfter = response.headers?.get?.('Retry-After');
      if (retryAfter) error.retryAfter = retryAfter;
      throw error;
    }

    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      if (controller.signal.aborted) throw new NetworkError('Request timed out');
      return undefined as T;
    }
  } finally {
    clearTimeout(timer);
  }
}
