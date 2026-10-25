<script lang="ts">
  import {
    investmentGainsSeries,
    investmentSeries,
    memoized,
    money,
    stackAccountsByMonth,
    type Granularity,
    type InvestmentAccountSeries,
  } from '../lib/aggregate';
  import { investmentOption, stackedMonthsOption } from '../lib/chartOptions';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';
  import HoldingsList from './HoldingsList.svelte';

  interface Props {
    payload: DataPayload;
    /** Distinguishes several copies of this section (memo keys, element ids). */
    id?: string;
    title?: string;
    emptyText?: string;
    /** Draw stacked monthly bars instead of one line per account. */
    stacked?: boolean;
    /** With stacked bars, offer a split by these groups (account id -> group) besides by account. */
    groupOf?: (accountId: string) => { key: string; label: string };
    groupLabel?: string;
    /** List each account's holdings under the chart (the Investments tab shows them separately). */
    showHoldings?: boolean;
  }

  let {
    payload,
    id = 'investments',
    title = 'Investments',
    emptyText = 'No investment accounts in this view.',
    stacked = false,
    groupOf,
    groupLabel = 'Group',
    showHoldings = true,
  }: Props = $props();

  let chosenGranularity = $state<Granularity>('daily');
  let view = $state<'value' | 'gains'>('value');
  let split = $state<'account' | 'group'>('account');
  // Stacked bars need every account on the same dates, so they use monthly averages.
  const granularity = $derived<Granularity>(stacked ? 'monthly' : chosenGranularity);

  const accounts = $derived(
    memoized([id, payload.data_version, $store.scope, granularity, view], () =>
      view === 'gains'
        ? investmentGainsSeries(payload, $store.scope, granularity)
        : investmentSeries(payload, $store.scope, granularity),
    ),
  );
  const option = $derived.by(() => {
    if (!stacked) return investmentOption(accounts);
    const byGroup =
      split === 'group' && groupOf ? (a: InvestmentAccountSeries) => groupOf(a.account_id) : undefined;
    return stackedMonthsOption(stackAccountsByMonth(accounts, byGroup), view === 'gains' ? 'Total gains' : 'Total value');
  });
  const noFlowsReported = $derived(
    accounts.filter((a) => payload.investments.find((i) => i.account_id === a.account_id)?.flows.length === 0),
  );
</script>

<section class="card" aria-labelledby="{id}-title">
  <div class="row head">
    <h2 id="{id}-title">{title}</h2>
    <div class="seg" role="group" aria-label="{title} view">
      <button type="button" aria-pressed={view === 'value'} onclick={() => (view = 'value')}>Account value</button>
      <button type="button" aria-pressed={view === 'gains'} onclick={() => (view = 'gains')}>
        Gains (excl. deposits)
      </button>
    </div>
    {#if stacked}
      {#if groupOf}
        <div class="seg" role="group" aria-label="{title} split">
          <button type="button" aria-pressed={split === 'account'} onclick={() => (split = 'account')}>By account</button>
          <button type="button" aria-pressed={split === 'group'} onclick={() => (split = 'group')}>By {groupLabel}</button>
        </div>
      {/if}
    {:else}
      <div class="seg" role="group" aria-label="{title} granularity">
        <button type="button" aria-pressed={granularity === 'daily'} onclick={() => (chosenGranularity = 'daily')}
          >Daily</button
        >
        <button type="button" aria-pressed={granularity === 'monthly'} onclick={() => (chosenGranularity = 'monthly')}>
          Monthly average
        </button>
      </div>
    {/if}
  </div>

  {#if accounts.length === 0}
    <p class="muted">{emptyText}</p>
  {:else}
    <Chart
      {option}
      label={view === 'gains' ? 'Investment gains excluding deposits over time' : 'Investment account values over time'}
      height={360}
    />
    {#if view === 'gains'}
      <p class="muted">
        Change in each account's value since the start of its history, minus money moved in and plus money
        moved out (deposits, withdrawals and cash transfers), so moving money does not look like growth or
        loss. Dividends, interest and fees count as gains. Shares moved in from another broker are not taken
        out.
      </p>
      {#if noFlowsReported.length > 0}
        <p class="muted">
          No deposits or withdrawals were reported for {noFlowsReported.map((a) => a.label).join(', ')}, so
          {noFlowsReported.length === 1 ? 'its' : 'their'} gains include any money moved in or out.
        </p>
      {/if}
    {/if}
    {#if stacked}
      <p class="muted">
        Monthly averages, stacked. Hatched bars include values estimated from transactions and public prices
        (before you linked); solid bars are values reported by your institution.
      </p>
    {:else}
      <p class="muted">
        Dashed = estimated from transactions and public prices; solid = values reported by your institution.
      </p>
    {/if}
    <ul class="notes">
      {#each accounts as account (account.account_id)}
        {#if account.meta}
          {#if account.meta.status === 'downloading'}
            <li>{account.label}: Investment history is still downloading.</li>
          {/if}
          {#if account.meta.excluded_count > 0}
            <li>
              {account.label}: Estimate excludes {account.meta.excluded_count}
              {account.meta.excluded_count === 1 ? 'holding' : 'holdings'} ({money(account.meta.excluded_value)} of today's value).
            </li>
          {/if}
          {#each account.meta.warnings as warning}
            <li>{account.label}: {warning}</li>
          {/each}
        {/if}
      {/each}
    </ul>

    {#if showHoldings}
      <HoldingsList {payload} {accounts} />
    {/if}
  {/if}
</section>

<style>
  .head {
    justify-content: space-between;
  }
  .notes {
    padding-left: 20px;
  }
</style>
