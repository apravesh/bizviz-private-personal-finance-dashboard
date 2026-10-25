import { accountLabel, type CategorySlice } from './aggregate';
import type { AccountOut, DataPayload, HoldingOut, Scope } from './types';

export type AssetClass = 'cash' | 'stocks' | 'funds' | 'bonds' | 'crypto' | 'other' | 'unknown';

export const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  cash: 'Cash',
  stocks: 'Stocks',
  funds: 'ETFs & funds',
  bonds: 'Bonds',
  crypto: 'Crypto',
  other: 'Other',
  unknown: 'Not broken down',
};

const HOLDINGS_TOP = 10;
// Below this gap between an account's balance and its holdings, the two are taken to agree.
const RESIDUAL_TOLERANCE = 1;

export interface PieData {
  slices: CategorySlice[];
  total: number;
}

export interface Allocation extends PieData {
  /** Investment accounts that report a balance but no holdings; counted as "Not broken down". */
  notBrokenDown: string[];
  /** Balance above the reported holdings in investment accounts, counted as cash. */
  uninvestedCash: number;
  /** Holdings with no reported value, left out. */
  unvaluedHoldings: number;
}

export interface HoldingsBreakdown extends PieData {
  /** How many holdings are folded into "Other holdings". */
  otherCount: number;
  /** Investment accounts that report no holdings, so are not in this chart. */
  withoutHoldings: string[];
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Plaid labels some crypto (stablecoins) cash-equivalent; it is still crypto here. */
export function holdingClass(h: Pick<HoldingOut, 'security_type' | 'is_cash_equivalent'>): AssetClass {
  const type = (h.security_type ?? '').toLowerCase();
  if (type === 'cryptocurrency') return 'crypto';
  if (type === 'cash' || h.is_cash_equivalent) return 'cash';
  if (type === 'equity') return 'stocks';
  if (type === 'etf' || type === 'mutual fund') return 'funds';
  if (type === 'fixed income') return 'bonds';
  return 'other';
}

function visible(account: AccountOut, scope: Scope): boolean {
  return (
    account.included &&
    !account.removed &&
    account.duplicate_of_account === null &&
    (scope === 'household' || account.ownership === 'mine')
  );
}

function investmentAccounts(payload: DataPayload, scope: Scope) {
  const holdings = new Map(payload.investments.map((i) => [i.account_id, i.holdings]));
  return payload.accounts
    .filter((a) => a.type === 'investment' && visible(a, scope))
    .map((account) => {
      const valued = (holdings.get(account.account_id) ?? []).filter((h) => h.value !== null);
      return { account, holdings: holdings.get(account.account_id) ?? [], valued };
    });
}

function toSlices(totals: Map<string, { label: string; value: number }>): CategorySlice[] {
  return [...totals.entries()]
    .map(([key, { label, value }]) => ({ key, label, value: round2(value) }))
    .filter((s) => s.value > 0)
    .sort((a, b) => b.value - a.value || a.key.localeCompare(b.key));
}

/** Where assets sit today: bank cash plus investment holdings by kind. Debts are not included. */
export function assetAllocation(payload: DataPayload, scope: Scope): Allocation {
  const totals = new Map<string, { label: string; value: number }>();
  const add = (cls: AssetClass, value: number) => {
    const entry = totals.get(cls) ?? { label: ASSET_CLASS_LABELS[cls], value: 0 };
    entry.value += value;
    totals.set(cls, entry);
  };
  for (const account of payload.accounts) {
    if (account.type === 'depository' && visible(account, scope) && (account.current_balance ?? 0) > 0) {
      add('cash', account.current_balance!);
    }
  }
  const notBrokenDown: string[] = [];
  let uninvestedCash = 0;
  let unvaluedHoldings = 0;
  for (const { account, holdings, valued } of investmentAccounts(payload, scope)) {
    unvaluedHoldings += holdings.length - valued.length;
    if (valued.length === 0) {
      if ((account.current_balance ?? 0) > 0) {
        add('unknown', account.current_balance!);
        notBrokenDown.push(accountLabel(account));
      }
      continue;
    }
    let held = 0;
    for (const h of valued) {
      add(holdingClass(h), h.value!);
      held += h.value!;
    }
    const residual = (account.current_balance ?? held) - held;
    if (residual > RESIDUAL_TOLERANCE) {
      add('cash', residual);
      uninvestedCash += residual;
    }
  }
  const slices = toSlices(totals);
  return {
    slices,
    total: round2(slices.reduce((sum, s) => sum + s.value, 0)),
    notBrokenDown,
    uninvestedCash: round2(uninvestedCash),
    unvaluedHoldings,
  };
}

/** Value per security across investment accounts (cash holdings left out), largest first. */
export function holdingsBreakdown(payload: DataPayload, scope: Scope): HoldingsBreakdown {
  const totals = new Map<string, { label: string; value: number }>();
  const withoutHoldings: string[] = [];
  for (const { account, valued } of investmentAccounts(payload, scope)) {
    if (valued.length === 0) {
      if ((account.current_balance ?? 0) > 0) withoutHoldings.push(accountLabel(account));
      continue;
    }
    for (const h of valued) {
      if (holdingClass(h) === 'cash') continue;
      const key = h.ticker ?? h.security_name ?? 'Unknown';
      const label = h.ticker ?? h.security_name ?? 'Unknown holding';
      const entry = totals.get(key) ?? { label, value: 0 };
      entry.value += h.value!;
      totals.set(key, entry);
    }
  }
  const all = toSlices(totals);
  let slices = all;
  let otherCount = 0;
  if (all.length > HOLDINGS_TOP + 1) {
    const rest = all.slice(HOLDINGS_TOP);
    otherCount = rest.length;
    slices = [
      ...all.slice(0, HOLDINGS_TOP),
      { key: 'other', label: `Other (${rest.length} holdings)`, value: round2(rest.reduce((sum, s) => sum + s.value, 0)) },
    ];
  }
  return { slices, total: round2(all.reduce((sum, s) => sum + s.value, 0)), otherCount, withoutHoldings };
}
