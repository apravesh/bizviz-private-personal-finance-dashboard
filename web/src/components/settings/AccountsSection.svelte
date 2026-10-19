<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { apiFetch } from '../../lib/api';
  import { runAction } from '../../lib/actions';
  import { accountLabel } from '../../lib/aggregate';
  import { formatDate } from '../../lib/format';
  import { refreshData, store } from '../../lib/store';
  import type { AccountOut } from '../../lib/types';

  let error = $state('');
  let section: HTMLElement;
  let firstUnreviewed: HTMLSelectElement | undefined = $state();
  let focusedFor = '';

  const accounts = $derived($store.payload?.accounts ?? []);
  const unreviewed = $derived(accounts.filter((a) => !a.reviewed));
  const firstId = $derived(unreviewed[0]?.account_id ?? '');
  const supersededFrom = $derived(
    new Map(($store.payload?.quality.superseded_accounts ?? []).map((s) => [s.account_id, s.from_date])),
  );

  async function focusFirst(): Promise<void> {
    await tick();
    if (firstId && focusedFor !== firstId) {
      focusedFor = firstId;
      section?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
      firstUnreviewed?.focus({ preventScroll: true });
    }
  }

  onMount(focusFirst);
  $effect(() => {
    firstId;
    void focusFirst();
  });

  async function patch(account: AccountOut, body: Record<string, unknown>): Promise<void> {
    error = (await runAction(() => apiFetch(`/api/accounts/${account.account_id}`, { method: 'PATCH', body }))) ?? '';
    await refreshData();
  }
</script>

<section class="card" id="accounts" bind:this={section} aria-labelledby="acct-title">
  <h2 id="acct-title">Accounts</h2>
  {#if unreviewed.length > 0}
    <p class="muted">Choose Mine or Joint for new accounts, then mark them reviewed.</p>
  {/if}
  {#each accounts as account (account.account_id)}
    <div class="account" class:unreviewed={!account.reviewed} data-account={account.account_id}>
      <strong>{accountLabel(account)}</strong>
      <span class="badge">{account.type}</span>
      {#if !account.reviewed}<span class="badge pending">needs review</span>{/if}
      {#if account.gone_at}<span class="badge">closed / no longer reported</span>{/if}
      {#if account.duplicate_of_account}<span class="badge">duplicate (excluded)</span>{/if}
      {#if account.superseded_by}
        <span class="badge">superseded from {formatDate(supersededFrom.get(account.account_id))}</span>
      {/if}
      {#if account.removed}<span class="badge">disconnected</span>{/if}
      <div class="row">
        {#if account.account_id === firstId}
          <select
            aria-label="Ownership of {accountLabel(account)}"
            value={account.ownership}
            bind:this={firstUnreviewed}
            disabled={!$store.online}
            onchange={(e) => patch(account, { ownership: e.currentTarget.value })}
          >
            <option value="mine">Mine</option>
            <option value="joint">Joint</option>
          </select>
        {:else}
          <select
            aria-label="Ownership of {accountLabel(account)}"
            value={account.ownership}
            disabled={!$store.online}
            onchange={(e) => patch(account, { ownership: e.currentTarget.value })}
          >
            <option value="mine">Mine</option>
            <option value="joint">Joint</option>
          </select>
        {/if}
        <label class="check">
          <input
            type="checkbox"
            checked={account.included}
            disabled={!$store.online}
            onchange={(e) => patch(account, { included: e.currentTarget.checked })}
          />
          Included
        </label>
        {#if !account.reviewed}
          <button type="button" class="primary" disabled={!$store.online} onclick={() => patch(account, { reviewed: true })}>
            Mark reviewed
          </button>
        {/if}
      </div>
    </div>
  {:else}
    <p class="muted">No accounts yet.</p>
  {/each}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .account {
    padding: 8px;
    border-bottom: 1px solid var(--line);
  }
  .account.unreviewed {
    background: var(--hilite);
    border: 2px solid var(--warn-line);
    border-radius: 8px;
    margin-bottom: 6px;
  }
</style>
