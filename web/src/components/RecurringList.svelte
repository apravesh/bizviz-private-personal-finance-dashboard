<script lang="ts">
  import { money, recurringList } from '../lib/aggregate';
  import { formatDate } from '../lib/format';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';

  let { payload }: { payload: DataPayload } = $props();

  const groups = $derived(
    [...recurringList(payload, $store.scope)].sort(
      (a, b) => Number(b.active) - Number(a.active) || (b.last_date ?? '').localeCompare(a.last_date ?? ''),
    ),
  );
</script>

<section class="card" aria-labelledby="recurring-title">
  <h2 id="recurring-title">Recurring charges</h2>
  {#if groups.length === 0}
    <p class="muted">No recurring charges detected.</p>
  {:else}
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Merchant</th><th>Cadence</th><th class="num">Typical amount</th><th>Last charge</th></tr>
        </thead>
        <tbody>
          {#each groups as group (group.merchant_key)}
            <tr class:inactive={!group.active}>
              <td>
                {group.merchant_display ?? group.merchant_key}
                {#if !group.active}<span class="badge">inactive</span>{/if}
              </td>
              <td>{group.cadence}</td>
              <td class="num">{group.typical_amount === null ? '—' : money(group.typical_amount)}</td>
              <td>{formatDate(group.last_date)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<style>
  .inactive {
    color: var(--muted);
  }
</style>
