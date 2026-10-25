<script lang="ts">
  import { store } from '../lib/store';
  import { withInvestments } from '../lib/taxAdvantaged';
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
    }}
  />
  <Lazy {payload} minHeight={420} load={() => import('./NetWorthSection.svelte')} />
{/if}
