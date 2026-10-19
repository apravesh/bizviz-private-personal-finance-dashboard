<script lang="ts">
  import { apiFetch } from '../../lib/api';
  import { errorMessage } from '../../lib/actions';
  import { store } from '../../lib/store';

  const MIN_LENGTH = 10;
  let current = $state('');
  let next = $state('');
  let confirm = $state('');
  let message = $state('');
  let error = $state('');
  let busy = $state(false);

  const problem = $derived(
    next && next.length < MIN_LENGTH
      ? `The new password needs at least ${MIN_LENGTH} characters.`
      : confirm && confirm !== next
        ? 'The new passwords do not match.'
        : '',
  );

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    message = '';
    error = '';
    busy = true;
    try {
      await apiFetch('/api/auth/password', {
        method: 'POST',
        body: { current_password: current, new_password: next },
      });
      current = next = confirm = '';
      message = 'Password changed. Other devices were logged out.';
    } catch (e) {
      error = errorMessage(e);
    } finally {
      busy = false;
    }
  }
</script>

<section class="card" aria-labelledby="pw-title">
  <h2 id="pw-title">Change password</h2>
  <form onsubmit={submit}>
    <p>
      <label for="pw-current">Current password</label><br />
      <input id="pw-current" type="password" autocomplete="current-password" bind:value={current} />
    </p>
    <p>
      <label for="pw-new">New password</label><br />
      <input id="pw-new" type="password" autocomplete="new-password" bind:value={next} />
    </p>
    <p>
      <label for="pw-confirm">Repeat new password</label><br />
      <input id="pw-confirm" type="password" autocomplete="new-password" bind:value={confirm} />
    </p>
    {#if problem}<p class="muted">{problem}</p>{/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if message}<p role="status">{message}</p>{/if}
    <button
      type="submit"
      class="primary"
      disabled={busy || !$store.online || !current || next.length < MIN_LENGTH || next !== confirm}
    >
      Change password
    </button>
  </form>
</section>
