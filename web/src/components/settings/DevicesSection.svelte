<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '../../lib/api';
  import { errorMessage, runAction } from '../../lib/actions';
  import { confirmDialog } from '../../lib/dialog';
  import { formatDateTime } from '../../lib/format';
  import { store } from '../../lib/store';

  interface Device {
    session_id: string;
    device_name: string;
    user_agent: string | null;
    created_at: string;
    last_seen_at: string | null;
    current: boolean;
  }

  let devices = $state<Device[]>([]);
  let error = $state('');

  async function load(): Promise<void> {
    try {
      devices = await apiFetch<Device[]>('/api/devices');
    } catch (e) {
      error = errorMessage(e);
    }
  }

  onMount(load);

  async function revoke(device: Device): Promise<void> {
    if (!(await confirmDialog(`Revoke ${device.device_name}? It will be logged out and its cached data deleted.`, 'Revoke'))) return;
    error = (await runAction(() => apiFetch(`/api/devices/${device.session_id}`, { method: 'DELETE' }))) ?? '';
    await load();
  }
</script>

<section class="card" aria-labelledby="dev-title">
  <h2 id="dev-title">Devices</h2>
  {#each devices as device (device.session_id)}
    <div class="device">
      <strong>{device.device_name}</strong>
      {#if device.current}<span class="badge">this device</span>{/if}
      <div class="muted">Logged in {formatDateTime(device.created_at)} · last seen {formatDateTime(device.last_seen_at)}</div>
      {#if !device.current}
        <button type="button" class="danger" disabled={!$store.online} onclick={() => revoke(device)}>Revoke</button>
      {/if}
    </div>
  {:else}
    <p class="muted">No devices to show.</p>
  {/each}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .device {
    padding: 8px 0;
    border-bottom: 1px solid var(--line);
  }
</style>
