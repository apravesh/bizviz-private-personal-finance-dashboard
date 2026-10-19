import { describe, expect, it } from 'vitest';
import {
  accountLabel,
  investmentSeries,
  money,
  moneyAxis,
  monthlyEarnings,
  monthlySpending,
  monthsAxis,
  netWorthSeries,
  recurringList,
  scopeClass,
  seriesTransactions,
} from './aggregate';
import { makePayload, TODAY, tx } from './__fixtures__/payload';

describe('scopeClass', () => {
  it('returns the class for the active scope', () => {
    const t = makePayload().transactions[7];
    expect(scopeClass(t, 'mine')).toBeNull();
    expect(scopeClass(t, 'household')?.flow).toBe('spending');
  });
});

describe('monthsAxis', () => {
  it('starts at the first month with data and ends at the current month', () => {
    const months = monthsAxis(makePayload(), TODAY);
    expect(months).toEqual(['2026-08', '2026-09', '2026-10']);
  });

  it('is capped at 60 months', () => {
    const p = makePayload();
    p.transactions.push(tx('old', '2015-01-05', 10));
    const months = monthsAxis(p, TODAY);
    expect(months).toHaveLength(60);
    expect(months[0]).toBe('2021-11');
    expect(months[59]).toBe('2026-10');
  });

  it('is just the current month without transactions', () => {
    const p = makePayload();
    p.transactions = [];
    expect(monthsAxis(p, TODAY)).toEqual(['2026-10']);
  });

  it('accepts a Date', () => {
    expect(monthsAxis(makePayload(), new Date(2026, 9, 19)).at(-1)).toBe('2026-10');
  });

  it('crosses year boundaries', () => {
    const p = makePayload();
    p.transactions = [tx('a', '2025-11-02', 1)];
    expect(monthsAxis(p, '2026-02-01')).toEqual(['2025-11', '2025-12', '2026-01', '2026-02']);
  });
});

describe('monthlySpending', () => {
  it('sums spending per month, refunds reduce, pending reported separately', () => {
    const r = monthlySpending(makePayload(), 'mine', 'none', TODAY);
    expect(r.months).toEqual(['2026-08', '2026-09', '2026-10']);
    expect(r.inProgress).toBe('2026-10');
    expect(r.series).toHaveLength(1);
    expect(r.series[0]).toMatchObject({ key: 'total', label: 'Total' });
    // Aug: 100 + 50 (refund t3 is flow earning, not spending); Sep: 200; Oct: 80 pending
    expect(r.series[0].values).toEqual([150, 200, 80]);
    expect(r.totals).toEqual([150, 200, 80]);
    expect(r.pendingByMonth).toEqual([0, 0, 80]);
  });

  it('treats negative spending amounts as refunds', () => {
    const p = makePayload();
    p.transactions.push(
      tx('refund', '2026-09-20', -25, {
        mine: { flow: 'spending', category_id: 'groceries', recurring: false },
      }),
    );
    const r = monthlySpending(p, 'mine', 'none', TODAY);
    expect(r.series[0].values[1]).toBe(175);
  });

  it('household scope includes transactions only visible there', () => {
    const r = monthlySpending(makePayload(), 'household', 'none', TODAY);
    expect(r.series[0].values).toEqual([150, 200, 120]);
  });

  it('excludes internal and ignored flows', () => {
    const r = monthlySpending(makePayload(), 'mine', 'none', TODAY);
    expect(r.totals[1]).toBe(200);
  });

  it('splits by category with labels', () => {
    const r = monthlySpending(makePayload(), 'mine', 'category', TODAY);
    const byKey = Object.fromEntries(r.series.map((s) => [s.key, s]));
    expect(byKey.groceries.label).toBe('Groceries');
    expect(byKey.groceries.values).toEqual([100, 200, 0]);
    expect(byKey.dining.values).toEqual([50, 0, 80]);
    expect(r.series.map((s) => s.key)).toEqual(['groceries', 'dining']);
  });

  it('splits by account with institution, name and mask', () => {
    const r = monthlySpending(makePayload(), 'mine', 'account', TODAY);
    const card = r.series.find((s) => s.key === 'acc-card')!;
    expect(card.label).toBe('First Bank Visa ••2222');
    expect(card.values).toEqual([0, 200, 0]);
  });

  it('splits by recurring vs one-off', () => {
    const r = monthlySpending(makePayload(), 'mine', 'recurring', TODAY);
    const byKey = Object.fromEntries(r.series.map((s) => [s.key, s.values]));
    expect(byKey.recurring).toEqual([50, 0, 80]);
    expect(byKey['one-off']).toEqual([100, 200, 0]);
  });

  it('splits by merchant: top 10 over the axis, the rest as Other', () => {
    const p = makePayload();
    p.transactions = [];
    for (let i = 1; i <= 12; i++) {
      p.transactions.push(
        tx(`m${i}`, '2026-10-02', i * 10, { merchant_key: `m${i}`, merchant_display: `M${i}` }),
      );
    }
    const r = monthlySpending(p, 'mine', 'merchant', TODAY);
    expect(r.series).toHaveLength(11);
    expect(r.series[0].label).toBe('M12');
    const other = r.series[10];
    expect(other).toMatchObject({ key: 'other', label: 'Other' });
    expect(other.values.at(-1)).toBe(30);
    expect(r.totals.at(-1)).toBe(780);
  });

  it('keeps 10 or fewer merchants without an Other series', () => {
    const r = monthlySpending(makePayload(), 'mine', 'merchant', TODAY);
    expect(r.series.some((s) => s.key === 'other')).toBe(false);
  });

  it('rounds to cents', () => {
    const p = makePayload();
    p.transactions = [tx('a', '2026-10-01', 0.1), tx('b', '2026-10-02', 0.2)];
    expect(monthlySpending(p, 'mine', 'none', TODAY).totals.at(-1)).toBe(0.3);
  });

  it('ignores transactions outside the axis', () => {
    const p = makePayload();
    p.transactions.push(tx('future', '2026-12-01', 999));
    expect(monthlySpending(p, 'mine', 'none', TODAY).totals).toEqual([150, 200, 80]);
  });
});

describe('monthlyEarnings', () => {
  it('sums negated amounts for earning flows', () => {
    const r = monthlyEarnings(makePayload(), 'mine', 'none', TODAY);
    expect(r.series[0].values).toEqual([30, 3000, 0]);
  });

  it('household includes joint-only income', () => {
    const r = monthlyEarnings(makePayload(), 'household', 'none', TODAY);
    expect(r.series[0].values).toEqual([30, 3000, 1000]);
  });

  it('splits by category and account', () => {
    const p = makePayload();
    const byCat = monthlyEarnings(p, 'household', 'category', TODAY);
    expect(byCat.series).toHaveLength(1);
    expect(byCat.series[0].label).toBe('Income');
    const byAcc = monthlyEarnings(p, 'household', 'account', TODAY);
    expect(byAcc.series.map((s) => s.key).sort()).toEqual(['acc-checking', 'acc-joint']);
  });
});

describe('seriesTransactions', () => {
  it('lists the transactions behind a bar segment, newest first', () => {
    const list = seriesTransactions(makePayload(), 'mine', 'spending', 'category', TODAY, 'dining', '2026-10');
    expect(list.map((t) => t.id)).toEqual(['t7']);
    const aug = seriesTransactions(makePayload(), 'mine', 'spending', 'none', TODAY, 'total', '2026-08');
    expect(aug.map((t) => t.id)).toEqual(['t2', 't1']);
  });

  it('returns Other-bucket members for merchant splits', () => {
    const p = makePayload();
    p.transactions = [];
    for (let i = 1; i <= 12; i++) {
      p.transactions.push(tx(`m${i}`, '2026-10-02', i * 10, { merchant_key: `m${i}` }));
    }
    const list = seriesTransactions(p, 'mine', 'spending', 'merchant', TODAY, 'other', '2026-10');
    expect(list.map((t) => t.id).sort()).toEqual(['m1', 'm2']);
  });

  it('returns an empty list for unknown segments', () => {
    expect(seriesTransactions(makePayload(), 'mine', 'spending', 'none', TODAY, 'x', '2026-10')).toEqual([]);
  });
});

describe('investmentSeries', () => {
  it('mine only includes accounts owned by me', () => {
    const r = investmentSeries(makePayload(), 'mine', 'daily');
    expect(r.map((a) => a.account_id)).toEqual(['acc-brokerage']);
    expect(r[0].label).toBe('First Bank Brokerage ••4444');
    expect(r[0].actual).toHaveLength(3);
    expect(r[0].meta?.status).toBe('ready');
  });

  it('household includes all investment accounts', () => {
    expect(investmentSeries(makePayload(), 'household', 'daily')).toHaveLength(2);
  });

  it('monthly averages actual and estimated separately', () => {
    const r = investmentSeries(makePayload(), 'mine', 'monthly')[0];
    expect(r.actual).toEqual([
      ['2026-09-15', 100],
      ['2026-10-15', 115],
    ]);
    expect(r.estimated).toEqual([
      ['2026-08-15', 90],
      ['2026-09-15', 97.5],
    ]);
  });
});

describe('netWorthSeries', () => {
  it('computes net per scope', () => {
    const r = netWorthSeries(makePayload(), 'mine', 'daily');
    expect(r.points).toHaveLength(4);
    expect(r.points[1]).toEqual({
      date: '2026-10-01',
      assets: 1000,
      debts: 200,
      net: 800,
      stale_accounts: 0,
    });
    expect(r.current).toEqual(r.points[3]);
    expect(r.current?.net).toBe(1400);
  });

  it('uses household values for the household scope', () => {
    expect(netWorthSeries(makePayload(), 'household', 'daily').current?.assets).toBe(2400);
  });

  it('monthly mean per month', () => {
    const r = netWorthSeries(makePayload(), 'mine', 'monthly');
    expect(r.points.map((p) => p.date)).toEqual(['2026-09-15', '2026-10-15']);
    expect(r.points[1].assets).toBe(1200);
    expect(r.points[1].debts).toBe(100);
    expect(r.points[1].net).toBe(1100);
    expect(r.points[1].stale_accounts).toBe(2);
    expect(r.current).toEqual(r.points[1]);
  });

  it('handles an empty series', () => {
    const p = makePayload();
    p.net_worth.series = [];
    expect(netWorthSeries(p, 'mine', 'daily')).toEqual({ points: [], current: null });
  });
});

describe('recurringList', () => {
  it('returns the list for the scope', () => {
    expect(recurringList(makePayload(), 'mine')).toHaveLength(1);
    expect(recurringList(makePayload(), 'household')).toHaveLength(0);
  });
});

describe('formatting', () => {
  it('formats money', () => {
    expect(money(1234.5)).toBe('$1,234.50');
    expect(moneyAxis(1234.5)).toBe('$1,235');
    expect(money(-5)).toBe('-$5.00');
  });

  it('labels accounts, tolerating missing parts', () => {
    expect(accountLabel(undefined, 'abc')).toBe('abc');
    const a = makePayload().accounts[0];
    expect(accountLabel({ ...a, mask: null })).toBe('First Bank Checking');
  });
});
