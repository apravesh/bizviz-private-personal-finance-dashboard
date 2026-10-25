<script lang="ts">
  import { memoized, money, netWorthSeries, type Granularity } from '../lib/aggregate';
  import { assetAllocation, holdingsBreakdown } from '../lib/allocation';
  import { categoryPieOption, netWorthOption } from '../lib/chartOptions';
  import { formatDate } from '../lib/format';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';

  let { payload }: { payload: DataPayload } = $props();

  let granularity = $state<Granularity>('daily');

  const result = $derived(
    memoized(['networth', payload.data_version, $store.scope, granularity], () =>
      netWorthSeries(payload, $store.scope, granularity),
    ),
  );
  const daily = $derived(
    memoized(['networth', payload.data_version, $store.scope, 'daily'], () =>
      netWorthSeries(payload, $store.scope, 'daily'),
    ),
  );
  const option = $derived(netWorthOption(result.points));
  const allocation = $derived(
    memoized(['allocation', payload.data_version, $store.scope], () => assetAllocation(payload, $store.scope)),
  );
  const holdings = $derived(
    memoized(['holdings', payload.data_version, $store.scope], () => holdingsBreakdown(payload, $store.scope)),
  );
</script>

<section class="card" aria-labelledby="networth-title">
  <div class="row head">
    <h2 id="networth-title">Net worth</h2>
    <div class="seg" role="group" aria-label="Net worth granularity">
      <button type="button" aria-pressed={granularity === 'daily'} onclick={() => (granularity = 'daily')}>Daily</button>
      <button type="button" aria-pressed={granularity === 'monthly'} onclick={() => (granularity = 'monthly')}>
        Monthly average
      </button>
    </div>
  </div>

  {#if daily.current}
    <dl class="current">
      <div><dt>Net worth</dt><dd>{money(daily.current.net)}</dd></div>
      <div class="retirement"><dt>Retirement &amp; tax-advantaged</dt><dd>{money(daily.current.retirement)}</dd></div>
      <div class="other"><dt>Other assets</dt><dd>{money(daily.current.otherAssets)}</dd></div>
      <div class="debts"><dt>Debts</dt><dd>{money(daily.current.debts)}</dd></div>
    </dl>
    {#if daily.current.stale_accounts > 0}
      <p class="muted">
        {daily.current.stale_accounts} account{daily.current.stale_accounts === 1 ? '' : 's'} not updated recently.
      </p>
    {/if}
    <Chart {option} label="Net worth over time" />
  {:else}
    <p class="muted">No net worth history yet.</p>
  {/if}
  {#if daily.current}
    <p class="muted">
      Bars: green is retirement and other tax-advantaged accounts (401(k), IRA, Roth, HSA, 529), blue is
      everything else you own (cash and taxable investments), red below zero is debts. The line is net worth.
    </p>
  {/if}
  {#if allocation.slices.length > 0}
    <div class="where">
      <h3>Where your money is today</h3>
      <div class="pies">
        <div>
          <h4>Cash, stocks and other assets</h4>
          <p class="muted">{money(allocation.total)} in assets</p>
          <Chart option={categoryPieOption(allocation)} label="Assets by kind" height={320} />
          <p class="muted">
            Current balances, debts not included. Cash is bank accounts, cash and money-market holdings{allocation.uninvestedCash >
            0
              ? `, and ${money(allocation.uninvestedCash)} of uninvested cash in investment accounts`
              : ''}.
            {#if allocation.notBrokenDown.length > 0}
              Not broken down: {allocation.notBrokenDown.join(', ')} report a balance but no holdings.
            {/if}
            {#if allocation.unvaluedHoldings > 0}
              {allocation.unvaluedHoldings} holding{allocation.unvaluedHoldings === 1 ? ' has' : 's have'} no reported
              value and {allocation.unvaluedHoldings === 1 ? 'is' : 'are'} left out.
            {/if}
          </p>
        </div>
        <div>
          <h4>Investments by holding</h4>
          {#if holdings.slices.length === 0}
            <p class="muted">No holdings reported.</p>
          {:else}
            <p class="muted">{money(holdings.total)} in stocks, funds and crypto</p>
            <Chart option={categoryPieOption(holdings)} label="Investments by holding" height={320} />
            <p class="muted">
              Combined across accounts by ticker; the 10 largest are shown{holdings.otherCount > 0
                ? ` and ${holdings.otherCount} smaller ones are grouped as Other`
                : ''}. Cash holdings are left out.
              {#if holdings.withoutHoldings.length > 0}
                Not included because they report no holdings: {holdings.withoutHoldings.join(', ')}.
              {/if}
            </p>
          {/if}
        </div>
      </div>
    </div>
  {/if}
  {#if payload.net_worth.starts_on}
    <p class="muted">
      Net worth history starts on {formatDate(payload.net_worth.starts_on)}, when accounts were linked.
    </p>
  {/if}
</section>

<style>
  .head {
    justify-content: space-between;
  }
  .where {
    margin-top: 16px;
    padding-top: 8px;
    border-top: 1px solid var(--line);
  }
  .where h3 {
    margin: 0;
  }
  .where h4 {
    margin: 8px 0 0;
    font-size: 0.95rem;
  }
  .where p {
    margin: 4px 0;
  }
  .pies {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .current {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin: 0 0 8px;
  }
  dt {
    color: var(--muted);
    font-size: 0.85rem;
  }
  /* Each figure carries its chart color as a left rule. */
  .current .retirement,
  .current .other,
  .current .debts {
    border-left: 4px solid;
    padding-left: 8px;
  }
  .current .retirement {
    border-color: #2a9d6f;
  }
  .current .other {
    border-color: #2f6fdd;
  }
  .current .debts {
    border-color: #d64550;
  }
  dd {
    margin: 0;
    font-size: 1.2rem;
    font-weight: 600;
  }
</style>
