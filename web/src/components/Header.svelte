<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '../lib/api';
  import { errorMessage } from '../lib/actions';
  import { relativeTime } from '../lib/format';
  import { route } from '../lib/route';
  import { poll, setScope, store } from '../lib/store';

  let now = $state(Date.now());
  let syncing = $state(false);
  let syncError = $state('');

  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 30_000);
    return () => clearInterval(timer);
  });

  async function syncNow(): Promise<void> {
    syncError = '';
    syncing = true;
    try {
      await apiFetch('/api/sync', { method: 'POST' });
      for (let i = 0; i < 40; i++) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        await poll();
        if (!$store.syncing || !$store.online) break;
      }
    } catch (error) {
      syncError = errorMessage(error);
    } finally {
      syncing = false;
    }
  }

  const links = [
    { href: '#/', name: 'dashboard', label: 'Earnings & spending' },
    { href: '#/investments', name: 'investments', label: 'Investments' },
    { href: '#/transactions', name: 'transactions', label: 'Transactions' },
    { href: '#/settings', name: 'settings', label: 'Settings' },
  ] as const;
</script>

<header class="top">
  <div class="row">
    <strong class="brand">BizViz</strong>
    <div class="seg" role="group" aria-label="Scope">
      <button type="button" aria-pressed={$store.scope === 'mine'} onclick={() => setScope('mine')}>Mine</button>
      <button type="button" aria-pressed={$store.scope === 'household'} onclick={() => setScope('household')}>
        Household
      </button>
    </div>
    <span class="muted">Updated {relativeTime($store.payload?.status.newest_data_at, now)}</span>
    <button type="button" onclick={syncNow} disabled={!$store.online || syncing || $store.syncing}>
      {syncing || $store.syncing ? 'Syncing…' : 'Sync now'}
    </button>
  </div>
  {#if syncError}<p class="error" role="alert">{syncError}</p>{/if}
  <nav class="row" aria-label="Pages">
    {#each links as link}
      <a href={link.href} aria-current={$route.name === link.name ? 'page' : undefined}>{link.label}</a>
    {/each}
  </nav>
</header>

<style>
  .top {
    position: sticky;
    top: 0;
    z-index: 5;
    background: var(--card);
    border-bottom: 1px solid var(--line);
    padding: 8px 12px;
  }
  .brand {
    font-size: 1.1rem;
  }
  nav {
    margin-top: 4px;
  }
  nav a {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 8px;
    text-decoration: none;
  }
  nav a[aria-current='page'] {
    font-weight: 700;
    border-bottom: 3px solid var(--accent);
  }
</style>
