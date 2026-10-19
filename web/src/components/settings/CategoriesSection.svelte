<script lang="ts">
  import { apiFetch } from '../../lib/api';
  import { runAction } from '../../lib/actions';
  import { refreshData, store } from '../../lib/store';
  import type { CategoryOut } from '../../lib/types';

  let name = $state('');
  let appliesTo = $state('spending');
  let error = $state('');
  let renaming = $state<{ id: string; name: string } | null>(null);

  const categories = $derived($store.payload?.categories ?? []);

  async function act(action: () => Promise<unknown>): Promise<void> {
    error = (await runAction(action)) ?? '';
    await refreshData();
  }

  async function add(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!name.trim()) return;
    await act(() => apiFetch('/api/categories', { method: 'POST', body: { name: name.trim(), applies_to: appliesTo } }));
    if (!error) name = '';
  }

  async function rename(): Promise<void> {
    if (!renaming?.name.trim()) return;
    const { id, name: next } = renaming;
    await act(() => apiFetch(`/api/categories/${id}`, { method: 'PATCH', body: { name: next.trim() } }));
    if (!error) renaming = null;
  }

  const toggleHidden = (c: CategoryOut) =>
    act(() => apiFetch(`/api/categories/${c.category_id}`, { method: 'PATCH', body: { hidden: !c.hidden } }));
</script>

<section class="card" aria-labelledby="cat-title">
  <h2 id="cat-title">Categories</h2>
  <form class="row" onsubmit={add}>
    <input type="text" placeholder="New category name" aria-label="New category name" bind:value={name} />
    <select aria-label="Applies to" bind:value={appliesTo}>
      <option value="spending">Spending</option>
      <option value="earning">Earning</option>
      <option value="both">Both</option>
    </select>
    <button type="submit" class="primary" disabled={!$store.online || !name.trim()}>Add category</button>
  </form>
  <ul class="plain">
    {#each categories as category (category.category_id)}
      <li class="row">
        {#if renaming?.id === category.category_id}
          <input type="text" aria-label="Rename category" bind:value={renaming.name} />
          <button type="button" class="primary" onclick={rename}>Save</button>
          <button type="button" onclick={() => (renaming = null)}>Cancel</button>
        {:else}
          <span class:hidden={category.hidden}>{category.name}</span>
          <span class="badge">{category.applies_to}</span>
          {#if category.hidden}<span class="badge">hidden</span>{/if}
          {#if category.kind === 'custom'}
            <button
              type="button"
              disabled={!$store.online}
              onclick={() => (renaming = { id: category.category_id, name: category.name })}>Rename</button
            >
          {/if}
          <button type="button" disabled={!$store.online} onclick={() => toggleHidden(category)}>
            {category.hidden ? 'Unhide' : 'Hide'}
          </button>
        {/if}
      </li>
    {/each}
  </ul>
  <p class="muted">Hidden categories are removed from the pickers only; charts still include their transactions.</p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .plain {
    list-style: none;
    padding: 0;
  }
  li {
    padding: 4px 0;
    border-bottom: 1px solid var(--line);
  }
  .hidden {
    color: var(--muted);
  }
</style>
