import { describe, expect, it } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import { computeBanners, isIosSafari, type BannerContext } from './banners';
import type { AppState } from './store';
import type { SystemResponse } from './types';

const NOW = Date.parse('2026-10-19T12:00:00Z');

function system(over: Partial<SystemResponse> = {}): SystemResponse {
  return {
    app_version: 'x',
    filevault: true,
    time_machine: true,
    cert_expires_at: '2027-10-19T00:00:00Z',
    ca_expires_at: '2030-01-01T00:00:00Z',
    ca_expiring: false,
    hostname: 'mac.local',
    current_hostname: 'mac.local',
    hostname_mismatch: false,
    plaid_env: 'sandbox',
    plaid_configured: true,
    plaid_items_created_count: 1,
    recovered_items: [],
    login_locked_until: null,
    ...over,
  };
}

function ctx(over: Partial<BannerContext> = {}, state: Partial<AppState> = {}): BannerContext {
  return {
    state: {
      payload: makePayload(),
      fetchedAt: null,
      loaded: true,
      online: true,
      lastOkPoll: null,
      updateAvailable: false,
      needsLogin: false,
      system: system(),
      syncing: false,
      scope: 'mine',
      ...state,
    },
    isLocal: false,
    standalone: true,
    iosSafari: false,
    dismissed: new Set(),
    now: NOW,
    ...over,
  };
}

const ids = (c: BannerContext) => computeBanners(c).map((b) => b.id);

describe('computeBanners', () => {
  it('has no banners when everything is healthy', () => {
    expect(computeBanners(ctx())).toEqual([]);
  });

  it('shows the offline banner with as-of and since times', () => {
    const [banner] = computeBanners(ctx({}, { online: false, lastOkPoll: '2026-10-19T11:00:00Z' }));
    expect(banner.id).toBe('offline');
    expect(banner.text).toMatch(/^Showing data as of .*Mac unreachable since /);
    expect(banner.text).not.toContain('this session');
    expect(banner.dismissKey).toBeUndefined();
  });

  it('falls back to "this session" without a successful poll', () => {
    const [banner] = computeBanners(ctx({}, { online: false }));
    expect(banner.text).toContain('Mac unreachable since this session');
  });

  it('shows the update banner with a Reload action', () => {
    const [banner] = computeBanners(ctx({}, { updateAvailable: true }));
    expect(banner.action?.kind).toBe('reload');
  });

  it('flags broken connections with Fix only on localhost', () => {
    const state = ctx().state;
    state.payload!.status.items[0].status = 'error';
    state.payload!.status.items[0].last_success_at = '2026-10-10T00:00:00Z';
    const remote = computeBanners(ctx({ state }));
    expect(remote[0].text).toContain("First Bank hasn't updated since");
    expect(remote[0].text).toContain('numbers may be incomplete');
    expect(remote[0].action).toBeUndefined();
    const local = computeBanners(ctx({ state, isLocal: true }));
    expect(local[0].action).toMatchObject({ kind: 'fix', itemId: 'item-1' });
    expect(local[0].dismissKey).toBeUndefined();
  });

  it('flags consent expiring within 7 days, and not later', () => {
    const state = ctx().state;
    state.payload!.status.items[0].consent_expires_at = '2026-10-25T00:00:00Z';
    expect(ids(ctx({ state }))).toContain('item:item-1');
    state.payload!.status.items[0].consent_expires_at = '2026-11-25T00:00:00Z';
    expect(ids(ctx({ state }))).not.toContain('item:item-1');
  });

  it('ignores removed items', () => {
    const state = ctx().state;
    state.payload!.status.items[0].status = 'error';
    state.payload!.status.items[0].removed = true;
    expect(ids(ctx({ state }))).toEqual([]);
  });

  it('asks to review new accounts only on localhost', () => {
    const state = ctx().state;
    state.payload!.accounts[0].reviewed = false;
    expect(ids(ctx({ state }))).not.toContain('review');
    const banner = computeBanners(ctx({ state, isLocal: true })).find((b) => b.id === 'review')!;
    expect(banner.text).toBe('1 new account to review.');
    expect(banner.action?.href).toBe('#/settings');
  });

  it('shows recovered connections, dismissible by exact key', () => {
    const state = ctx({}, { system: system({ recovered_items: ['First Bank'] }) });
    const [banner] = computeBanners(state);
    expect(banner.dismissKey).toBe('recovered:First Bank');
    expect(ids({ ...state, dismissed: new Set(['recovered:First Bank']) })).toEqual([]);
  });

  it('shows duplicate and superseded accounts with duplicate:<account_id> keys', () => {
    const state = ctx().state;
    state.payload!.quality.duplicate_accounts = ['acc-card'];
    state.payload!.quality.superseded_accounts = [
      { account_id: 'acc-joint', superseded_by: 'acc-checking', from_date: '2026-09-01' },
    ];
    const banners = computeBanners(ctx({ state }));
    expect(banners.map((b) => b.dismissKey)).toEqual(['duplicate:acc-card', 'duplicate:acc-joint']);
    expect(banners[1].text).toContain('superseded by First Bank Checking');
    const hidden = computeBanners(ctx({ state, dismissed: new Set(['duplicate:acc-card']) }));
    expect(hidden.map((b) => b.dismissKey)).toEqual(['duplicate:acc-joint']);
  });

  it('counts suspect card payments from the last 90 days only', () => {
    const state = ctx().state;
    state.payload!.quality.suspect_card_payments = { count: 2, transaction_ids: ['t1', 't8'] };
    // t1 is 2026-08-03 (77 days ago), t8 is 2026-10-05
    expect(computeBanners(ctx({ state }))[0].text).toBe(
      "2 card payments couldn't be matched to a linked card.",
    );
    state.payload!.transactions[0].date = '2026-06-01';
    const banner = computeBanners(ctx({ state }))[0];
    expect(banner.text).toBe("1 card payment couldn't be matched to a linked card.");
    expect(banner.dismissKey).toBe('suspect');
    expect(banner.action?.href).toBe('#/transactions?suspect=1');
    expect(ids(ctx({ state, dismissed: new Set(['suspect']) }))).toEqual([]);
  });

  it('shows the Add to Home Screen hint only on non-standalone iOS Safari until dismissed', () => {
    expect(ids(ctx({ iosSafari: true, standalone: false }))).toEqual(['a2hs']);
    expect(ids(ctx({ iosSafari: true, standalone: true }))).toEqual([]);
    expect(ids(ctx({ iosSafari: false, standalone: false }))).toEqual([]);
    expect(ids(ctx({ iosSafari: true, standalone: false, dismissed: new Set(['a2hs']) }))).toEqual([]);
  });

  it('detects iOS Safari', () => {
    expect(isIosSafari('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Mobile Safari/604.1')).toBe(true);
    expect(isIosSafari('Mozilla/5.0 (iPhone) CriOS/120 Mobile Safari')).toBe(false);
    expect(isIosSafari('Mozilla/5.0 (Macintosh) Chrome/120 Safari/537')).toBe(false);
  });

  it('shows live system warnings, never dismissible, even when keys are dismissed', () => {
    const all = ctx(
      { dismissed: new Set(['filevault', 'time-machine', 'cert']) },
      {
        system: system({
          filevault: false,
          time_machine: false,
          cert_expires_at: '2026-11-01T00:00:00Z',
          ca_expiring: true,
          hostname_mismatch: true,
          current_hostname: 'new.local',
          login_locked_until: '2026-10-19T13:00:00Z',
        }),
      },
    );
    const banners = computeBanners(all);
    expect(banners.map((b) => b.id)).toEqual(['filevault', 'time-machine', 'cert', 'ca', 'hostname', 'locked']);
    expect(banners.every((b) => b.dismissKey === undefined)).toBe(true);
  });

  it('treats unknown FileVault/Time Machine state as no warning', () => {
    expect(ids(ctx({}, { system: system({ filevault: null, time_machine: null }) }))).toEqual([]);
  });

  it('ignores an expired login lock', () => {
    expect(ids(ctx({}, { system: system({ login_locked_until: '2026-10-19T11:00:00Z' }) }))).toEqual([]);
  });
});
