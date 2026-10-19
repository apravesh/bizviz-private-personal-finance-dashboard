import { accountLabel, scopeClass } from './aggregate';
import type { DataPayload, Scope, TransactionOut } from './types';

export interface TxFilters {
  search: string;
  month: string;
  account: string;
  category: string;
  flow: string;
  suspectOnly: boolean;
}

export const EMPTY_FILTERS: TxFilters = {
  search: '',
  month: '',
  account: '',
  category: '',
  flow: '',
  suspectOnly: false,
};

export const PAGE_SIZE = 100;

export function filterTransactions(payload: DataPayload, scope: Scope, f: TxFilters): TransactionOut[] {
  const needle = f.search.trim().toLowerCase();
  const suspect = f.suspectOnly ? new Set(payload.quality.suspect_card_payments.transaction_ids) : null;
  const list = payload.transactions.filter((tx) => {
    const cls = scopeClass(tx, scope);
    if (!cls) return false;
    if (suspect && !suspect.has(tx.id)) return false;
    if (f.month && tx.date.slice(0, 7) !== f.month) return false;
    if (f.account && tx.account_id !== f.account) return false;
    if (f.category && cls.category_id !== f.category) return false;
    if (f.flow && cls.flow !== f.flow) return false;
    if (needle) {
      const haystack = `${tx.merchant_display ?? ''}\n${tx.name ?? ''}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
  return list.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.id < b.id ? -1 : 1));
}

export function paginate<T>(items: T[], page: number): { rows: T[]; pages: number; page: number } {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(Math.max(0, page), pages - 1);
  return { rows: items.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE), pages, page: current };
}

export function availableMonths(payload: DataPayload, scope: Scope): string[] {
  const months = new Set<string>();
  for (const tx of payload.transactions) if (scopeClass(tx, scope)) months.add(tx.date.slice(0, 7));
  return [...months].sort().reverse();
}

export function accountOptions(payload: DataPayload): { id: string; label: string }[] {
  const used = new Set(payload.transactions.map((t) => t.account_id));
  return payload.accounts
    .filter((a) => used.has(a.account_id))
    .map((a) => ({ id: a.account_id, label: accountLabel(a) }));
}
