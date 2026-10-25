<script lang="ts">
  import { investmentSeries, memoized } from '../lib/aggregate';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import HoldingsList from './HoldingsList.svelte';

  let { payload }: { payload: DataPayload } = $props();

  // Same accounts, labels and Mine/Household filter as the investments chart above.
  const accounts = $derived(
    memoized(['holdings-list', payload.data_version, $store.scope], () =>
      investmentSeries(payload, $store.scope, 'daily'),
    ),
  );
</script>

{#if accounts.length > 0}
  <section class="card" aria-labelledby="holdings-title">
    <h2 id="holdings-title">Holdings</h2>
    <HoldingsList {payload} {accounts} />
  </section>
{/if}
