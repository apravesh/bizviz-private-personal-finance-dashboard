<script lang="ts">
  import { accountLabel, money, scopeClass } from '../lib/aggregate';
  import { editingTx } from '../lib/editor';
  import { formatDate } from '../lib/format';
  import type { DataPayload, Scope, TransactionOut } from '../lib/types';

  interface Props {
    tx: TransactionOut;
    payload: DataPayload;
    scope: Scope;
  }

  let { tx, payload, scope }: Props = $props();

  const account = $derived(payload.accounts.find((a) => a.account_id === tx.account_id));
  const cls = $derived(scopeClass(tx, scope));
  const category = $derived(
    cls ? (payload.categories.find((c) => c.category_id === cls.category_id)?.name ?? cls.category_id) : '—',
  );
  const shown = $derived(-tx.amount);
</script>

<tr>
  <td>{formatDate(tx.date)}</td>
  <td>
    <button type="button" class="link" onclick={() => editingTx.set(tx)}>
      {tx.merchant_display ?? tx.name ?? 'Unknown'}
    </button>
    {#if tx.pending}<span class="badge pending">pending</span>{/if}
    <div class="muted">{accountLabel(account, tx.account_id)} · {category}{cls && cls.flow !== 'spending' && cls.flow !== 'earning' ? ` · ${cls.flow === 'internal' ? 'transfer' : cls.flow}` : ''}</div>
  </td>
  <td class="num {shown < 0 ? 'out' : 'in'}">{money(shown)}</td>
</tr>

<style>
  button.link {
    border: 0;
    background: none;
    padding: 0;
    min-height: 44px;
    color: var(--accent);
    text-align: left;
    text-decoration: underline;
  }
</style>
