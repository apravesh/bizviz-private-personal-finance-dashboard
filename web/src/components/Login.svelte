<script lang="ts">
  import { ApiError, NetworkError, apiFetch } from '../lib/api';
  import { deviceNameFromUserAgent } from '../lib/format';
  import { retryLeftoverExchange } from '../lib/plaid';
  import { start } from '../lib/store';

  let password = $state('');
  let deviceName = $state(deviceNameFromUserAgent(navigator.userAgent));
  let error = $state('');
  let busy = $state(false);

  function describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 401) return 'That password is not correct.';
    if (e instanceof ApiError && e.status === 429) {
      const seconds = Number(e.retryAfter);
      const wait = Number.isFinite(seconds) && seconds > 0 ? ` Try again in ${Math.ceil(seconds / 60)} min.` : ' Try again later.';
      return `Too many failed attempts.${wait}`;
    }
    if (e instanceof NetworkError) return 'The Mac is unreachable.';
    return e instanceof Error ? e.message : 'Login failed.';
  }

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      await apiFetch('/api/auth/login', {
        method: 'POST',
        body: { password, device_name: deviceName.trim() || 'Unnamed device' },
      });
      password = '';
      await start();
      void retryLeftoverExchange();
    } catch (e) {
      error = describe(e);
    } finally {
      busy = false;
    }
  }
</script>

<main class="page login">
  <form class="card" onsubmit={submit}>
    <h1>BizViz</h1>
    <p class="muted">Log in to see your data.</p>
    <p>
      <label for="login-password">Password</label><br />
      <input id="login-password" type="password" bind:value={password} autocomplete="current-password" required />
    </p>
    <p>
      <label for="login-device">Device name</label><br />
      <input id="login-device" type="text" bind:value={deviceName} />
    </p>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <button class="primary" type="submit" disabled={busy || !password}>{busy ? 'Logging in…' : 'Log in'}</button>
  </form>
</main>

<style>
  .login {
    max-width: 420px;
  }
  input {
    width: 100%;
  }
</style>
