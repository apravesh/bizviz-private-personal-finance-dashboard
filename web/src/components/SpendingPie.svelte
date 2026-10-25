<script lang="ts">
  import {
    EVERYDAY_EXCLUSION,
    currentMonth,
    memoized,
    money,
    sliceFilters,
    spendingByCategory,
    type CategoryBreakdown,
    type PiePeriod,
  } from '../lib/aggregate';
  import { categoryPieOption } from '../lib/chartOptions';
  import { monthLabel } from '../lib/format';
  import { store } from '../lib/store';
  import { transactionsHref } from '../lib/txfilter';
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
  const all = $derived(
    memoized(['pie', payload.data_version, $store.scope, period, currentMonth(today)], () =>
      spendingByCategory(payload, $store.scope, period, today),
    ),
  );
  const everyday = $derived(
    memoized(['pie-everyday', payload.data_version, $store.scope, period, currentMonth(today)], () =>
      spendingByCategory(payload, $store.scope, period, today, EVERYDAY_EXCLUSION),
    ),
  );
  const pies = $derived([
    { id: 'all', title: 'All spending', breakdown: all },
    { id: 'everyday', title: 'Excluding unlinked-card payments, rent and transfers out', breakdown: everyday },
  ]);

  function rangeOf(b: CategoryBreakdown): string {
    if (b.from === null) return '';
    return b.from === b.to ? monthLabel(b.from) : `${monthLabel(b.from)} – ${monthLabel(b.to!)}`;
  }

  function opener(breakdown: CategoryBreakdown): (params: { data?: unknown }) => void {
    return (params) => {
      const id = (params.data as { id?: string } | undefined)?.id;
      if (id) location.hash = transactionsHref(sliceFilters(breakdown, id));
    };
  }
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
  {#if all.from !== null}
    <p class="muted">{rangeOf(all)}{all.includesInProgress ? ' (current month in progress)' : ''}</p>
  {/if}
  <div class="pies">
    {#each pies as pie (pie.id)}
      <div class="one" data-pie={pie.id}>
        <h4>{pie.title}</h4>
        {#if pie.breakdown.slices.length === 0}
          <p class="muted">No spending in this period.</p>
        {:else}
          <p class="muted">{money(pie.breakdown.total)} total</p>
          <Chart
            option={categoryPieOption(pie.breakdown)}
            label="{pie.title}, by category, {rangeOf(pie.breakdown)}"
            height={340}
            onclick={opener(pie.breakdown)}
          />
        {/if}
        {#if pie.breakdown.netRefundCategories.length > 0}
          <p class="muted">
            Not shown because refunds were larger than spending in this period: {pie.breakdown.netRefundCategories.join(
              ', ',
            )}.
          </p>
        {/if}
      </div>
    {/each}
  </div>
  <p class="muted">Click a category to see its transactions.</p>
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
  h4 {
    margin: 8px 0 0;
    font-size: 0.95rem;
  }
  .one p {
    margin: 4px 0;
  }
  .pies {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
</style>
