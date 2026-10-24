<script lang="ts">
  import { onMount } from 'svelte';
  import { retryLeftoverExchange } from './lib/plaid';
  import { route } from './lib/route';
  import { start, store } from './lib/store';
  import Banners from './components/Banners.svelte';
  import ConfirmDialog from './components/ConfirmDialog.svelte';
  import Dashboard from './components/Dashboard.svelte';
  import Header from './components/Header.svelte';
  import Investments from './components/Investments.svelte';
  import Login from './components/Login.svelte';
  import Settings from './components/Settings.svelte';
  import TransactionEditorHost from './components/TransactionEditorHost.svelte';
  import Transactions from './components/Transactions.svelte';

  onMount(() => {
    void start().then(() => {
      if (!$store.needsLogin) void retryLeftoverExchange();
    });
  });
</script>

{#if $store.needsLogin}
  <Login />
{:else if !$store.loaded}
  <main class="page"><p class="muted">Loading…</p></main>
{:else}
  <Header />
  <Banners />
  <main class="page">
    {#if !$store.payload}
      <p class="muted">
        {$store.online ? 'Loading your data…' : 'No data is saved on this device yet, and the Mac is unreachable.'}
      </p>
    {:else if $route.name === 'investments'}
      <Investments />
    {:else if $route.name === 'transactions'}
      <Transactions />
    {:else if $route.name === 'settings'}
      <Settings />
    {:else}
      <Dashboard />
    {/if}
  </main>
  <TransactionEditorHost />
{/if}
<ConfirmDialog />
