<script lang="ts">
  import { store } from '../lib/store';
  import Lazy from './Lazy.svelte';
</script>

{#if $store.payload}
  {@const payload = $store.payload}
  <Lazy {payload} minHeight={420} load={() => import('./InvestmentsSection.svelte')} />
  <Lazy {payload} minHeight={420} load={() => import('./NetWorthSection.svelte')} />
{/if}
