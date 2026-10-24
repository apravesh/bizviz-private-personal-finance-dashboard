<script lang="ts">
  import { currentMonth, memoized, monthlySavings } from '../lib/aggregate';
  import { savingsOption } from '../lib/chartOptions';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';

  let { payload }: { payload: DataPayload } = $props();

  const today = new Date();
  const result = $derived(
    memoized(['savings', payload.data_version, $store.scope, currentMonth(today)], () =>
      monthlySavings(payload, $store.scope, today),
    ),
  );
  const option = $derived(savingsOption(result));
</script>

<section class="card" aria-labelledby="savings-title">
  <h2 id="savings-title">Savings per month</h2>
  {#if payload.transactions.length === 0}
    <p class="muted">No transactions yet.</p>
  {/if}
  <Chart {option} label="Savings per month" />
  <p class="muted">
    Earnings minus spending, counted the same way as the charts below. Bars below zero are months where
    spending was higher than earnings. Transfers between your own accounts are not counted, so money moved
    into savings or investment accounts is not spending. Pending transactions are included.
  </p>
</section>
