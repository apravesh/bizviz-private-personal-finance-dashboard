import { get, writable } from 'svelte/store';
import { NetworkError, apiFetch, setUnauthorizedHandler } from './api';
import { deleteDataset, getDataset, putDataset } from './idb';
import type { DataPayload, Scope, StatusResponse, SystemResponse } from './types';
import { APP_VERSION } from './version';

export interface AppState {
  payload: DataPayload | null;
  fetchedAt: string | null;
  loaded: boolean;
  online: boolean;
  lastOkPoll: string | null;
  updateAvailable: boolean;
  needsLogin: boolean;
  system: SystemResponse | null;
  syncing: boolean;
  scope: Scope;
}

export const SCOPE_KEY = 'bizviz.scope';
const STATUS_TIMEOUT_MS = 5000;
const DATA_TIMEOUT_MS = 30000;
const POLL_INTERVAL_MS = 60_000;
const SYSTEM_INTERVAL_MS = 10 * 60_000;

function readScope(): Scope {
  try {
    return localStorage.getItem(SCOPE_KEY) === 'household' ? 'household' : 'mine';
  } catch {
    return 'mine';
  }
}

function initialState(): AppState {
  return {
    payload: null,
    fetchedAt: null,
    loaded: false,
    online: true,
    lastOkPoll: null,
    updateAvailable: false,
    needsLogin: false,
    system: null,
    syncing: false,
    scope: readScope(),
  };
}

export const store = writable<AppState>(initialState());

function patch(partial: Partial<AppState>): void {
  store.update((s) => ({ ...s, ...partial }));
}

let pollTimer: ReturnType<typeof setInterval> | null = null;
let systemTimer: ReturnType<typeof setInterval> | null = null;
let visibilityBound = false;
let pollInFlight: Promise<void> | null = null;
let dataInFlight: Promise<void> | null = null;
let systemLoaded = false;

function stopTimers(): void {
  if (pollTimer) clearInterval(pollTimer);
  if (systemTimer) clearInterval(systemTimer);
  pollTimer = null;
  systemTimer = null;
  if (visibilityBound) {
    document.removeEventListener('visibilitychange', onVisibility);
    visibilityBound = false;
  }
}

async function handleUnauthorized(): Promise<void> {
  stopTimers();
  patch({ payload: null, fetchedAt: null, needsLogin: true, system: null, loaded: true });
  try {
    await deleteDataset();
  } catch {
    // nothing cached or storage unavailable
  }
}

setUnauthorizedHandler(() => {
  void handleUnauthorized();
});

function onVisibility(): void {
  if (document.visibilityState === 'visible') void poll();
}

export function refreshData(): Promise<void> {
  if (dataInFlight) return dataInFlight;
  dataInFlight = (async () => {
    try {
      const payload = await apiFetch<DataPayload>('/api/data', { timeoutMs: DATA_TIMEOUT_MS });
      const fetchedAt = new Date().toISOString();
      patch({ payload, fetchedAt, online: true });
      try {
        await putDataset({ payload, fetched_at: fetchedAt });
      } catch {
        // cache write failure only costs the offline copy
      }
    } catch (error) {
      if (error instanceof NetworkError) patch({ online: false });
    } finally {
      dataInFlight = null;
    }
  })();
  return dataInFlight;
}

export async function loadSystem(): Promise<void> {
  try {
    const system = await apiFetch<SystemResponse>('/api/system');
    patch({ system });
  } catch {
    // stale or missing system info only hides warnings
  }
}

export function poll(): Promise<void> {
  if (pollInFlight) return pollInFlight;
  pollInFlight = (async () => {
    try {
      const status = await apiFetch<StatusResponse>('/api/status', { timeoutMs: STATUS_TIMEOUT_MS });
      patch({
        online: true,
        lastOkPoll: new Date().toISOString(),
        syncing: status.syncing,
        needsLogin: false,
        ...(status.app_version !== APP_VERSION ? { updateAvailable: true } : {}),
      });
      if (!systemLoaded) {
        systemLoaded = true;
        void loadSystem();
      }
      if (status.data_version !== get(store).payload?.data_version) await refreshData();
    } catch (error) {
      if (error instanceof NetworkError) patch({ online: false });
    } finally {
      pollInFlight = null;
    }
  })();
  return pollInFlight;
}

export async function start(): Promise<void> {
  stopTimers();
  systemLoaded = false;
  patch({ needsLogin: false });

  if (!get(store).payload) {
    try {
      const cached = await getDataset();
      if (cached) patch({ payload: cached.payload, fetchedAt: cached.fetched_at });
    } catch {
      // unreadable cache: start from the network
    }
  }
  patch({ loaded: true });

  pollTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void poll();
  }, POLL_INTERVAL_MS);
  systemTimer = setInterval(() => {
    if (get(store).online) void loadSystem();
  }, SYSTEM_INTERVAL_MS);
  document.addEventListener('visibilitychange', onVisibility);
  visibilityBound = true;

  await poll();
}

export async function logout(): Promise<void> {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST', timeoutMs: 5000 });
  } catch {
    // the local state is dropped regardless
  }
  await handleUnauthorized();
}

export function setScope(scope: Scope): void {
  try {
    localStorage.setItem(SCOPE_KEY, scope);
  } catch {
    // private mode: the choice just will not persist
  }
  patch({ scope });
}

export async function afterEdit(dataVersion: number | undefined): Promise<void> {
  if (dataVersion === undefined || dataVersion !== get(store).payload?.data_version) await refreshData();
}

export function resetForTests(): void {
  stopTimers();
  pollInFlight = null;
  dataInFlight = null;
  systemLoaded = false;
  store.set(initialState());
}
