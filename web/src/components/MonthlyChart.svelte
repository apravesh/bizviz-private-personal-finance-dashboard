<script lang="ts">
  import {
    currentMonth,
    memoized,
    monthlyEarnings,
    monthlySpending,
    type EarningsSplit,
    type SpendingSplit,
  } from '../lib/aggregate';
  import { barOption } from '../lib/chartOptions';
  import type { Snippet } from 'svelte';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';
  import Drilldown from './Drilldown.svelte';

  interface Props {
    payload: DataPayload;
    kind: 'spending' | 'earning';
    title: string;
    splits: { value: string; label: string }[];
    /** Extra content shown inside the card. */
    children?: Snippet;
    /**
     * Card heading when the card holds more than the monthly bars: the extra content then comes
     * first and the bars last, under `title`.
     */
    cardTitle?: string;
  }

  let { payload, kind, title, splits, children, cardTitle }: Props = $props();

  let split = $state('none');
  let drill = $state<{ seriesKey: string; month: string } | null>(null);

  const today = new Date();
  const result = $derived(
    memoized(['monthly', kind, payload.data_version, $store.scope, split, currentMonth(today)], () =>
      kind === 'spending'
        ? monthlySpending(payload, $store.scope, split as SpendingSplit, today)
        : monthlyEarnings(payload, $store.scope, split as EarningsSplit, today),
    ),
  );
  const option = $derived(barOption(result, kind === 'spending' ? 'Spending' : 'Earnings', true));
  const drillLabel = $derived(result.series.find((s) => s.key === drill?.seriesKey)?.label ?? '');

  function openDrill(params: { seriesId?: string; name?: string }): void {
    if (params.seriesId && params.name) drill = { seriesKey: params.seriesId, month: params.name };
  }
</script>

{#snippet splitControl()}
  <label class="muted">
    Split by
    <select bind:value={split} aria-label="Split {title}">
      {#each splits as s}<option value={s.value}>{s.label}</option>{/each}
    </select>
  </label>
{/snippet}

{#snippet monthlyBars()}
  {#if payload.transactions.length === 0}
    <p class="muted">No transactions yet.</p>
  {/if}
  <Chart {option} label="{title} by month" onclick={openDrill} />
  <p class="muted">Pending transactions are included; the tooltip shows how much of a month is pending.</p>
  {#if drill}
    <Drilldown
      {payload}
      scope={$store.scope}
      flow={kind}
      split={split as SpendingSplit}
      seriesKey={drill.seriesKey}
      seriesLabel={drillLabel}
      month={drill.month}
      onclose={() => (drill = null)}
    />
  {/if}
{/snippet}

{#if cardTitle}
  <!-- The card's other charts come first; the monthly bars close it under their own heading. -->
  <section class="card" aria-labelledby="{kind}-card-title">
    <h2 id="{kind}-card-title">{cardTitle}</h2>
    {@render children?.()}
    <div class="bars">
      <div class="row head">
        <h3 id="{kind}-title">{title}</h3>
        {@render splitControl()}
      </div>
      {@render monthlyBars()}
    </div>
  </section>
{:else}
  <section class="card" aria-labelledby="{kind}-title">
    <div class="row head">
      <h2 id="{kind}-title">{title}</h2>
      {@render splitControl()}
    </div>
    {@render monthlyBars()}
    {@render children?.()}
  </section>
{/if}

<style>
  .head {
    justify-content: space-between;
  }
  .bars {
    margin-top: 16px;
    padding-top: 8px;
    border-top: 1px solid var(--line);
  }
  .bars h3 {
    margin: 0;
  }
</style>
