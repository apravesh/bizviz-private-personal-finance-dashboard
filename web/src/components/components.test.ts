import 'fake-indexeddb/auto';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { makePayload, tx } from '../lib/__fixtures__/payload';
import { resetForTests, store } from '../lib/store';
import AccountsSection from './settings/AccountsSection.svelte';
import Login from './Login.svelte';
import TransactionEditor from './TransactionEditor.svelte';

let mounted: Record<string, unknown> | null = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function render(component: any, props: Record<string, unknown> = {}) {
  mounted = mount(component, { target: document.body, props });
  flushSync();
}

beforeEach(() => {
  resetForTests();
  vi.stubGlobal('fetch', vi.fn());
});
afterEach(() => {
  if (mounted) unmount(mounted);
  mounted = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('Login', () => {
  it('disables submit until a password is typed and shows the wrong-password message', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'no' } }), { status: 401 }),
    );
    render(Login);
    const button = document.querySelector<HTMLButtonElement>('button[type=submit]')!;
    expect(button.disabled).toBe(true);
    const input = document.querySelector<HTMLInputElement>('#login-password')!;
    input.value = 'nope';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(button.disabled).toBe(false);
    document.querySelector('form')!.dispatchEvent(new SubmitEvent('submit', { cancelable: true, bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('[role=alert]')?.textContent).toContain('not correct'));
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ method: 'POST' });
  });
});

describe('TransactionEditor', () => {
  const pendingTx = tx('p1', '2026-10-18', 12, { pending: true, merchant_key: 'cafe', merchant_display: 'Cafe' });

  it('disables apply-to-merchant for a pending transaction', () => {
    store.update((s) => ({ ...s, payload: makePayload(), online: true, loaded: true }));
    render(TransactionEditor, { tx: pendingTx });
    const checkbox = document.querySelector<HTMLInputElement>('input[type=checkbox]')!;
    expect(checkbox.disabled).toBe(true);
    expect(document.body.textContent).toContain('available once posted');
  });

  it('disables saving and explains why while the Mac is unreachable', () => {
    store.update((s) => ({ ...s, payload: makePayload(), online: false, loaded: true }));
    render(TransactionEditor, { tx: pendingTx });
    expect(document.querySelector('fieldset')!.disabled).toBe(true);
    expect(document.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(true);
    expect(document.body.textContent).toContain('unreachable');
  });
});

describe('AccountsSection', () => {
  it('flags unreviewed accounts', () => {
    const payload = makePayload();
    payload.accounts[0] = { ...payload.accounts[0], reviewed: false };
    store.update((s) => ({ ...s, payload, online: true, loaded: true }));
    render(AccountsSection);
    expect(document.querySelectorAll('.account.unreviewed')).toHaveLength(1);
    expect(document.body.textContent).toContain('needs review');
  });
});
