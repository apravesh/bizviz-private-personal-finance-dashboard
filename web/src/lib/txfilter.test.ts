import { describe, expect, it } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import {
  EMPTY_FILTERS,
  filtersFromParams,
  flowTotal,
  transactionsHref,
  PAGE_SIZE,
  accountOptions,
  availableMonths,
  filterTransactions,
  paginate,
} from './txfilter';
import { parseHash } from './route';

const ids = (filters: Partial<typeof EMPTY_FILTERS>, scope: 'mine' | 'household' = 'mine') =>
  filterTransactions(makePayload(), scope, { ...EMPTY_FILTERS, ...filters }).map((t) => t.id);

describe('filterTransactions', () => {
  it('lists in-scope transactions newest first', () => {
    expect(ids({})).toEqual(['t7', 't6', 't5', 't4', 't3', 't2', 't1']);
    expect(ids({}, 'household')).toContain('t8');
  });
  it('searches merchant and raw name case-insensitively', () => {
    expect(ids({ search: 'CAFE' })).toEqual(['t7', 't2']);
    expect(ids({ search: 'raw t4' })).toEqual(['t4']);
    expect(ids({ search: '  nothing matches  ' })).toEqual([]);
  });
  it('filters by month, account, category and flow', () => {
    expect(ids({ month: '2026-08' })).toEqual(['t3', 't2', 't1']);
    expect(ids({ account: 'acc-card' })).toEqual(['t4']);
    expect(ids({ category: 'dining' })).toEqual(['t7', 't2']);
    expect(ids({ flow: 'earning' })).toEqual(['t5', 't3']);
    expect(ids({ flow: 'internal' })).toEqual(['t6']);
  });
  it('combines filters', () => {
    expect(ids({ month: '2026-08', flow: 'spending', search: 'shop' })).toEqual(['t1']);
  });
  it('filters suspect card payments', () => {
    const p = makePayload();
    p.quality.suspect_card_payments = { count: 1, transaction_ids: ['t4', 't8'] };
    expect(
      filterTransactions(p, 'mine', { ...EMPTY_FILTERS, suspectOnly: true }).map((t) => t.id),
    ).toEqual(['t4']);
  });
});

describe('paginate', () => {
  const items = Array.from({ length: 250 }, (_, i) => i);
  it('uses pages of 100', () => {
    expect(PAGE_SIZE).toBe(100);
    expect(paginate(items, 0).rows).toHaveLength(100);
    expect(paginate(items, 2)).toMatchObject({ pages: 3, page: 2 });
    expect(paginate(items, 2).rows).toHaveLength(50);
  });
  it('clamps the page and handles empty lists', () => {
    expect(paginate(items, 9).page).toBe(2);
    expect(paginate(items, -1).page).toBe(0);
    expect(paginate([], 0)).toEqual({ rows: [], pages: 1, page: 0 });
  });
});

describe('option lists', () => {
  it('months are newest first and scope-aware', () => {
    expect(availableMonths(makePayload(), 'mine')).toEqual(['2026-10', '2026-09', '2026-08']);
  });
  it('accounts only include those with transactions', () => {
    expect(accountOptions(makePayload()).map((a) => a.id)).toEqual(['acc-checking', 'acc-card', 'acc-joint']);
  });
});

describe('month range and category set', () => {
  it('keeps months from..to inclusive', () => {
    const all = ids({});
    const inRange = ids({ from: '2026-09', to: '2026-10' });
    expect(inRange.length).toBeGreaterThan(0);
    expect(inRange.length).toBeLessThan(all.length);
    const dates = makePayload().transactions.filter((t) => inRange.includes(t.id)).map((t) => t.date);
    expect(dates.every((d) => d >= '2026-09' && d < '2026-11')).toBe(true);
    expect(ids({ from: '2026-10' }).every((id) => inRange.includes(id))).toBe(true);
  });

  it('keeps any of several categories', () => {
    const groceries = ids({ category: 'groceries' });
    const dining = ids({ category: 'dining' });
    expect(ids({ categories: ['groceries', 'dining'] }).sort()).toEqual([...groceries, ...dining].sort());
  });
});

describe('flowTotal', () => {
  it('sums spending as positive and earnings as positive, and is null for mixed flows', () => {
    const p = makePayload();
    const spend = filterTransactions(p, 'mine', { ...EMPTY_FILTERS, flow: 'spending' });
    expect(flowTotal(spend, 'spending')).toBe(430);
    const earn = filterTransactions(p, 'mine', { ...EMPTY_FILTERS, flow: 'earning' });
    expect(flowTotal(earn, 'earning')).toBe(3030);
    expect(flowTotal(spend, '')).toBeNull();
  });
});

describe('link filters', () => {
  it('round-trips every filter through the URL', () => {
    const f = {
      ...EMPTY_FILTERS,
      search: 'cafe & bar',
      from: '2025-10',
      to: '2026-09',
      account: 'acc-1',
      categories: ['a', 'b'],
      flow: 'spending',
      suspectOnly: true,
    };
    const href = transactionsHref(f);
    expect(href.startsWith('#/transactions?')).toBe(true);
    expect(filtersFromParams(parseHash(href).params)).toEqual(f);
  });

  it('a single category goes to the dropdown, not the set', () => {
    const f = filtersFromParams(new URLSearchParams('category=dining&month=2026-09'));
    expect(f).toEqual({ ...EMPTY_FILTERS, category: 'dining', month: '2026-09' });
  });

  it('keeps the existing suspect-payments link and an empty link working', () => {
    expect(transactionsHref({ suspectOnly: true })).toBe('#/transactions?suspect=1');
    expect(transactionsHref({})).toBe('#/transactions');
    expect(filtersFromParams(new URLSearchParams())).toEqual(EMPTY_FILTERS);
  });
});
