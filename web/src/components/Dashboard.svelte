<script lang="ts">
  import { store } from '../lib/store';
  import EarningsChart from './EarningsChart.svelte';
  import Lazy from './Lazy.svelte';
  import RecurringList from './RecurringList.svelte';
  import SpendingChart from './SpendingChart.svelte';
</script>

{#if $store.payload}
  {@const payload = $store.payload}
  <EarningsChart {payload} />
  <SpendingChart {payload} />
  <RecurringList {payload} />
  <hr class="divider" />
  <Lazy {payload} minHeight={420} load={() => import('./InvestmentsSection.svelte')} />
  <Lazy {payload} minHeight={420} load={() => import('./NetWorthSection.svelte')} />
{/if}
