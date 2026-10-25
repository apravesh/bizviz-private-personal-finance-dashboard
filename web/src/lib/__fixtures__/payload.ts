import type { AccountOut, DataPayload, ScopeClass, TransactionOut } from '../types';

export const TODAY = '2026-10-19';

export function account(over: Partial<AccountOut> & { account_id: string }): AccountOut {
  return {
    item_id: 'item-1',
    institution_name: 'First Bank',
    name: 'Checking',
    mask: '1111',
    type: 'depository',
    subtype: 'checking',
    ownership: 'mine',
    included: true,
    reviewed: true,
    current_balance: 1000,
    available_balance: 1000,
    currency: 'USD',
    balance_as_of: '2026-10-18T10:00:00Z',
    gone_at: null,
    removed: false,
    duplicate_of_account: null,
    superseded_by: null,
    tax_group: null,
    ...over,
  };
}

function cls(flow: string, category_id: string, recurring = false): ScopeClass {
  return { flow, category_id, recurring };
}

export function tx(
  id: string,
  date: string,
  amount: number,
  over: Partial<TransactionOut> = {},
): TransactionOut {
  const flow = amount < 0 ? 'earning' : 'spending';
  const category = amount < 0 ? 'income' : 'groceries';
  return {
    id,
    account_id: 'acc-checking',
    date,
    amount,
    name: `RAW ${id}`,
    merchant_key: `merchant-${id}`,
    merchant_display: `Merchant ${id}`,
    pending: false,
    pfc_detailed: null,
    mine: cls(flow, category),
    household: cls(flow, category),
    override: null,
    ...over,
  };
}

export function makePayload(): DataPayload {
  const transactions: TransactionOut[] = [
    tx('t1', '2026-08-03', 100, { merchant_key: 'shop', merchant_display: 'Shop' }),
    tx('t2', '2026-08-15', 50, {
      merchant_key: 'cafe',
      merchant_display: 'Cafe',
      mine: cls('spending', 'dining', true),
      household: cls('spending', 'dining', true),
    }),
    tx('t3', '2026-08-20', -30, { merchant_key: 'shop', merchant_display: 'Shop' }),
    tx('t4', '2026-09-01', 200, {
      merchant_key: 'shop',
      merchant_display: 'Shop',
      account_id: 'acc-card',
    }),
    tx('t5', '2026-09-10', -3000, {
      merchant_key: 'employer',
      merchant_display: 'Employer',
      mine: cls('earning', 'income'),
      household: cls('earning', 'income'),
    }),
    tx('t6', '2026-09-12', 500, {
      merchant_key: 'transfer',
      merchant_display: 'Transfer',
      mine: cls('internal', 'transfer'),
      household: cls('internal', 'transfer'),
    }),
    tx('t7', '2026-10-02', 80, {
      merchant_key: 'cafe',
      merchant_display: 'Cafe',
      pending: true,
      mine: cls('spending', 'dining', true),
      household: cls('spending', 'dining', true),
    }),
    tx('t8', '2026-10-05', 40, {
      merchant_key: 'spouse-shop',
      merchant_display: 'Spouse Shop',
      account_id: 'acc-joint',
      mine: null,
      household: cls('spending', 'groceries'),
    }),
    tx('t9', '2026-10-06', -1000, {
      merchant_key: 'employer',
      merchant_display: 'Employer',
      account_id: 'acc-joint',
      mine: null,
      household: cls('earning', 'income'),
    }),
  ];
  return {
    data_version: 7,
    generated_at: '2026-10-19T12:00:00Z',
    status: {
      newest_data_at: '2026-10-19T11:00:00Z',
      items: [
        {
          item_id: 'item-1',
          institution_id: 'ins_1',
          institution_name: 'First Bank',
          status: 'ok',
          error_code: null,
          consent_expires_at: null,
          last_success_at: '2026-10-19T11:00:00Z',
          removed: false,
        },
      ],
    },
    accounts: [
      account({ account_id: 'acc-checking' }),
      account({ account_id: 'acc-card', name: 'Visa', mask: '2222', type: 'credit', subtype: 'credit card' }),
      account({ account_id: 'acc-joint', name: 'Joint', mask: '3333', ownership: 'joint' }),
      account({
        account_id: 'acc-brokerage',
        name: 'Brokerage',
        mask: '4444',
        type: 'investment',
        subtype: 'brokerage',
      }),
      account({
        account_id: 'acc-joint-ira',
        name: 'Joint IRA',
        mask: '5555',
        type: 'investment',
        subtype: 'ira',
        ownership: 'joint',
        tax_group: 'pre-tax',
      }),
    ],
    categories: [
      { category_id: 'groceries', name: 'Groceries', kind: 'builtin', applies_to: 'spending', hidden: false },
      { category_id: 'dining', name: 'Dining', kind: 'builtin', applies_to: 'spending', hidden: false },
      { category_id: 'income', name: 'Income', kind: 'builtin', applies_to: 'earning', hidden: false },
      { category_id: 'transfer', name: 'Transfer', kind: 'builtin', applies_to: 'both', hidden: false },
      { category_id: 'secret', name: 'Secret', kind: 'custom', applies_to: 'both', hidden: true },
    ],
    rules: [],
    transactions,
    recurring: {
      mine: [
        {
          merchant_key: 'cafe',
          merchant_display: 'Cafe',
          cadence: 'monthly',
          typical_amount: 50,
          last_date: '2026-10-02',
          count: 3,
          active: true,
        },
      ],
      household: [],
    },
    investments: [
      {
        account_id: 'acc-brokerage',
        ownership: 'mine',
        actual: [
          ['2026-09-30', 100],
          ['2026-10-01', 110],
          ['2026-10-02', 120],
        ],
        estimated: [
          ['2026-08-30', 90],
          ['2026-09-01', 95],
          ['2026-09-30', 100],
        ],
        estimate_meta: {
          anchor_date: '2026-09-30',
          window_start: '2026-08-30',
          window_end: '2026-09-30',
          excluded_count: 0,
          excluded_value: 0,
          warnings: [],
          status: 'ready',
        },
        holdings: [
          {
            security_name: 'Index Fund',
            ticker: 'VTI',
            quantity: 2,
            price: 60,
            value: 120,
            price_as_of: '2026-10-02',
            security_type: 'etf',
            is_cash_equivalent: false,
          },
        ],
        flows: [['2026-10-01', 5]],
      },
      {
        account_id: 'acc-joint-ira',
        ownership: 'joint',
        actual: [['2026-10-01', 500]],
        estimated: [],
        estimate_meta: null,
        holdings: [],
        flows: [],
      },
    ],
    net_worth: {
      starts_on: '2026-10-01',
      series: [
        {
          date: '2026-09-28',
          mine: { assets: 800, debts: 300, stale_accounts: 0, retirement: 200 },
          household: { assets: 1800, debts: 300, stale_accounts: 0, retirement: 700 },
        },
        {
          date: '2026-10-01',
          mine: { assets: 1000, debts: 200, stale_accounts: 0, retirement: 300 },
          household: { assets: 2000, debts: 200, stale_accounts: 1, retirement: 800 },
        },
        {
          date: '2026-10-02',
          mine: { assets: 1200, debts: 100, stale_accounts: 2, retirement: 300 },
          household: { assets: 2200, debts: 100, stale_accounts: 0, retirement: 800 },
        },
        {
          date: '2026-10-03',
          mine: { assets: 1400, debts: 0, stale_accounts: 0, retirement: 400 },
          household: { assets: 2400, debts: 0, stale_accounts: 0, retirement: 900 },
        },
      ],
    },
    quality: {
      suspect_card_payments: { count: 0, transaction_ids: [] },
      duplicate_accounts: [],
      superseded_accounts: [],
    },
  };
}
