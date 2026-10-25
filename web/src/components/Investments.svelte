<script lang="ts">
  import { store } from '../lib/store';
  import { withInvestments } from '../lib/taxAdvantaged';
  import HoldingsSection from './HoldingsSection.svelte';
  import Lazy from './Lazy.svelte';
</script>

{#if $store.payload}
  {@const payload = $store.payload}
  <Lazy
    payload={withInvestments(payload, false)}
    minHeight={420}
    load={() => import('./InvestmentsSection.svelte')}
    props={{
      emptyText: 'No taxable investment accounts in this view. Retirement and other tax-advantaged accounts are on the Retirement tab.',
      showHoldings: false,
    }}
  />
  <Lazy {payload} minHeight={420} load={() => import('./NetWorthSection.svelte')} />
  <!-- Charts first; the per-account holdings tables go last. -->
  <HoldingsSection payload={withInvestments(payload, false)} />
{/if}
