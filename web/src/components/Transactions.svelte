<script lang="ts">
  import {
    EMPTY_FILTERS,
    accountOptions,
    availableMonths,
    filterTransactions,
    filtersFromParams,
    flowTotal,
    paginate,
    type TxFilters,
  } from '../lib/txfilter';
  import { money } from '../lib/aggregate';
  import { monthLabel } from '../lib/format';
  import { route } from '../lib/route';
  import { store } from '../lib/store';
  import TxRow from './TxRow.svelte';

  let filters = $state<TxFilters>(filtersFromParams($route.params));
  let page = $state(0);

  $effect(() => {
    filters.search;
    filters.month;
    filters.from;
    filters.to;
    filters.account;
    filters.category;
    filters.categories;
    filters.excludeRent;
    filters.flow;
    filters.suspectOnly;
    page = 0;
  });

  // A link (a chart click, a banner) sets the filters it names and clears the rest.
  $effect(() => {
    filters = filtersFromParams($route.params);
  });

  const payload = $derived($store.payload);
  const filtered = $derived(payload ? filterTransactions(payload, $store.scope, filters) : []);
  const paged = $derived(paginate(filtered, page));
  const months = $derived(payload ? availableMonths(payload, $store.scope) : []);
  const accounts = $derived(payload ? accountOptions(payload) : []);
  const total = $derived(flowTotal(filtered, filters.flow));
  const categoryNames = $derived(new Map((payload?.categories ?? []).map((c) => [c.category_id, c.name])));
  const rangeLabel = $derived(
    filters.from && filters.to && filters.from === filters.to
      ? monthLabel(filters.from)
      : `${filters.from ? monthLabel(filters.from) : 'start'} – ${filters.to ? monthLabel(filters.to) : 'now'}`,
  );

  function clearAll(): void {
    filters = { ...EMPTY_FILTERS };
  }
</script>

<section class="card" aria-labelledby="tx-title">
  <h2 id="tx-title">Transactions</h2>
  {#if payload}
    <div class="row filters">
      <input type="search" placeholder="Search merchant or name" aria-label="Search" bind:value={filters.search} />
      <select
        aria-label="Month"
        bind:value={filters.month}
        onchange={() => {
          filters.from = '';
          filters.to = '';
        }}
      >
        <option value="">All months</option>
        {#each months as m}<option value={m}>{monthLabel(m)}</option>{/each}
      </select>
      <select aria-label="Account" bind:value={filters.account}>
        <option value="">All accounts</option>
        {#each accounts as a}<option value={a.id}>{a.label}</option>{/each}
      </select>
      <select aria-label="Category" bind:value={filters.category} onchange={() => (filters.categories = [])}>
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

    {#if filters.from || filters.to || filters.categories.length > 0 || filters.excludeRent}
      <div class="row chips">
        {#if filters.from || filters.to}
          <button
            type="button"
            class="chip"
            aria-label="Remove the {rangeLabel} filter"
            onclick={() => {
              filters.from = '';
              filters.to = '';
            }}>{rangeLabel} ×</button
          >
        {/if}
        {#if filters.categories.length > 0}
          <button
            type="button"
            class="chip"
            title={filters.categories.map((c) => categoryNames.get(c) ?? c).join(', ')}
            aria-label="Remove the {filters.categories.length} categories filter"
            onclick={() => (filters.categories = [])}
            >{filters.categories.length} categories ({filters.categories.map((c) => categoryNames.get(c) ?? c).join(', ')}) ×</button
          >
        {/if}
        {#if filters.excludeRent}
          <button
            type="button"
            class="chip"
            aria-label="Remove the excluding rent filter"
            onclick={() => (filters.excludeRent = false)}>Excluding rent ×</button
          >
        {/if}
        <button type="button" class="link" onclick={clearAll}>Clear all filters</button>
      </div>
    {/if}

    <p class="muted">
      {filtered.length} transaction{filtered.length === 1 ? '' : 's'}{#if total !== null}{` · ${
          filters.flow === 'spending' ? 'spending' : 'earnings'
        } total ${money(total)}`}{/if}
    </p>
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
  .chips {
    margin-top: 8px;
  }
  .chip {
    border-radius: 999px;
    padding: 4px 12px;
    background: var(--info-bg);
    border-color: var(--info-line);
  }
  .link {
    background: none;
    border: none;
    color: var(--accent);
    text-decoration: underline;
    padding: 4px;
  }
  .filters > * {
    flex: 1 1 150px;
    min-width: 0;
  }
</style>
