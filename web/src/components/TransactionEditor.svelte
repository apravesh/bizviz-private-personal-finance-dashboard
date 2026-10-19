<script lang="ts">
  import { apiFetch } from '../lib/api';
  import { errorMessage } from '../lib/actions';
  import { scopeClass } from '../lib/aggregate';
  import {
    FLOW_LABELS,
    applyBody,
    categoryChoices,
    editingTx,
    effectiveFlow,
    formFromTransaction,
    overrideBody,
    type EditorForm,
    type FlowChoice,
  } from '../lib/editor';
  import { afterEdit, store } from '../lib/store';
  import type { TransactionOut } from '../lib/types';
  import Modal from './Modal.svelte';

  let { tx }: { tx: TransactionOut } = $props();

  // The editor is mounted per transaction, so the initial snapshot is what we want.
  // svelte-ignore state_referenced_locally
  let form = $state<EditorForm>(formFromTransaction(tx));
  let applyToMerchant = $state(false);
  let busy = $state(false);
  let error = $state('');

  const payload = $derived($store.payload);
  const offline = $derived(!$store.online);
  const currentFlow = $derived(scopeClass(tx, $store.scope)?.flow);
  const flow = $derived(effectiveFlow(form, currentFlow));
  const categories = $derived(categoryChoices(payload?.categories ?? [], flow, form.category));
  const merchant = $derived(tx.merchant_display ?? tx.name ?? 'this merchant');
  const canApply = $derived(!tx.pending && tx.merchant_key !== null);
  const applyFields = $derived(applyBody(form));
  const nothingToApply = $derived(applyToMerchant && Object.keys(applyFields).length === 0);

  $effect(() => {
    if (form.category !== 'auto' && !categories.some((c) => c.category_id === form.category)) {
      form.category = 'auto';
    }
  });

  function close(): void {
    editingTx.set(null);
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      let result: { data_version?: number };
      if (applyToMerchant) {
        result = await apiFetch(`/api/transactions/${encodeURIComponent(tx.id)}/apply-to-merchant`, {
          method: 'POST',
          body: applyFields,
        });
      } else {
        result = await apiFetch(`/api/transactions/${encodeURIComponent(tx.id)}/override`, {
          method: 'PATCH',
          body: overrideBody(form),
        });
      }
      await afterEdit(result?.data_version);
      close();
    } catch (e) {
      error = errorMessage(e);
    } finally {
      busy = false;
    }
  }
</script>

<Modal title="Edit transaction" layer={20} onclose={close}>
  <p>
    <strong>{merchant}</strong>
    <span class="muted"> · {tx.date}{tx.pending ? ' · pending' : ''}</span>
  </p>
  {#if offline}
    <p class="warn" role="status">Editing needs the Mac to be reachable. It is unreachable right now.</p>
  {/if}
  <form onsubmit={save}>
    <fieldset disabled={offline || busy}>
      <p>
        <label for="edit-flow">Type</label><br />
        <select id="edit-flow" bind:value={form.flow}>
          {#each Object.entries(FLOW_LABELS) as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
      </p>
      <p>
        <label for="edit-category">Category</label><br />
        <select id="edit-category" bind:value={form.category}>
          <option value="auto">Auto</option>
          {#each categories as category (category.category_id)}
            <option value={category.category_id}>{category.name}{category.hidden ? ' (hidden)' : ''}</option>
          {/each}
        </select>
      </p>
      <p>
        <label for="edit-recurring">Recurring</label><br />
        <select id="edit-recurring" bind:value={form.recurring}>
          <option value="auto">Auto</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </p>
      <p>
        <label class="check">
          <input type="checkbox" bind:checked={applyToMerchant} disabled={!canApply} />
          Apply to all transactions from {merchant}
        </label>
        {#if tx.pending}<br /><span class="muted">available once posted</span>{/if}
        {#if nothingToApply}<br /><span class="muted">Choose a type, category or recurring value to apply.</span>{/if}
      </p>
    </fieldset>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="row">
      <button type="button" onclick={close}>Cancel</button>
      <button type="submit" class="primary" disabled={offline || busy || nothingToApply}>
        {busy ? 'Saving…' : 'Save'}
      </button>
    </div>
  </form>
</Modal>

<style>
  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
  }
  select {
    width: 100%;
  }
  .warn {
    background: var(--warn-bg);
    padding: 8px;
    border-radius: 8px;
  }
</style>
