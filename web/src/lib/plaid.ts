import { ApiError, NetworkError, apiFetch } from './api';
import { confirmDialog } from './dialog';
import { refreshData } from './store';
import type { ItemStatus } from './types';
import { writable } from 'svelte/store';

export type LinkMode = 'bank' | 'investment' | 'loan' | 'update';

export const LINK_SCRIPT_URL = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js';
export const PENDING_KEY = 'bizviz.pendingExchange';
export const PENDING_MAX_AGE_MS = 30 * 60_000;
export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000];

interface PlaidMetadata {
  institution_id?: string | null;
  institution_name?: string | null;
  institution?: { institution_id: string; name: string } | null;
}

interface PlaidHandler {
  open(): void;
  exit(options?: { force?: boolean }): void;
  destroy?(): void;
}

interface PlaidCreateConfig {
  token: string;
  onSuccess(publicToken: string, metadata: PlaidMetadata): void;
  onExit(error: unknown, metadata: unknown): void;
  onEvent(eventName: string, metadata: PlaidMetadata): void;
}

declare global {
  interface Window {
    Plaid?: { create(config: PlaidCreateConfig): PlaidHandler };
  }
}

export interface PendingExchange {
  public_token: string;
  institution_id: string;
  institution_name: string;
  mode: LinkMode;
  saved_at: string;
}

export interface LinkProgress {
  state: 'idle' | 'saving' | 'error';
  message: string;
}

export const linkProgress = writable<LinkProgress>({ state: 'idle', message: '' });

export interface OpenLinkOptions {
  mode: LinkMode;
  itemId?: string;
  items: ItemStatus[];
  confirm?: (message: string) => Promise<boolean>;
  skipInstitutions?: ReadonlySet<string>;
  onUpdated?: () => void;
}

export function isLocalhost(): boolean {
  return location.hostname === 'localhost';
}

let scriptPromise: Promise<void> | null = null;

export function loadLinkScript(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = LINK_SCRIPT_URL;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      script.remove();
      scriptPromise = null;
      reject(new Error('Could not load Plaid Link'));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

export function duplicateMessage(name: string, removedOnly: boolean): string {
  return removedOnly
    ? `${name} was linked before and disconnected. Linking again uses another slot; overlapping history will be merged, not double-counted. Continue?`
    : `${name} is already linked. If it stopped updating, use Fix in Settings instead. Linking again uses another of your 10 slots and is only right for a different login at the same institution (for example a spouse's login). Continue?`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readPending(): PendingExchange | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as PendingExchange;
    return value && typeof value.public_token === 'string' ? value : null;
  } catch {
    return null;
  }
}

let exchangeInFlight: Promise<boolean> | null = null;

export function submitPendingExchange(): Promise<boolean> {
  if (exchangeInFlight) return exchangeInFlight;
  exchangeInFlight = (async () => {
    const pending = readPending();
    if (!pending) return false;
    linkProgress.set({ state: 'saving', message: `Saving ${pending.institution_name} connection…` });
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
      try {
        await apiFetch('/api/plaid/exchange', {
          method: 'POST',
          body: {
            public_token: pending.public_token,
            institution_id: pending.institution_id,
            institution_name: pending.institution_name,
            mode: pending.mode,
          },
        });
        sessionStorage.removeItem(PENDING_KEY);
        linkProgress.set({ state: 'idle', message: '' });
        await refreshData();
        return true;
      } catch (error) {
        const retryable = error instanceof NetworkError || (error instanceof ApiError && error.status >= 500);
        if (!retryable || attempt === RETRY_DELAYS_MS.length) {
          linkProgress.set({
            state: 'error',
            message: `Could not save the ${pending.institution_name} connection. It will be retried the next time BizViz opens.`,
          });
          return false;
        }
        await sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
    return false;
  })().finally(() => {
    exchangeInFlight = null;
  });
  return exchangeInFlight;
}

export async function retryLeftoverExchange(now = Date.now()): Promise<boolean> {
  const pending = readPending();
  if (!pending) return false;
  const age = now - Date.parse(pending.saved_at);
  if (!(age >= 0 && age < PENDING_MAX_AGE_MS)) {
    sessionStorage.removeItem(PENDING_KEY);
    return false;
  }
  const ok = await submitPendingExchange();
  if (ok) location.hash = '#/settings';
  return ok;
}

export async function openLink(options: OpenLinkOptions): Promise<void> {
  const { mode, itemId, items } = options;
  const confirm = options.confirm ?? confirmDialog;
  const skip = options.skipInstitutions ?? new Set<string>();

  await loadLinkScript();
  const body: { mode: LinkMode; item_id?: string } = { mode };
  if (mode === 'update') body.item_id = itemId;
  const { link_token } = await apiFetch<{ link_token: string }>('/api/plaid/link-token', {
    method: 'POST',
    body,
  });

  let handler: PlaidHandler | undefined;
  handler = window.Plaid!.create({
    token: link_token,
    onSuccess: (publicToken, metadata) => {
      if (mode === 'update') {
        void (async () => {
          try {
            await apiFetch(`/api/plaid/items/${itemId}/repaired`, { method: 'POST' });
            await refreshData();
            options.onUpdated?.();
          } catch {
            linkProgress.set({ state: 'error', message: 'Could not mark the connection as repaired.' });
          }
        })();
        return;
      }
      const pending: PendingExchange = {
        public_token: publicToken,
        institution_id: metadata.institution?.institution_id ?? '',
        institution_name: metadata.institution?.name ?? '',
        mode,
        saved_at: new Date().toISOString(),
      };
      sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
      void submitPendingExchange().then((ok) => {
        if (ok) location.hash = '#/settings';
      });
    },
    onExit: () => {
      handler?.destroy?.();
    },
    onEvent: (eventName, metadata) => {
      if (mode === 'update') return;
      if (eventName !== 'SELECT_INSTITUTION' && eventName !== 'MATCHED_SELECT_INSTITUTION') return;
      const institutionId = metadata.institution_id;
      if (!institutionId || skip.has(institutionId)) return;
      const matches = items.filter((item) => item.institution_id === institutionId);
      if (matches.length === 0) return;
      const removedOnly = matches.every((item) => item.removed);
      const name = metadata.institution_name ?? matches[0].institution_name ?? 'This institution';
      handler?.exit({ force: true });
      void confirm(duplicateMessage(name, removedOnly)).then((ok) => {
        if (!ok) return;
        openLink({ ...options, skipInstitutions: new Set([...skip, institutionId]) }).catch((error: Error) =>
          linkProgress.set({ state: 'error', message: error.message }),
        );
      });
    },
  });
  handler.open();
}
