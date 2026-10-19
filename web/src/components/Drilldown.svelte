<script lang="ts">
  import { seriesTransactions, money, type SpendingSplit, type EarningsSplit } from '../lib/aggregate';
  import { monthLabel } from '../lib/format';
  import type { DataPayload, Scope } from '../lib/types';
  import Modal from './Modal.svelte';
  import TxRow from './TxRow.svelte';

  interface Props {
    payload: DataPayload;
    scope: Scope;
    flow: 'spending' | 'earning';
    split: SpendingSplit | EarningsSplit;
    seriesKey: string;
    seriesLabel: string;
    month: string;
    onclose: () => void;
  }

  let { payload, scope, flow, split, seriesKey, seriesLabel, month, onclose }: Props = $props();

  const transactions = $derived(seriesTransactions(payload, scope, flow, split, new Date(), seriesKey, month));
  const total = $derived(transactions.reduce((sum, tx) => sum + (flow === 'spending' ? tx.amount : -tx.amount), 0));
</script>

<Modal title="{seriesLabel} · {monthLabel(month)}" {onclose}>
  <p class="muted">
    {transactions.length} transaction{transactions.length === 1 ? '' : 's'}, {money(total)}
  </p>
  <div class="table-scroll">
    <table>
      <tbody>
        {#each transactions as tx (tx.id)}
          <TxRow {tx} {payload} {scope} />
        {/each}
      </tbody>
    </table>
  </div>
</Modal>
