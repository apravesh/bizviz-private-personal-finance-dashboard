import { describe, expect, it } from 'vitest';
import {
  EVERYDAY_EXCLUSION,
  accountGains,
  accountLabel,
  estimateNotes,
  investmentGainsSeries,
  investmentSeries,
  money,
  moneyAxis,
  monthlyEarnings,
  monthlySavings,
  monthlySpending,
  monthsAxis,
  netWorthSeries,
  recurringList,
  scopeClass,
  seriesTransactions,
  sliceFilters,
  stackAccountsByMonth,
  spendingByCategory,
} from './aggregate';
import { makePayload, TODAY, tx } from './__fixtures__/payload';
import { EMPTY_FILTERS, filterTransactions, flowTotal } from './txfilter';

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

describe('monthlySavings', () => {
  it('is earnings minus spending per month, with both totals kept', () => {
    const r = monthlySavings(makePayload(), 'mine', TODAY);
    expect(r.months).toEqual(['2026-08', '2026-09', '2026-10']);
    expect(r.inProgress).toBe('2026-10');
    expect(r.earnings).toEqual([30, 3000, 0]);
    expect(r.spending).toEqual([150, 200, 80]);
    expect(r.savings).toEqual([-120, 2800, -80]);
  });

  it('nets pending earnings against pending spending', () => {
    expect(monthlySavings(makePayload(), 'mine', TODAY).pendingByMonth).toEqual([0, 0, -80]);
  });

  it('follows the scope like the earnings and spending charts', () => {
    const p = makePayload();
    const r = monthlySavings(p, 'household', TODAY);
    const earned = monthlyEarnings(p, 'household', 'none', TODAY).totals;
    const spent = monthlySpending(p, 'household', 'none', TODAY).totals;
    expect(r.savings).toEqual(earned.map((e, i) => Math.round((e - spent[i]) * 100) / 100));
    expect(r.earnings[2]).toBe(1000);
  });

  it('is all zeros for months without transactions', () => {
    const p = makePayload();
    p.transactions = [];
    expect(monthlySavings(p, 'mine', TODAY).savings).toEqual([0]);
  });
});

describe('spendingByCategory', () => {
  it('defaults to complete months only, largest category first', () => {
    const r = spendingByCategory(makePayload(), 'mine', 'last-12', TODAY);
    expect(r.slices).toEqual([
      { key: 'groceries', label: 'Groceries', value: 300 },
      { key: 'dining', label: 'Dining', value: 50 },
    ]);
    expect(r.total).toBe(350);
    expect([r.from, r.to, r.includesInProgress]).toEqual(['2026-08', '2026-09', false]);
  });

  it('this month is the in-progress month and drops empty categories', () => {
    const r = spendingByCategory(makePayload(), 'mine', 'this-month', TODAY);
    expect(r.slices).toEqual([{ key: 'dining', label: 'Dining', value: 80 }]);
    expect([r.from, r.to, r.includesInProgress]).toEqual(['2026-10', '2026-10', true]);
  });

  it('last month, last 3 full months and all months', () => {
    expect(spendingByCategory(makePayload(), 'mine', 'last-month', TODAY).slices).toEqual([
      { key: 'groceries', label: 'Groceries', value: 200 },
    ]);
    expect(spendingByCategory(makePayload(), 'mine', 'last-3', TODAY).total).toBe(350);
    const all = spendingByCategory(makePayload(), 'household', 'all', TODAY);
    expect(all.slices.map((s) => s.value)).toEqual([340, 130]);
    expect(all.includesInProgress).toBe(true);
  });

  it('is empty when the period has no months on the axis', () => {
    const p = makePayload();
    p.transactions = [];
    const r = spendingByCategory(p, 'mine', 'last-month', TODAY);
    expect(r).toMatchObject({ slices: [], total: 0, from: null, to: null });
  });

  it('leaves out categories where refunds outweigh spending, and names them', () => {
    const p = makePayload();
    p.transactions.push(
      tx('refund', '2026-09-20', -75, {
        mine: { flow: 'spending', category_id: 'dining', recurring: false },
      }),
    );
    const r = spendingByCategory(p, 'mine', 'last-12', TODAY);
    expect(r.slices.map((s) => s.key)).toEqual(['groceries']);
    expect(r.netRefundCategories).toEqual(['Dining']);
    expect(r.total).toBe(300);
  });

  it('keeps the top 9 categories and folds the rest into Other', () => {
    const p = makePayload();
    p.transactions = [];
    for (let i = 1; i <= 12; i++) {
      p.transactions.push(
        tx(`c${i}`, '2026-09-02', i * 10, { mine: { flow: 'spending', category_id: `cat${i}`, recurring: false } }),
      );
    }
    const r = spendingByCategory(p, 'mine', 'last-month', TODAY);
    expect(r.slices).toHaveLength(10);
    expect(r.slices[0].value).toBe(120);
    expect(r.slices[9]).toEqual({ key: 'other', label: 'Other', value: 60 });
    expect(r.total).toBe(780);
  });
});

describe('sliceFilters', () => {
  it('uses a month range, or a single month, and the slice category', () => {
    const r = spendingByCategory(makePayload(), 'mine', 'last-12', TODAY);
    expect(sliceFilters(r, 'dining')).toEqual({ flow: 'spending', from: '2026-08', to: '2026-09', category: 'dining' });
    const one = spendingByCategory(makePayload(), 'mine', 'last-month', TODAY);
    expect(sliceFilters(one, 'groceries')).toEqual({ flow: 'spending', month: '2026-09', category: 'groceries' });
  });

  it('lists exactly the transactions behind each slice, Other included', () => {
    const p = makePayload();
    p.transactions = [];
    for (let i = 1; i <= 12; i++) {
      for (const day of ['2026-08-02', '2026-09-02']) {
        p.transactions.push(
          tx(`c${i}-${day}`, day, i * 10, { mine: { flow: 'spending', category_id: `cat${i}`, recurring: false } }),
        );
      }
    }
    const r = spendingByCategory(p, 'mine', 'last-12', TODAY);
    expect(r.otherKeys).toEqual(['cat3', 'cat2', 'cat1']);
    for (const slice of r.slices) {
      const list = filterTransactions(p, 'mine', { ...EMPTY_FILTERS, ...sliceFilters(r, slice.key) });
      expect(flowTotal(list, 'spending')).toBe(slice.value);
    }
  });
});

describe('spendingByCategory with the everyday exclusion', () => {
  const spend = (category: string, extra: Partial<Parameters<typeof tx>[3]> = {}) => ({
    mine: { flow: 'spending' as const, category_id: category, recurring: false },
    ...extra,
  });
  const world = () => {
    const p = makePayload();
    p.transactions = [
      tx('food', '2026-09-03', 100, spend('plaid:FOOD_AND_DRINK')),
      tx('rent', '2026-09-01', 2000, spend('plaid:RENT_AND_UTILITIES', { pfc_detailed: 'RENT_AND_UTILITIES_RENT' })),
      tx('power', '2026-09-05', 80, spend('plaid:RENT_AND_UTILITIES', { pfc_detailed: 'RENT_AND_UTILITIES_GAS_AND_ELECTRICITY' })),
      // Plaid says rent, but the user moved it to Home Improvement: no longer rent.
      tx('moved', '2026-09-06', 300, spend('plaid:HOME_IMPROVEMENT', { pfc_detailed: 'RENT_AND_UTILITIES_RENT' })),
      tx('card', '2026-09-10', 900, spend('sys:cc_unlinked')),
      tx('zelle', '2026-09-12', 250, spend('sys:transfers_out')),
    ];
    return p;
  };

  it('the plain pie keeps everything', () => {
    const r = spendingByCategory(world(), 'mine', 'last-month', TODAY);
    expect(r.total).toBe(3630);
    expect(r.excludesRent).toBe(false);
  });

  it('leaves out unlinked-card payments, transfers out and rent, keeping utilities', () => {
    const r = spendingByCategory(world(), 'mine', 'last-month', TODAY, EVERYDAY_EXCLUSION);
    expect(r.slices).toEqual([
      { key: 'plaid:HOME_IMPROVEMENT', label: 'plaid:HOME_IMPROVEMENT', value: 300 },
      { key: 'plaid:FOOD_AND_DRINK', label: 'plaid:FOOD_AND_DRINK', value: 100 },
      { key: 'plaid:RENT_AND_UTILITIES', label: 'Utilities (excl. rent)', value: 80 },
    ]);
    expect(r.total).toBe(480);
    expect(r.excludesRent).toBe(true);
  });

  it('clicking any of its slices lists exactly that slice', () => {
    const p = world();
    const r = spendingByCategory(p, 'mine', 'last-month', TODAY, EVERYDAY_EXCLUSION);
    for (const slice of r.slices) {
      const f = sliceFilters(r, slice.key);
      expect(f.excludeRent).toBe(true);
      const list = filterTransactions(p, 'mine', { ...EMPTY_FILTERS, ...f });
      expect(flowTotal(list, 'spending')).toBe(slice.value);
    }
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

describe('accountGains', () => {
  it('takes deposits and withdrawals out of the value change', () => {
    const gains = accountGains(
      [
        ['2026-01-03', 1600],
        ['2026-01-04', 1450],
      ],
      [
        ['2026-01-01', 1000],
        ['2026-01-02', 1010],
      ],
      [
        ['2026-01-03', 500],
        ['2026-01-04', -200],
      ],
    );
    expect(gains.estimated).toEqual([
      ['2026-01-01', 0],
      ['2026-01-02', 10],
    ]);
    // 1600 - 1000 - 500 deposited; 1450 - 1000 - (500 - 200)
    expect(gains.actual).toEqual([
      ['2026-01-03', 100],
      ['2026-01-04', 150],
    ]);
  });

  it('ignores flows on or before the first point, which its value already includes', () => {
    const gains = accountGains(
      [
        ['2026-01-01', 1000],
        ['2026-01-02', 1000],
      ],
      [],
      [
        ['2025-12-01', 999],
        ['2026-01-01', 1000],
      ],
    );
    expect(gains.actual).toEqual([
      ['2026-01-01', 0],
      ['2026-01-02', 0],
    ]);
  });

  it('prefers the reported value over the estimate on the same date', () => {
    const gains = accountGains([['2026-01-02', 120]], [['2026-01-01', 100], ['2026-01-02', 999]], []);
    expect(gains.estimated).toEqual([['2026-01-01', 0]]);
    expect(gains.actual).toEqual([['2026-01-02', 20]]);
  });

  it('is empty without values', () => {
    expect(accountGains([], [], [['2026-01-01', 5]])).toEqual({ actual: [], estimated: [] });
  });
});

describe('investmentGainsSeries', () => {
  it('follows scope and uses the fixture flows', () => {
    const mine = investmentGainsSeries(makePayload(), 'mine', 'daily');
    expect(mine.map((a) => a.account_id)).toEqual(['acc-brokerage']);
    // base 90 on 2026-08-30; 5 deposited on 2026-10-01
    expect(mine[0].estimated).toEqual([
      ['2026-08-30', 0],
      ['2026-09-01', 5],
    ]);
    expect(mine[0].actual).toEqual([
      ['2026-09-30', 10],
      ['2026-10-01', 15],
      ['2026-10-02', 25],
    ]);
    expect(investmentGainsSeries(makePayload(), 'household', 'daily')).toHaveLength(2);
  });

  it('averages per month', () => {
    const [account] = investmentGainsSeries(makePayload(), 'mine', 'monthly');
    expect(account.actual).toEqual([
      ['2026-09-15', 10],
      ['2026-10-15', 20],
    ]);
  });
});

describe('estimateNotes', () => {
  const meta = (over: Record<string, unknown>) => ({
    anchor_date: null,
    window_start: null,
    window_end: null,
    excluded_count: 0,
    excluded_value: 0,
    warnings: [],
    status: 'ok',
    ...over,
  });
  it('lists downloading, excluded holdings and warnings per account, in order', () => {
    const notes = estimateNotes([
      { account_id: 'a', label: 'A', actual: [], estimated: [], meta: meta({ status: 'downloading' }) },
      { account_id: 'b', label: 'B', actual: [], estimated: [], meta: null },
      {
        account_id: 'c',
        label: 'C',
        actual: [],
        estimated: [],
        meta: meta({ excluded_count: 1, excluded_value: 4.53, warnings: ['w1', 'w2'] }),
      },
      { account_id: 'd', label: 'D', actual: [], estimated: [], meta: meta({ excluded_count: 2, excluded_value: 10 }) },
    ]);
    expect(notes).toEqual([
      'A: Investment history is still downloading.',
      "C: Estimate excludes 1 holding ($4.53 of today's value).",
      'C: w1',
      'C: w2',
      "D: Estimate excludes 2 holdings ($10.00 of today's value).",
    ]);
  });
});

describe('stackAccountsByMonth', () => {
  const series = (account_id: string, actual: [string, number][], estimated: [string, number][] = []) => ({
    account_id,
    label: account_id.toUpperCase(),
    actual,
    estimated,
    meta: null,
  });
  const accounts = [
    series('a', [['2026-09-15', 100], ['2026-10-15', 120]], [['2026-08-15', 80], ['2026-09-15', 999]]),
    series('b', [['2026-10-15', 50]]),
    series('c', [['2026-10-15', 7]], [['2026-09-15', 5]]),
  ];

  it('lines accounts up by month; reported values win; estimated months are flagged', () => {
    const r = stackAccountsByMonth(accounts);
    expect(r.months).toEqual(['2026-08', '2026-09', '2026-10']);
    expect(r.series).toEqual([
      { key: 'a', label: 'A', values: [80, 100, 120] },
      { key: 'b', label: 'B', values: [null, null, 50] },
      { key: 'c', label: 'C', values: [null, 5, 7] },
    ]);
    expect(r.totals).toEqual([80, 105, 177]);
    expect(r.estimated).toEqual([true, true, false]);
  });

  it('sums accounts that share a group', () => {
    const r = stackAccountsByMonth(accounts, (a) =>
      a.account_id === 'c' ? { key: 'roth', label: 'Roth' } : { key: 'pre', label: 'Pre-tax' },
    );
    expect(r.series).toEqual([
      { key: 'pre', label: 'Pre-tax', values: [80, 100, 170] },
      { key: 'roth', label: 'Roth', values: [null, 5, 7] },
    ]);
    expect(r.totals).toEqual([80, 105, 177]);
  });

  it('is empty without accounts', () => {
    expect(stackAccountsByMonth([])).toEqual({ months: [], series: [], totals: [], estimated: [] });
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
      retirement: 300,
      otherAssets: 700,
    });
    expect(r.current).toEqual(r.points[3]);
    expect(r.current?.net).toBe(1400);
  });

  it('uses household values for the household scope', () => {
    const current = netWorthSeries(makePayload(), 'household', 'daily').current;
    expect([current?.assets, current?.retirement, current?.otherAssets]).toEqual([2400, 900, 1500]);
  });

  it('monthly mean per month', () => {
    const r = netWorthSeries(makePayload(), 'mine', 'monthly');
    expect(r.points.map((p) => p.date)).toEqual(['2026-09-15', '2026-10-15']);
    expect(r.points[1].assets).toBe(1200);
    expect(r.points[1].debts).toBe(100);
    expect(r.points[1].net).toBe(1100);
    expect(r.points[1].stale_accounts).toBe(2);
    // Oct: retirement 300, 300, 400 and other assets 700, 900, 1000
    expect([r.points[1].retirement, r.points[1].otherAssets]).toEqual([333.33, 866.67]);
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
