<script lang="ts">
  import { apiFetch } from '../../lib/api';
  import { runAction } from '../../lib/actions';
  import { confirmDialog } from '../../lib/dialog';
  import { FLOW_LABELS, type FlowChoice } from '../../lib/editor';
  import { refreshData, store } from '../../lib/store';
  import type { RuleOut } from '../../lib/types';

  interface Draft {
    rule_id: number;
    merchant_key: string;
    category: string;
    flow: FlowChoice;
    recurring: 'auto' | 'yes' | 'no';
  }

  let error = $state('');
  let draft = $state<Draft | null>(null);

  const rules = $derived($store.payload?.rules ?? []);
  const categories = $derived($store.payload?.categories ?? []);
  const merchantNames = $derived(
    new Map(($store.payload?.transactions ?? []).map((t) => [t.merchant_key, t.merchant_display])),
  );

  const categoryName = (id: string | null) => categories.find((c) => c.category_id === id)?.name ?? id ?? 'Auto';
  const recurringText = (r: boolean | null) => (r === null ? 'Auto' : r ? 'Yes' : 'No');

  function edit(rule: RuleOut): void {
    draft = {
      rule_id: rule.rule_id,
      merchant_key: rule.merchant_key,
      category: rule.category_id ?? 'auto',
      flow: (rule.flow_class as FlowChoice | null) ?? 'auto',
      recurring: rule.recurring === null ? 'auto' : rule.recurring ? 'yes' : 'no',
    };
  }

  async function act(action: () => Promise<unknown>): Promise<boolean> {
    error = (await runAction(action)) ?? '';
    await refreshData();
    return !error;
  }

  async function save(): Promise<void> {
    if (!draft) return;
    const d = draft;
    const ok = await act(() =>
      apiFetch(`/api/rules/${d.rule_id}`, {
        method: 'PATCH',
        body: {
          merchant_key: d.merchant_key,
          category_id: d.category === 'auto' ? null : d.category,
          flow_class: d.flow === 'auto' ? null : d.flow,
          recurring: d.recurring === 'auto' ? null : d.recurring === 'yes',
        },
      }),
    );
    if (ok) draft = null;
  }

  async function remove(rule: RuleOut): Promise<void> {
    const name = merchantNames.get(rule.merchant_key) ?? rule.merchant_key;
    if (await confirmDialog(`Delete the rule for ${name}? Past transactions are reclassified.`, 'Delete')) {
      await act(() => apiFetch(`/api/rules/${rule.rule_id}`, { method: 'DELETE' }));
    }
  }
</script>

<section class="card" aria-labelledby="rules-title">
  <h2 id="rules-title">Merchant rules</h2>
  {#if rules.length === 0}
    <p class="muted">No merchant rules yet. Use "Apply to all transactions from…" when editing a transaction.</p>
  {/if}
  {#each rules as rule (rule.rule_id)}
    <div class="rule">
      <strong>{merchantNames.get(rule.merchant_key) ?? rule.merchant_key}</strong>
      {#if draft?.rule_id === rule.rule_id}
        <div class="row">
          <select aria-label="Rule flow" bind:value={draft.flow}>
            {#each Object.entries(FLOW_LABELS) as [value, label]}<option {value}>{label}</option>{/each}
          </select>
          <select aria-label="Rule category" bind:value={draft.category}>
            <option value="auto">Auto</option>
            {#each categories as c}<option value={c.category_id}>{c.name}</option>{/each}
          </select>
          <select aria-label="Rule recurring" bind:value={draft.recurring}>
            <option value="auto">Auto</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
          <button type="button" class="primary" onclick={save}>Save</button>
          <button type="button" onclick={() => (draft = null)}>Cancel</button>
        </div>
      {:else}
        <div class="muted">
          Category: {categoryName(rule.category_id)} · Type: {rule.flow_class ? FLOW_LABELS[rule.flow_class as FlowChoice] ?? rule.flow_class : 'Auto'} · Recurring: {recurringText(rule.recurring)}
        </div>
        <div class="row">
          <button type="button" disabled={!$store.online} onclick={() => edit(rule)}>Edit</button>
          <button type="button" class="danger" disabled={!$store.online} onclick={() => remove(rule)}>Delete</button>
        </div>
      {/if}
    </div>
  {/each}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .rule {
    padding: 8px 0;
    border-bottom: 1px solid var(--line);
  }
</style>
