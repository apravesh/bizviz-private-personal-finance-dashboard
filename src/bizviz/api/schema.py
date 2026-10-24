"""Shared JSON payload contract: analytics builds it, the API serves it, the web app mirrors it
(web/src/lib/types.ts). Every key is always present; nullable values are typed `X | None`."""

from typing import TypedDict


class ItemStatus(TypedDict):
    item_id: str
    institution_id: str | None
    institution_name: str | None
    status: str
    error_code: str | None
    consent_expires_at: str | None
    last_success_at: str | None
    removed: bool


class AccountOut(TypedDict):
    account_id: str
    item_id: str
    institution_name: str | None
    name: str | None
    mask: str | None
    type: str
    subtype: str | None
    ownership: str
    included: bool
    reviewed: bool
    current_balance: float | None
    available_balance: float | None
    currency: str | None
    balance_as_of: str | None
    gone_at: str | None
    removed: bool
    duplicate_of_account: str | None
    superseded_by: str | None


class CategoryOut(TypedDict):
    category_id: str
    name: str
    kind: str
    applies_to: str
    hidden: bool


class RuleOut(TypedDict):
    rule_id: int
    merchant_key: str
    category_id: str | None
    flow_class: str | None
    recurring: bool | None


class ScopeClass(TypedDict):
    flow: str
    category_id: str
    recurring: bool


class OverrideOut(TypedDict):
    category_id: str | None
    flow_class: str | None
    recurring: bool | None


class TransactionOut(TypedDict):
    id: str
    account_id: str
    date: str
    amount: float
    name: str | None
    merchant_key: str | None
    merchant_display: str | None
    pending: bool
    mine: ScopeClass | None
    household: ScopeClass | None
    override: OverrideOut | None


class RecurringGroupOut(TypedDict):
    merchant_key: str
    merchant_display: str | None
    cadence: str
    typical_amount: float | None
    last_date: str | None
    count: int
    active: bool


class RecurringOut(TypedDict):
    mine: list[RecurringGroupOut]
    household: list[RecurringGroupOut]


class HoldingOut(TypedDict):
    security_name: str | None
    ticker: str | None
    quantity: float
    price: float | None
    value: float | None
    price_as_of: str | None


class EstimateMetaOut(TypedDict):
    anchor_date: str | None
    window_start: str | None
    window_end: str | None
    excluded_count: int
    excluded_value: float
    warnings: list[str]
    status: str


class InvestmentOut(TypedDict):
    account_id: str
    ownership: str
    actual: list[tuple[str, float]]
    estimated: list[tuple[str, float]]
    estimate_meta: EstimateMetaOut | None
    holdings: list[HoldingOut]
    flows: list[tuple[str, float]]


class NetWorthScope(TypedDict):
    assets: float
    debts: float
    stale_accounts: int


class NetWorthPoint(TypedDict):
    date: str
    mine: NetWorthScope
    household: NetWorthScope


class NetWorthOut(TypedDict):
    starts_on: str | None
    series: list[NetWorthPoint]


class SuspectCardPayments(TypedDict):
    count: int
    transaction_ids: list[str]


class SupersededOut(TypedDict):
    account_id: str
    superseded_by: str
    from_date: str | None


class QualityOut(TypedDict):
    suspect_card_payments: SuspectCardPayments
    duplicate_accounts: list[str]
    superseded_accounts: list[SupersededOut]


class StatusOut(TypedDict):
    newest_data_at: str | None
    items: list[ItemStatus]


class DataPayload(TypedDict):
    data_version: int
    generated_at: str | None
    status: StatusOut
    accounts: list[AccountOut]
    categories: list[CategoryOut]
    rules: list[RuleOut]
    transactions: list[TransactionOut]
    recurring: RecurringOut
    investments: list[InvestmentOut]
    net_worth: NetWorthOut
    quality: QualityOut


class StatusResponse(TypedDict):
    app_version: str
    data_version: int
    newest_data_at: str | None
    items: list[ItemStatus]
    syncing: bool


class SystemResponse(TypedDict):
    app_version: str
    filevault: bool | None
    time_machine: bool | None
    cert_expires_at: str | None
    ca_expires_at: str | None
    ca_expiring: bool
    hostname: str | None
    current_hostname: str | None
    hostname_mismatch: bool
    plaid_env: str | None
    plaid_configured: bool
    plaid_items_created_count: int
    recovered_items: list[str]
    login_locked_until: str | None
