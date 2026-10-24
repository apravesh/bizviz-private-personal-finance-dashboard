<script lang="ts">
  import { currentMonth, memoized, money, spendingByCategory, type PiePeriod } from '../lib/aggregate';
  import { categoryPieOption } from '../lib/chartOptions';
  import { monthLabel } from '../lib/format';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';

  let { payload }: { payload: DataPayload } = $props();

  const periods: { value: PiePeriod; label: string }[] = [
    { value: 'this-month', label: 'This month (in progress)' },
    { value: 'last-month', label: 'Last month' },
    { value: 'last-3', label: 'Last 3 full months' },
    { value: 'last-12', label: 'Last 12 full months' },
    { value: 'all', label: 'All months shown' },
  ];

  let period = $state<PiePeriod>('last-12');

  const today = new Date();
  const breakdown = $derived(
    memoized(['pie', payload.data_version, $store.scope, period, currentMonth(today)], () =>
      spendingByCategory(payload, $store.scope, period, today),
    ),
  );
  const option = $derived(categoryPieOption(breakdown));
  const range = $derived(
    breakdown.from === null
      ? ''
      : breakdown.from === breakdown.to
        ? monthLabel(breakdown.from)
        : `${monthLabel(breakdown.from)} – ${monthLabel(breakdown.to!)}`,
  );
</script>

<div class="pie">
  <div class="row head">
    <h3 id="spending-pie-title">Spending by category</h3>
    <label class="muted">
      Period
      <select bind:value={period} aria-label="Spending by category period">
        {#each periods as p}<option value={p.value}>{p.label}</option>{/each}
      </select>
    </label>
  </div>
  {#if breakdown.slices.length === 0}
    <p class="muted">No spending in this period.</p>
  {:else}
    <p class="muted">
      {range}{breakdown.includesInProgress ? ' (current month in progress)' : ''}: {money(breakdown.total)} total.
    </p>
    <Chart {option} label="Spending by category, {range}" height={360} />
  {/if}
  {#if breakdown.netRefundCategories.length > 0}
    <p class="muted">
      Not shown because refunds were larger than spending in this period: {breakdown.netRefundCategories.join(', ')}.
    </p>
  {/if}
</div>

<style>
  .pie {
    margin-top: 16px;
    padding-top: 8px;
    border-top: 1px solid var(--line);
  }
  .head {
    justify-content: space-between;
  }
  h3 {
    margin: 0;
  }
</style>
