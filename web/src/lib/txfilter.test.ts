import { describe, expect, it } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import {
  EMPTY_FILTERS,
  PAGE_SIZE,
  accountOptions,
  availableMonths,
  filterTransactions,
  paginate,
} from './txfilter';

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
