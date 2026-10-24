// Mirrors src/bizviz/api/schema.py field for field.

export type Flow = 'earning' | 'spending' | 'internal' | 'ignored';
export type Scope = 'mine' | 'household';

export interface ItemStatus {
  item_id: string;
  institution_id: string | null;
  institution_name: string | null;
  status: string;
  error_code: string | null;
  consent_expires_at: string | null;
  last_success_at: string | null;
  removed: boolean;
}

export interface AccountOut {
  account_id: string;
  item_id: string;
  institution_name: string | null;
  name: string | null;
  mask: string | null;
  type: string;
  subtype: string | null;
  ownership: string;
  included: boolean;
  reviewed: boolean;
  current_balance: number | null;
  available_balance: number | null;
  currency: string | null;
  balance_as_of: string | null;
  gone_at: string | null;
  removed: boolean;
  duplicate_of_account: string | null;
  superseded_by: string | null;
}

export interface CategoryOut {
  category_id: string;
  name: string;
  kind: string;
  applies_to: string;
  hidden: boolean;
}

export interface RuleOut {
  rule_id: number;
  merchant_key: string;
  category_id: string | null;
  flow_class: string | null;
  recurring: boolean | null;
}

export interface ScopeClass {
  flow: string;
  category_id: string;
  recurring: boolean;
}

export interface OverrideOut {
  category_id: string | null;
  flow_class: string | null;
  recurring: boolean | null;
}

export interface TransactionOut {
  id: string;
  account_id: string;
  date: string;
  amount: number;
  name: string | null;
  merchant_key: string | null;
  merchant_display: string | null;
  pending: boolean;
  mine: ScopeClass | null;
  household: ScopeClass | null;
  override: OverrideOut | null;
}

export interface RecurringGroupOut {
  merchant_key: string;
  merchant_display: string | null;
  cadence: string;
  typical_amount: number | null;
  last_date: string | null;
  count: number;
  active: boolean;
}

export interface RecurringOut {
  mine: RecurringGroupOut[];
  household: RecurringGroupOut[];
}

export interface HoldingOut {
  security_name: string | null;
  ticker: string | null;
  quantity: number;
  price: number | null;
  value: number | null;
  price_as_of: string | null;
}

export interface EstimateMetaOut {
  anchor_date: string | null;
  window_start: string | null;
  window_end: string | null;
  excluded_count: number;
  excluded_value: number;
  warnings: string[];
  status: string;
}

export interface InvestmentOut {
  account_id: string;
  ownership: string;
  actual: [string, number][];
  estimated: [string, number][];
  estimate_meta: EstimateMetaOut | null;
  holdings: HoldingOut[];
  /** Net money moved into (+) or out of (-) the account per day: deposits, withdrawals, cash transfers. */
  flows: [string, number][];
}

export interface NetWorthScope {
  assets: number;
  debts: number;
  stale_accounts: number;
}

export interface NetWorthPoint {
  date: string;
  mine: NetWorthScope;
  household: NetWorthScope;
}

export interface NetWorthOut {
  starts_on: string | null;
  series: NetWorthPoint[];
}

export interface SuspectCardPayments {
  count: number;
  transaction_ids: string[];
}

export interface SupersededOut {
  account_id: string;
  superseded_by: string;
  from_date: string | null;
}

export interface QualityOut {
  suspect_card_payments: SuspectCardPayments;
  duplicate_accounts: string[];
  superseded_accounts: SupersededOut[];
}

export interface StatusOut {
  newest_data_at: string | null;
  items: ItemStatus[];
}

export interface DataPayload {
  data_version: number;
  generated_at: string | null;
  status: StatusOut;
  accounts: AccountOut[];
  categories: CategoryOut[];
  rules: RuleOut[];
  transactions: TransactionOut[];
  recurring: RecurringOut;
  investments: InvestmentOut[];
  net_worth: NetWorthOut;
  quality: QualityOut;
}

export interface StatusResponse {
  app_version: string;
  data_version: number;
  newest_data_at: string | null;
  items: ItemStatus[];
  syncing: boolean;
}

export interface SystemResponse {
  app_version: string;
  filevault: boolean | null;
  time_machine: boolean | null;
  cert_expires_at: string | null;
  ca_expires_at: string | null;
  ca_expiring: boolean;
  hostname: string | null;
  current_hostname: string | null;
  hostname_mismatch: boolean;
  plaid_env: string | null;
  plaid_configured: boolean;
  plaid_items_created_count: number;
  recovered_items: string[];
  login_locked_until: string | null;
}
