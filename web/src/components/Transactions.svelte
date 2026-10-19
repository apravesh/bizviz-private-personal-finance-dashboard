<script lang="ts">
  import {
    EMPTY_FILTERS,
    accountOptions,
    availableMonths,
    filterTransactions,
    paginate,
    type TxFilters,
  } from '../lib/txfilter';
  import { monthLabel } from '../lib/format';
  import { route } from '../lib/route';
  import { store } from '../lib/store';
  import TxRow from './TxRow.svelte';

  let filters = $state<TxFilters>({ ...EMPTY_FILTERS, suspectOnly: $route.params.get('suspect') === '1' });
  let page = $state(0);

  $effect(() => {
    filters.search;
    filters.month;
    filters.account;
    filters.category;
    filters.flow;
    filters.suspectOnly;
    page = 0;
  });

  $effect(() => {
    filters.suspectOnly = $route.params.get('suspect') === '1';
  });

  const payload = $derived($store.payload);
  const filtered = $derived(payload ? filterTransactions(payload, $store.scope, filters) : []);
  const paged = $derived(paginate(filtered, page));
  const months = $derived(payload ? availableMonths(payload, $store.scope) : []);
  const accounts = $derived(payload ? accountOptions(payload) : []);
</script>

<section class="card" aria-labelledby="tx-title">
  <h2 id="tx-title">Transactions</h2>
  {#if payload}
    <div class="row filters">
      <input type="search" placeholder="Search merchant or name" aria-label="Search" bind:value={filters.search} />
      <select aria-label="Month" bind:value={filters.month}>
        <option value="">All months</option>
        {#each months as m}<option value={m}>{monthLabel(m)}</option>{/each}
      </select>
      <select aria-label="Account" bind:value={filters.account}>
        <option value="">All accounts</option>
        {#each accounts as a}<option value={a.id}>{a.label}</option>{/each}
      </select>
      <select aria-label="Category" bind:value={filters.category}>
        <option value="">All categories</option>
        {#each payload.categories as c}<option value={c.category_id}>{c.name}</option>{/each}
      </select>
      <select aria-label="Flow" bind:value={filters.flow}>
        <option value="">All flows</option>
        <option value="spending">Spending</option>
        <option value="earning">Earning</option>
        <option value="internal">Transfer (excluded)</option>
        <option value="ignored">Ignored</option>
      </select>
      <label class="check">
        <input type="checkbox" bind:checked={filters.suspectOnly} />
        Suspect card payments
      </label>
    </div>

    <p class="muted">{filtered.length} transaction{filtered.length === 1 ? '' : 's'}</p>
    <div class="table-scroll">
      <table>
        <tbody>
          {#each paged.rows as tx (tx.id)}
            <TxRow {tx} {payload} scope={$store.scope} />
          {/each}
        </tbody>
      </table>
    </div>
    {#if paged.pages > 1}
      <div class="row pager">
        <button type="button" disabled={paged.page === 0} onclick={() => (page = paged.page - 1)}>Previous</button>
        <span>Page {paged.page + 1} of {paged.pages}</span>
        <button type="button" disabled={paged.page >= paged.pages - 1} onclick={() => (page = paged.page + 1)}>
          Next
        </button>
      </div>
    {/if}
  {:else}
    <p class="muted">No data yet.</p>
  {/if}
</section>

<style>
  .filters > * {
    flex: 1 1 150px;
    min-width: 0;
  }
</style>
