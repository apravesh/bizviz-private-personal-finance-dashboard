<script lang="ts">
  import { confirmRequest } from '../lib/dialog';
  import Modal from './Modal.svelte';
</script>

{#if $confirmRequest}
  {@const request = $confirmRequest}
  <Modal title="Please confirm" layer={30} onclose={() => request.resolve(false)}>
    <p>{request.message}</p>
    <div class="row">
      <button type="button" onclick={() => request.resolve(false)}>Cancel</button>
      <button type="button" class="primary" onclick={() => request.resolve(true)}>{request.confirmLabel}</button>
    </div>
  </Modal>
{/if}
