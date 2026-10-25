import { describe, expect, it } from 'vitest';
import { account, makePayload } from './__fixtures__/payload';
import { isTaxAdvantaged, taxGroup, taxSummary, withInvestments, yearlyMoneyIn } from './taxAdvantaged';

describe('taxGroup', () => {
  // Subtypes are mapped to groups on the Mac (bizviz.analytics.tax); the page reads the result.
  it('reads the group the Mac assigned', () => {
    expect(taxGroup({ tax_group: 'pre-tax' })).toBe('pre-tax');
    expect(taxGroup({ tax_group: 'roth' })).toBe('roth');
    expect(isTaxAdvantaged({ tax_group: 'health' })).toBe(true);
  });

  it('treats taxable and unknown groups as not tax-advantaged', () => {
    for (const tax_group of [null, '', 'brokerage', 'toString']) {
      expect(isTaxAdvantaged({ tax_group })).toBe(false);
    }
  });
});

describe('withInvestments', () => {
  it('splits investment accounts into tax-advantaged and taxable', () => {
    const p = makePayload();
    expect(withInvestments(p, true).investments.map((i) => i.account_id)).toEqual(['acc-joint-ira']);
    expect(withInvestments(p, false).investments.map((i) => i.account_id)).toEqual(['acc-brokerage']);
    expect(withInvestments(p, false).transactions).toBe(p.transactions);
  });
});

describe('taxSummary', () => {
  const payload = () => {
    const p = makePayload();
    p.accounts.push(
      account({ account_id: 'k1', name: '401k A', type: 'investment', tax_group: 'pre-tax', current_balance: 50000 }),
      account({ account_id: 'k2', name: '401k B', type: 'investment', tax_group: 'pre-tax', current_balance: null }),
      account({ account_id: 'r1', name: 'Roth', type: 'investment', tax_group: 'roth', current_balance: 12000.5 }),
      account({ account_id: 'h1', name: 'HSA', type: 'depository', tax_group: 'health', current_balance: 3000 }),
      account({ account_id: 'gone', type: 'investment', tax_group: 'roth', removed: true }),
      account({ account_id: 'off', type: 'investment', tax_group: 'roth', included: false }),
    );
    return p;
  };

  it('groups accounts in a fixed order with totals; mine leaves out joint accounts', () => {
    const s = taxSummary(payload(), 'mine');
    expect(s.groups.map((g) => g.group)).toEqual(['pre-tax', 'roth', 'health']);
    const preTax = s.groups[0];
    expect(preTax.rows.map((r) => r.account_id)).toEqual(['k1', 'k2']);
    expect([preTax.total, preTax.missingValues]).toEqual([50000, 1]);
    expect(s.total).toBe(65000.5);
  });

  it('household includes joint accounts', () => {
    const s = taxSummary(payload(), 'household');
    expect(s.groups[0].rows.map((r) => r.account_id)).toEqual(['k1', 'acc-joint-ira', 'k2']);
    expect(s.total).toBe(66000.5);
  });

  it('is empty without tax-advantaged accounts', () => {
    const p = makePayload();
    p.accounts = p.accounts.filter((a) => a.tax_group === null);
    expect(taxSummary(p, 'household')).toEqual({ groups: [], total: 0 });
  });
});

describe('yearlyMoneyIn', () => {
  it('sums money in and out per calendar year for tax-advantaged accounts only', () => {
    const p = makePayload();
    const ira = p.investments.find((i) => i.account_id === 'acc-joint-ira')!;
    ira.flows = [
      ['2025-03-01', 500],
      ['2025-06-01', 500],
      ['2025-07-01', -200],
      ['2026-01-15', 1000],
    ];
    expect(yearlyMoneyIn(p, 'mine')).toEqual([]);
    expect(yearlyMoneyIn(p, 'household')).toEqual([
      { year: '2026', account_id: 'acc-joint-ira', label: 'First Bank Joint IRA ••5555', movedIn: 1000, movedOut: 0 },
      { year: '2025', account_id: 'acc-joint-ira', label: 'First Bank Joint IRA ••5555', movedIn: 1000, movedOut: 200 },
    ]);
  });
});
