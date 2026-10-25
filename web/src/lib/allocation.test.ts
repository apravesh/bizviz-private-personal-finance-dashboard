import { describe, expect, it } from 'vitest';
import { account, makePayload } from './__fixtures__/payload';
import { assetAllocation, holdingClass, holdingsBreakdown } from './allocation';
import type { DataPayload, HoldingOut } from './types';

function holding(over: Partial<HoldingOut>): HoldingOut {
  return {
    security_name: 'Thing',
    ticker: null,
    quantity: 1,
    price: null,
    value: 100,
    price_as_of: null,
    security_type: 'equity',
    is_cash_equivalent: false,
    ...over,
  };
}

/** Checking 1000 (fixture) + a brokerage with stocks, a fund, cash and a gap to its balance. */
function world(): DataPayload {
  const p = makePayload();
  p.accounts = p.accounts.filter((a) => a.type !== 'investment');
  p.accounts.push(
    account({ account_id: 'brk', type: 'investment', subtype: 'brokerage', current_balance: 10_050 }),
    account({ account_id: 'k401', name: '401k', type: 'investment', subtype: '401k', current_balance: 30_000 }),
    account({ account_id: 'joint-brk', type: 'investment', subtype: 'brokerage', ownership: 'joint', current_balance: 500 }),
  );
  const base = { ownership: 'mine', actual: [], estimated: [], estimate_meta: null, flows: [] };
  p.investments = [
    {
      ...base,
      account_id: 'brk',
      holdings: [
        holding({ ticker: 'AAPL', value: 4000 }),
        holding({ ticker: 'VTI', security_type: 'etf', value: 3000 }),
        holding({ ticker: 'BND', security_type: 'fixed income', value: 1000 }),
        holding({ ticker: 'BTC', security_type: 'cryptocurrency', is_cash_equivalent: true, value: 500 }),
        holding({ security_name: 'Sweep', security_type: 'cash', is_cash_equivalent: true, value: 1500 }),
        holding({ ticker: 'XYZ', value: null }),
      ],
    },
    { ...base, account_id: 'k401', holdings: [] },
    { ...base, account_id: 'joint-brk', ownership: 'joint', holdings: [holding({ ticker: 'AAPL', value: 500 })] },
  ];
  return p;
}

describe('holdingClass', () => {
  it('sorts security types into kinds; crypto stays crypto even when flagged cash-equivalent', () => {
    const cls = (security_type: string | null, is_cash_equivalent = false) =>
      holdingClass({ security_type, is_cash_equivalent });
    expect(cls('equity')).toBe('stocks');
    expect(cls('etf')).toBe('funds');
    expect(cls('mutual fund')).toBe('funds');
    expect(cls('fixed income')).toBe('bonds');
    expect(cls('cash')).toBe('cash');
    expect(cls('mutual fund', true)).toBe('cash');
    expect(cls('cryptocurrency', true)).toBe('crypto');
    expect(cls('derivative')).toBe('other');
    expect(cls(null)).toBe('other');
  });
});

describe('assetAllocation', () => {
  it('adds bank cash, holdings by kind, uninvested cash and accounts without holdings', () => {
    const r = assetAllocation(world(), 'mine');
    const byKey = Object.fromEntries(r.slices.map((s) => [s.key, s.value]));
    // cash: checking 1000 + sweep 1500 + uninvested 10050 - 10000
    expect(byKey).toEqual({ unknown: 30_000, stocks: 4000, funds: 3000, cash: 2550, bonds: 1000, crypto: 500 });
    expect(r.slices[0].label).toBe('Not broken down');
    expect(r.total).toBe(41_050);
    expect(r.uninvestedCash).toBe(50);
    expect(r.notBrokenDown).toEqual(['First Bank 401k ••1111']);
    expect(r.unvaluedHoldings).toBe(1);
  });

  it('household adds joint accounts', () => {
    const mine = assetAllocation(world(), 'mine');
    const household = assetAllocation(world(), 'household');
    // fixture joint checking 1000 + joint brokerage 500
    expect(household.total - mine.total).toBe(1500);
  });

  it('a balance within a dollar of the holdings is not uninvested cash', () => {
    const p = world();
    p.accounts.find((a) => a.account_id === 'brk')!.current_balance = 10_000.5;
    expect(assetAllocation(p, 'mine').uninvestedCash).toBe(0);
  });
});

describe('holdingsBreakdown', () => {
  it('combines holdings by ticker across accounts and leaves out cash', () => {
    const r = holdingsBreakdown(world(), 'household');
    expect(r.slices.map((s) => [s.label, s.value])).toEqual([
      ['AAPL', 4500],
      ['VTI', 3000],
      ['BND', 1000],
      ['BTC', 500],
    ]);
    expect(r.total).toBe(9000);
    expect(r.withoutHoldings).toEqual(['First Bank 401k ••1111']);
  });

  it('keeps the 10 largest and groups the rest', () => {
    const p = world();
    p.investments[0].holdings = Array.from({ length: 14 }, (_, i) => holding({ ticker: `T${i + 1}`, value: (i + 1) * 10 }));
    const r = holdingsBreakdown(p, 'mine');
    expect(r.slices).toHaveLength(11);
    expect(r.slices[0]).toEqual({ key: 'T14', label: 'T14', value: 140 });
    expect(r.slices[10]).toEqual({ key: 'other', label: 'Other (4 holdings)', value: 100 });
    expect(r.otherCount).toBe(4);
    expect(r.total).toBe(1050);
  });
});
