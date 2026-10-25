import { accountLabel, isRent, scopeClass } from './aggregate';
import type { DataPayload, Scope, TransactionOut } from './types';

export interface TxFilters {
  search: string;
  month: string;
  /** Inclusive month range (YYYY-MM), e.g. the period of a pie chart; empty means open. */
  from: string;
  to: string;
  account: string;
  category: string;
  /** Several categories at once, e.g. the ones folded into a pie chart's "Other" slice. */
  categories: string[];
  /** Leave out rent payments, as the "excluding rent" spending pie does. */
  excludeRent: boolean;
  flow: string;
  suspectOnly: boolean;
}

export const EMPTY_FILTERS: TxFilters = {
  search: '',
  month: '',
  from: '',
  to: '',
  account: '',
  category: '',
  categories: [],
  excludeRent: false,
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
    const month = tx.date.slice(0, 7);
    if (f.month && month !== f.month) return false;
    if (f.from && month < f.from) return false;
    if (f.to && month > f.to) return false;
    if (f.account && tx.account_id !== f.account) return false;
    if (f.category && cls.category_id !== f.category) return false;
    if (f.categories.length > 0 && !f.categories.includes(cls.category_id)) return false;
    if (f.excludeRent && isRent(tx, cls)) return false;
    if (f.flow && cls.flow !== f.flow) return false;
    if (needle) {
      const haystack = `${tx.merchant_display ?? ''}\n${tx.name ?? ''}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
  return list.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.id < b.id ? -1 : 1));
}

/** Net amount of a list for one flow, signed so spending and earnings read as positive. */
export function flowTotal(list: TransactionOut[], flow: string): number | null {
  if (flow !== 'spending' && flow !== 'earning') return null;
  const sign = flow === 'spending' ? 1 : -1;
  return Math.round(list.reduce((sum, tx) => sum + sign * tx.amount, 0) * 100) / 100;
}

const PARAM_KEYS = ['q', 'month', 'from', 'to', 'account', 'category', 'norent', 'flow', 'suspect'] as const;

/** Filters from the Transactions page's URL (`#/transactions?...`). */
export function filtersFromParams(params: URLSearchParams): TxFilters {
  const categories = (params.get('category') ?? '').split(',').filter(Boolean);
  return {
    search: params.get('q') ?? '',
    month: params.get('month') ?? '',
    from: params.get('from') ?? '',
    to: params.get('to') ?? '',
    account: params.get('account') ?? '',
    category: categories.length === 1 ? categories[0] : '',
    categories: categories.length > 1 ? categories : [],
    excludeRent: params.get('norent') === '1',
    flow: params.get('flow') ?? '',
    suspectOnly: params.get('suspect') === '1',
  };
}

/** A link to the Transactions page with these filters applied. */
export function transactionsHref(f: Partial<TxFilters>): string {
  const values: Record<(typeof PARAM_KEYS)[number], string> = {
    q: f.search ?? '',
    month: f.month ?? '',
    from: f.from ?? '',
    to: f.to ?? '',
    account: f.account ?? '',
    category: f.categories?.length ? f.categories.join(',') : (f.category ?? ''),
    norent: f.excludeRent ? '1' : '',
    flow: f.flow ?? '',
    suspect: f.suspectOnly ? '1' : '',
  };
  const params = new URLSearchParams();
  for (const key of PARAM_KEYS) if (values[key]) params.set(key, values[key]);
  const query = params.toString();
  return query ? `#/transactions?${query}` : '#/transactions';
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
