<script lang="ts">
  import { store } from '../lib/store';
  import EarningsChart from './EarningsChart.svelte';
  import RecurringList from './RecurringList.svelte';
  import SavingsChart from './SavingsChart.svelte';
  import SpendingChart from './SpendingChart.svelte';
</script>

{#if $store.payload}
  {@const payload = $store.payload}
  <SavingsChart {payload} />
  <EarningsChart {payload} />
  <SpendingChart {payload} />
  <RecurringList {payload} />
{/if}
