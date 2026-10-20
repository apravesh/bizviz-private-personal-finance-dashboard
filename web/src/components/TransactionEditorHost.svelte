<script lang="ts">
  import { editingTx } from '../lib/editor';
  import TransactionEditor from './TransactionEditor.svelte';
</script>

{#if $editingTx}
  {#key $editingTx.id}
    <TransactionEditor tx={$editingTx} />
  {/key}
{/if}
