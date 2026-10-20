import { accountLabel } from './aggregate';
import { formatDate, formatDateTime } from './format';
import type { AppState } from './store';

export interface BannerAction {
  label: string;
  kind: 'reload' | 'fix' | 'link';
  itemId?: string;
  href?: string;
}

export interface Banner {
  id: string;
  level: 'info' | 'warn';
  text: string;
  dismissKey?: string;
  action?: BannerAction;
}

export interface BannerContext {
  state: AppState;
  isLocal: boolean;
  standalone: boolean;
  iosSafari: boolean;
  dismissed: ReadonlySet<string>;
  now: number;
}

const DAY_MS = 86_400_000;
const EXPIRY_WINDOW_DAYS = 7;
const CERT_WINDOW_DAYS = 30;
const SUSPECT_WINDOW_DAYS = 90;

export function isIosSafari(userAgent: string): boolean {
  return /iPhone|iPad|iPod/.test(userAgent) && !/CriOS|FxiOS|EdgiOS/.test(userAgent);
}

function within(iso: string | null | undefined, days: number, now: number): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return !Number.isNaN(t) && t - now <= days * DAY_MS;
}

export function computeBanners(ctx: BannerContext): Banner[] {
  const { state, isLocal, now } = ctx;
  const { payload, system } = state;
  const banners: Banner[] = [];
  const dismissible = (key: string) => !ctx.dismissed.has(key);

  if (!state.online) {
    const since = state.lastOkPoll ? formatDateTime(state.lastOkPoll) : 'this session';
    const asOf = payload ? formatDateTime(payload.status.newest_data_at) : 'unknown';
    banners.push({
      id: 'offline',
      level: 'warn',
      text: payload
        ? `Showing data as of ${asOf}. Mac unreachable since ${since}.`
        : `No data is saved on this device. Mac unreachable since ${since}.`,
    });
  }

  if (state.updateAvailable) {
    banners.push({
      id: 'update',
      level: 'info',
      text: 'BizViz was updated.',
      action: { label: 'Reload', kind: 'reload' },
    });
  }

  if (payload) {
    for (const item of payload.status.items) {
      if (item.removed) continue;
      const name = item.institution_name ?? 'A connection';
      const action: BannerAction | undefined = isLocal
        ? { label: 'Fix', kind: 'fix', itemId: item.item_id }
        : undefined;
      if (item.status !== 'ok') {
        const since = item.last_success_at ? formatDateTime(item.last_success_at) : 'it was linked';
        banners.push({
          id: `item:${item.item_id}`,
          level: 'warn',
          text: `${name} hasn't updated since ${since}; numbers may be incomplete.`,
          action,
        });
      } else if (within(item.consent_expires_at, EXPIRY_WINDOW_DAYS, now)) {
        banners.push({
          id: `item:${item.item_id}`,
          level: 'warn',
          text: `${name} access expires ${formatDate(item.consent_expires_at)}; it will stop updating after that.`,
          action,
        });
      }
    }

    const unreviewed = payload.accounts.filter((a) => !a.reviewed && !a.removed);
    if (isLocal && unreviewed.length > 0) {
      banners.push({
        id: 'review',
        level: 'info',
        text: `${unreviewed.length} new account${unreviewed.length === 1 ? '' : 's'} to review.`,
        action: { label: 'Review', kind: 'link', href: '#/settings' },
      });
    }
  }

  if (system) {
    for (const name of system.recovered_items) {
      const key = `recovered:${name}`;
      if (!dismissible(key)) continue;
      banners.push({
        id: key,
        level: 'info',
        text: `${name} was recovered from the Keychain backup; its history is being downloaded again.`,
        dismissKey: key,
      });
    }
  }

  if (payload) {
    const accounts = new Map(payload.accounts.map((a) => [a.account_id, a]));
    const label = (id: string) => accountLabel(accounts.get(id), id);
    for (const id of payload.quality.duplicate_accounts) {
      const key = `duplicate:${id}`;
      if (!dismissible(key)) continue;
      banners.push({
        id: key,
        level: 'info',
        text: `${label(id)} duplicates another linked account and is excluded from totals.`,
        dismissKey: key,
      });
    }
    for (const s of payload.quality.superseded_accounts) {
      const key = `duplicate:${s.account_id}`;
      if (!dismissible(key)) continue;
      banners.push({
        id: `superseded:${s.account_id}`,
        level: 'info',
        text: `${label(s.account_id)} was superseded by ${label(s.superseded_by)}${s.from_date ? ` from ${formatDate(s.from_date)}` : ''}.`,
        dismissKey: key,
      });
    }

    const recent = new Set(
      payload.quality.suspect_card_payments.transaction_ids,
    );
    const cutoff = new Date(now - SUSPECT_WINDOW_DAYS * DAY_MS).toISOString().slice(0, 10);
    const suspectCount = payload.transactions.filter((t) => recent.has(t.id) && t.date >= cutoff).length;
    if (suspectCount > 0 && dismissible('suspect')) {
      banners.push({
        id: 'suspect',
        level: 'info',
        text: `${suspectCount} card payment${suspectCount === 1 ? '' : 's'} couldn't be matched to a linked card.`,
        dismissKey: 'suspect',
        action: { label: 'Review', kind: 'link', href: '#/transactions?suspect=1' },
      });
    }
  }

  if (ctx.iosSafari && !ctx.standalone && dismissible('a2hs')) {
    banners.push({
      id: 'a2hs',
      level: 'info',
      text: 'To install BizViz on this iPhone: tap Share, then Add to Home Screen.',
      dismissKey: 'a2hs',
    });
  }

  if (system) {
    if (system.filevault === false) {
      banners.push({ id: 'filevault', level: 'warn', text: 'FileVault is off on the Mac.' });
    }
    if (system.time_machine === false) {
      banners.push({ id: 'time-machine', level: 'warn', text: 'Time Machine is not backing up this Mac.' });
    }
    if (within(system.cert_expires_at, CERT_WINDOW_DAYS, now)) {
      banners.push({
        id: 'cert',
        level: 'warn',
        text: `The HTTPS certificate expires ${formatDate(system.cert_expires_at)}.`,
      });
    }
    if (system.ca_expiring) {
      banners.push({
        id: 'ca',
        level: 'warn',
        text: `The BizViz local CA certificate expires ${formatDate(system.ca_expires_at)}.`,
      });
    }
    if (system.hostname_mismatch) {
      banners.push({
        id: 'hostname',
        level: 'warn',
        text: `The Mac's hostname is now ${system.current_hostname ?? 'different'}, but the certificate was issued for ${system.hostname ?? 'another name'}.`,
      });
    }
    if (system.login_locked_until && Date.parse(system.login_locked_until) > now) {
      banners.push({
        id: 'locked',
        level: 'warn',
        text: `Logins from other devices are locked until ${formatDateTime(system.login_locked_until)}.`,
      });
    }
  }

  return banners;
}
