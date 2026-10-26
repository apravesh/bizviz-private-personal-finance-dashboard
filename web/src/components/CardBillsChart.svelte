<script lang="ts">
  import { cardBillFilters, currentMonth, memoized, monthlyCardBills } from '../lib/aggregate';
  import { barOption } from '../lib/chartOptions';
  import { store } from '../lib/store';
  import { transactionsHref } from '../lib/txfilter';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';

  let { payload }: { payload: DataPayload } = $props();

  const today = new Date();
  const result = $derived(
    memoized(['card-bills', payload.data_version, $store.scope, currentMonth(today)], () =>
      monthlyCardBills(payload, $store.scope, today),
    ),
  );
  const option = $derived(barOption(result, 'Card bills', true));

  function openSegment(params: { seriesId?: string; name?: string }): void {
    if (params.seriesId && params.name) location.hash = transactionsHref(cardBillFilters(params.seriesId, params.name));
  }
</script>

<div class="bills">
  <h3 id="card-bills-title">Credit card bills per month</h3>
  {#if result.series.length === 0}
    <p class="muted">No credit card spending or card payments in this view.</p>
  {:else}
    <Chart {option} label="Credit card bills by month" onclick={openSegment} />
    <p class="muted">
      Purchases on each linked card (refunds reduce them), plus payments to cards that aren't linked, where the
      payment is the only record of that spending. Payments to linked cards aren't added again, since their
      purchases are already counted. Click a bar to see its transactions.
    </p>
  {/if}
</div>

<style>
  .bills {
    margin-top: 16px;
    padding-top: 8px;
    border-top: 1px solid var(--line);
  }
  h3 {
    margin: 0;
  }
  /* First in its card: no divider under the card heading. */
  :global(h2) + .bills {
    margin-top: 8px;
    padding-top: 0;
    border-top: none;
  }
</style>
