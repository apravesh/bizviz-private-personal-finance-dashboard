<script lang="ts">
  import { memoized, money, netWorthSeries, type Granularity } from '../lib/aggregate';
  import { netWorthOption } from '../lib/chartOptions';
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
      <div><dt>Assets</dt><dd>{money(daily.current.assets)}</dd></div>
      <div><dt>Debts</dt><dd>{money(daily.current.debts)}</dd></div>
      <div><dt>Net worth</dt><dd>{money(daily.current.net)}</dd></div>
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
  dd {
    margin: 0;
    font-size: 1.2rem;
    font-weight: 600;
  }
</style>
