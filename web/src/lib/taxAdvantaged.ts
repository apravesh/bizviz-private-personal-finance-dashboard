import { accountLabel } from './aggregate';
import type { AccountOut, DataPayload, Scope } from './types';

export type TaxGroup = 'pre-tax' | 'roth' | 'health' | 'education' | 'other';

export const TAX_GROUP_LABELS: Record<TaxGroup, string> = {
  'pre-tax': 'Pre-tax retirement',
  roth: 'Roth and tax-free',
  health: 'Health savings',
  education: 'Education savings',
  other: 'Other retirement',
};

const GROUP_ORDER: TaxGroup[] = ['pre-tax', 'roth', 'health', 'education', 'other'];

/** The account's tax-advantaged group, decided on the Mac from its Plaid subtype. */
export function taxGroup(account: Pick<AccountOut, 'tax_group'>): TaxGroup | null {
  const group = account.tax_group;
  return group !== null && Object.hasOwn(TAX_GROUP_LABELS, group) ? (group as TaxGroup) : null;
}

export function isTaxAdvantaged(account: Pick<AccountOut, 'tax_group'>): boolean {
  return taxGroup(account) !== null;
}

/** The payload with its investment accounts limited to tax-advantaged ones, or to the others. */
export function withInvestments(payload: DataPayload, taxAdvantaged: boolean): DataPayload {
  const advantaged = new Set(payload.accounts.filter(isTaxAdvantaged).map((a) => a.account_id));
  return {
    ...payload,
    investments: payload.investments.filter((i) => advantaged.has(i.account_id) === taxAdvantaged),
  };
}

export interface TaxAccountRow {
  account_id: string;
  label: string;
  group: TaxGroup;
  value: number | null;
  asOf: string | null;
}

export interface TaxGroupSummary {
  group: TaxGroup;
  label: string;
  rows: TaxAccountRow[];
  /** Sum of the rows that report a value. */
  total: number;
  missingValues: number;
}

export interface TaxSummary {
  groups: TaxGroupSummary[];
  total: number;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function visible(account: AccountOut, scope: Scope): boolean {
  return (
    account.included &&
    !account.removed &&
    account.duplicate_of_account === null &&
    (scope === 'household' || account.ownership === 'mine')
  );
}

/** Current value of each tax-advantaged account, grouped by tax treatment. */
export function taxSummary(payload: DataPayload, scope: Scope): TaxSummary {
  const byGroup = new Map<TaxGroup, TaxAccountRow[]>();
  for (const account of payload.accounts) {
    const group = taxGroup(account);
    if (group === null || !visible(account, scope)) continue;
    const rows = byGroup.get(group) ?? [];
    rows.push({
      account_id: account.account_id,
      label: accountLabel(account),
      group,
      value: account.current_balance,
      asOf: account.balance_as_of,
    });
    byGroup.set(group, rows);
  }
  const groups = GROUP_ORDER.filter((g) => byGroup.has(g)).map((group) => {
    const rows = byGroup.get(group)!.sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || a.label.localeCompare(b.label));
    return {
      group,
      label: TAX_GROUP_LABELS[group],
      rows,
      total: round2(rows.reduce((sum, r) => sum + (r.value ?? 0), 0)),
      missingValues: rows.filter((r) => r.value === null).length,
    };
  });
  return { groups, total: round2(groups.reduce((sum, g) => sum + g.total, 0)) };
}

export interface YearlyMoneyIn {
  year: string;
  account_id: string;
  label: string;
  /** Contributions, deposits and transfers in. */
  movedIn: number;
  /** Withdrawals and transfers out, as a positive number. */
  movedOut: number;
}

/** Money moved into and out of each tax-advantaged investment account per calendar year. */
export function yearlyMoneyIn(payload: DataPayload, scope: Scope): YearlyMoneyIn[] {
  const accounts = new Map(payload.accounts.map((a) => [a.account_id, a]));
  const rows = new Map<string, YearlyMoneyIn>();
  for (const inv of payload.investments) {
    const account = accounts.get(inv.account_id);
    if (!account || !isTaxAdvantaged(account) || !visible(account, scope)) continue;
    for (const [date, amount] of inv.flows) {
      const year = date.slice(0, 4);
      const key = `${year}|${inv.account_id}`;
      const row = rows.get(key) ?? { year, account_id: inv.account_id, label: accountLabel(account), movedIn: 0, movedOut: 0 };
      if (amount >= 0) row.movedIn = round2(row.movedIn + amount);
      else row.movedOut = round2(row.movedOut - amount);
      rows.set(key, row);
    }
  }
  return [...rows.values()].sort((a, b) => b.year.localeCompare(a.year) || b.movedIn - a.movedIn);
}
