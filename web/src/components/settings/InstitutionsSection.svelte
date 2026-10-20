<script lang="ts">
  import { ApiError, apiFetch } from '../../lib/api';
  import { errorMessage, runAction } from '../../lib/actions';
  import { confirmDialog } from '../../lib/dialog';
  import { formatDateTime } from '../../lib/format';
  import { isLocalhost, openLink, type LinkMode } from '../../lib/plaid';
  import { refreshData, store } from '../../lib/store';

  const SLOT_LIMIT = 10;
  let error = $state('');

  const items = $derived($store.payload?.status.items ?? []);
  const used = $derived($store.system?.plaid_items_created_count ?? items.length);
  const atLimit = $derived(used >= SLOT_LIMIT);
  const local = isLocalhost();

  const buttons: { mode: LinkMode; label: string }[] = [
    { mode: 'bank', label: 'Link bank or credit card' },
    { mode: 'investment', label: 'Link brokerage or retirement account' },
    { mode: 'loan', label: 'Link loan or mortgage' },
  ];

  function linkError(e: unknown): string {
    if (e instanceof ApiError && e.code === 'slot_limit') return 'All 10 Plaid Trial connections are used.';
    if (e instanceof ApiError && e.code === 'plaid_not_configured') return 'Plaid is not configured on the Mac yet.';
    return errorMessage(e);
  }

  async function link(mode: LinkMode, itemId?: string): Promise<void> {
    error = '';
    try {
      await openLink({ mode, itemId, items });
    } catch (e) {
      error = linkError(e);
    }
  }

  async function disconnect(itemId: string): Promise<void> {
    const ok = await confirmDialog(
      'Disconnecting does not free a Plaid slot; this connection will stop updating. Its history stays.',
      'Disconnect',
    );
    if (!ok) return;
    error = (await runAction(() => apiFetch(`/api/plaid/items/${itemId}`, { method: 'DELETE' }))) ?? '';
    await refreshData();
  }
</script>

<section class="card" aria-labelledby="inst-title">
  <h2 id="inst-title">Institutions</h2>
  {#if items.length === 0}
    <p class="muted">No institutions linked.</p>
  {:else}
    <ul class="plain">
      {#each items as item (item.item_id)}
        <li>
          <strong>{item.institution_name ?? 'Unknown institution'}</strong>
          <span class="badge">{item.removed ? 'disconnected' : item.status}</span>
          <div class="muted">Last sync: {formatDateTime(item.last_success_at)}</div>
          {#if local && !item.removed}
            <div class="row">
              <button type="button" onclick={() => link('update', item.item_id)}>Fix</button>
              <button type="button" class="danger" disabled={!$store.online} onclick={() => disconnect(item.item_id)}>
                Disconnect
              </button>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  <p>Plaid Trial connections used: {used} of {SLOT_LIMIT}. Removing a connection does not free a slot.</p>
  {#if local}
    <div class="row">
      {#each buttons as b}
        <button type="button" disabled={atLimit || !$store.online} onclick={() => link(b.mode)}>{b.label}</button>
      {/each}
    </div>
    {#if atLimit}<p class="muted">All {SLOT_LIMIT} connections are used, so linking is disabled.</p>{/if}
  {:else}
    <p class="muted">Link accounts from the Mac's browser.</p>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .plain {
    list-style: none;
    padding: 0;
  }
  li {
    padding: 8px 0;
    border-bottom: 1px solid var(--line);
  }
</style>
