import type {
  AccountOut,
  DataPayload,
  EstimateMetaOut,
  RecurringGroupOut,
  Scope,
  ScopeClass,
  TransactionOut,
} from './types';

export type SpendingSplit = 'none' | 'category' | 'merchant' | 'account' | 'recurring';
export type EarningsSplit = 'none' | 'category' | 'account';
export type Granularity = 'daily' | 'monthly';

export interface Series {
  key: string;
  label: string;
  values: number[];
}

export interface MonthlyResult {
  months: string[];
  inProgress: string;
  series: Series[];
  pendingByMonth: number[];
  totals: number[];
}

export interface InvestmentAccountSeries {
  account_id: string;
  label: string;
  actual: [string, number][];
  estimated: [string, number][];
  meta: EstimateMetaOut | null;
}

export interface NetWorthSeriesPoint {
  date: string;
  assets: number;
  debts: number;
  net: number;
  stale_accounts: number;
}

export interface NetWorthResult {
  points: NetWorthSeriesPoint[];
  current: NetWorthSeriesPoint | null;
}

const MAX_MONTHS = 60;
const TOP_MERCHANTS = 10;
const OTHER_KEY = 'other';

type Today = string | Date;

function todayString(today: Today): string {
  if (typeof today === 'string') return today;
  const y = today.getFullYear();
  const m = String(today.getMonth() + 1).padStart(2, '0');
  const d = String(today.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function currentMonth(today: Today): string {
  return todayString(today).slice(0, 7);
}

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number);
  const index = y * 12 + (m - 1) + delta;
  const ny = Math.floor(index / 12);
  const nm = (index % 12) + 1;
  return `${ny}-${String(nm).padStart(2, '0')}`;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function scopeClass(tx: TransactionOut, scope: Scope): ScopeClass | null {
  return scope === 'mine' ? tx.mine : tx.household;
}

export function accountLabel(account: AccountOut | undefined, fallbackId = ''): string {
  if (!account) return fallbackId;
  const base = [account.institution_name, account.name].filter(Boolean).join(' ');
  const label = account.mask ? `${base} ••${account.mask}` : base;
  return label || account.account_id;
}

export function monthsAxis(payload: DataPayload, today: Today): string[] {
  const current = currentMonth(today);
  const earliest = shiftMonth(current, -(MAX_MONTHS - 1));
  let first: string | null = null;
  for (const tx of payload.transactions) {
    const month = tx.date.slice(0, 7);
    if (first === null || month < first) first = month;
  }
  let start = first === null || first < earliest ? earliest : first;
  if (first === null || start > current) start = current;
  const months: string[] = [];
  for (let m = start; m <= current; m = shiftMonth(m, 1)) months.push(m);
  return months;
}

interface Bucketed {
  result: MonthlyResult;
  members: Map<string, TransactionOut[]>;
}

type Split = SpendingSplit | EarningsSplit;

function bucketKey(seriesKey: string, month: string): string {
  return `${seriesKey}|${month}`;
}

function compute(
  payload: DataPayload,
  scope: Scope,
  flow: 'spending' | 'earning',
  split: Split,
  today: Today,
): Bucketed {
  const months = monthsAxis(payload, today);
  const monthIndex = new Map(months.map((m, i) => [m, i]));
  const accounts = new Map(payload.accounts.map((a) => [a.account_id, a]));
  const categories = new Map(payload.categories.map((c) => [c.category_id, c]));
  const sign = flow === 'spending' ? 1 : -1;

  interface Row {
    tx: TransactionOut;
    index: number;
    key: string;
    label: string;
    value: number;
  }
  const rows: Row[] = [];
  for (const tx of payload.transactions) {
    const cls = scopeClass(tx, scope);
    if (!cls || cls.flow !== flow) continue;
    const index = monthIndex.get(tx.date.slice(0, 7));
    if (index === undefined) continue;
    let key = 'total';
    let label = 'Total';
    if (split === 'category') {
      key = cls.category_id;
      label = categories.get(cls.category_id)?.name ?? cls.category_id;
    } else if (split === 'merchant') {
      key = tx.merchant_key ?? '(unknown)';
      label = tx.merchant_display ?? tx.name ?? 'Unknown';
    } else if (split === 'account') {
      key = tx.account_id;
      label = accountLabel(accounts.get(tx.account_id), tx.account_id);
    } else if (split === 'recurring') {
      key = cls.recurring ? 'recurring' : 'one-off';
      label = cls.recurring ? 'Recurring' : 'One-off';
    }
    rows.push({ tx, index, key, label, value: sign * tx.amount });
  }

  const labels = new Map<string, string>();
  const totalsByKey = new Map<string, number>();
  for (const r of rows) {
    labels.set(r.key, r.label);
    totalsByKey.set(r.key, (totalsByKey.get(r.key) ?? 0) + r.value);
  }

  let keys = [...totalsByKey.keys()];
  if (split === 'recurring') {
    keys = ['recurring', 'one-off'].filter((k) => totalsByKey.has(k));
  } else if (split !== 'none') {
    keys.sort((a, b) => totalsByKey.get(b)! - totalsByKey.get(a)! || a.localeCompare(b));
  }
  const remap = new Map<string, string>();
  if (split === 'merchant' && keys.length > TOP_MERCHANTS) {
    const rest = keys.slice(TOP_MERCHANTS);
    keys = [...keys.slice(0, TOP_MERCHANTS), OTHER_KEY];
    labels.set(OTHER_KEY, 'Other');
    for (const k of rest) remap.set(k, OTHER_KEY);
  }
  if (split === 'none') {
    keys = ['total'];
    labels.set('total', 'Total');
  }

  const seriesMap = new Map<string, number[]>(keys.map((k) => [k, months.map(() => 0)]));
  const pending = months.map(() => 0);
  const members = new Map<string, TransactionOut[]>();
  for (const r of rows) {
    const key = remap.get(r.key) ?? r.key;
    seriesMap.get(key)![r.index] += r.value;
    if (r.tx.pending) pending[r.index] += r.value;
    const bk = bucketKey(key, months[r.index]);
    const list = members.get(bk);
    if (list) list.push(r.tx);
    else members.set(bk, [r.tx]);
  }

  const series: Series[] = keys.map((key) => ({
    key,
    label: labels.get(key) ?? key,
    values: seriesMap.get(key)!.map(round2),
  }));
  const totals = months.map((_, i) => round2(series.reduce((sum, s) => sum + s.values[i], 0)));
  return {
    result: {
      months,
      inProgress: currentMonth(today),
      series,
      pendingByMonth: pending.map(round2),
      totals,
    },
    members,
  };
}

export function monthlySpending(
  payload: DataPayload,
  scope: Scope,
  split: SpendingSplit,
  today: Today,
): MonthlyResult {
  return compute(payload, scope, 'spending', split, today).result;
}

export function monthlyEarnings(
  payload: DataPayload,
  scope: Scope,
  split: EarningsSplit,
  today: Today,
): MonthlyResult {
  return compute(payload, scope, 'earning', split, today).result;
}

export function seriesTransactions(
  payload: DataPayload,
  scope: Scope,
  flow: 'spending' | 'earning',
  split: Split,
  today: Today,
  seriesKey: string,
  month: string,
): TransactionOut[] {
  const list = compute(payload, scope, flow, split, today).members.get(bucketKey(seriesKey, month)) ?? [];
  return [...list].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

function monthlyMean(points: [string, number][]): [string, number][] {
  const sums = new Map<string, { sum: number; n: number }>();
  for (const [date, value] of points) {
    const month = date.slice(0, 7);
    const entry = sums.get(month) ?? { sum: 0, n: 0 };
    entry.sum += value;
    entry.n += 1;
    sums.set(month, entry);
  }
  return [...sums.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([month, { sum, n }]) => [`${month}-15`, round2(sum / n)]);
}

export function investmentSeries(
  payload: DataPayload,
  scope: Scope,
  granularity: Granularity,
): InvestmentAccountSeries[] {
  const accounts = new Map(payload.accounts.map((a) => [a.account_id, a]));
  return payload.investments
    .filter((inv) => scope === 'household' || inv.ownership === 'mine')
    .map((inv) => ({
      account_id: inv.account_id,
      label: accountLabel(accounts.get(inv.account_id), inv.account_id),
      actual: granularity === 'monthly' ? monthlyMean(inv.actual) : inv.actual,
      estimated: granularity === 'monthly' ? monthlyMean(inv.estimated) : inv.estimated,
      meta: inv.estimate_meta,
    }));
}

export function netWorthSeries(
  payload: DataPayload,
  scope: Scope,
  granularity: Granularity,
): NetWorthResult {
  const daily: NetWorthSeriesPoint[] = payload.net_worth.series.map((p) => {
    const s = p[scope];
    return {
      date: p.date,
      assets: s.assets,
      debts: s.debts,
      net: round2(s.assets - s.debts),
      stale_accounts: s.stale_accounts,
    };
  });
  let points = daily;
  if (granularity === 'monthly') {
    const groups = new Map<string, NetWorthSeriesPoint[]>();
    for (const p of daily) {
      const month = p.date.slice(0, 7);
      groups.set(month, [...(groups.get(month) ?? []), p]);
    }
    points = [...groups.entries()]
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([month, group]) => {
        const mean = (f: (p: NetWorthSeriesPoint) => number) =>
          round2(group.reduce((sum, p) => sum + f(p), 0) / group.length);
        return {
          date: `${month}-15`,
          assets: mean((p) => p.assets),
          debts: mean((p) => p.debts),
          net: mean((p) => p.net),
          stale_accounts: Math.max(...group.map((p) => p.stale_accounts)),
        };
      });
  }
  return { points, current: points.length ? points[points.length - 1] : null };
}

export function recurringList(payload: DataPayload, scope: Scope): RecurringGroupOut[] {
  return payload.recurring[scope];
}

const wholeFormat = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});
const centsFormat = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function money(n: number, withCents = true): string {
  return (withCents ? centsFormat : wholeFormat).format(n);
}

export function moneyAxis(n: number): string {
  return money(n, false);
}

const memo = new Map<string, unknown>();
const MEMO_LIMIT = 64;

export function memoized<T>(parts: (string | number)[], compute: () => T): T {
  const key = parts.join('|');
  if (memo.has(key)) return memo.get(key) as T;
  const value = compute();
  if (memo.size >= MEMO_LIMIT) memo.delete(memo.keys().next().value as string);
  memo.set(key, value);
  return value;
}
