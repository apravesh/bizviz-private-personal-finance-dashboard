<script lang="ts">
  import { formatDate } from '../../lib/format';
  import { store } from '../../lib/store';

  const system = $derived($store.system);
  const flag = (v: boolean | null | undefined) => (v === null || v === undefined ? 'unknown' : v ? 'on' : 'off');
</script>

<section class="card" aria-labelledby="sec-title">
  <h2 id="sec-title">Security status</h2>
  {#if system}
    <dl>
      <div><dt>FileVault</dt><dd>{flag(system.filevault)}</dd></div>
      <div><dt>Time Machine</dt><dd>{flag(system.time_machine)}</dd></div>
      <div><dt>HTTPS certificate expires</dt><dd>{formatDate(system.cert_expires_at)}</dd></div>
      <div><dt>Local CA expires</dt><dd>{formatDate(system.ca_expires_at)}</dd></div>
      <div>
        <dt>Hostname</dt>
        <dd>
          {system.current_hostname ?? system.hostname ?? 'unknown'}
          {#if system.hostname_mismatch}(certificate was issued for {system.hostname}){/if}
        </dd>
      </div>
      <div><dt>Plaid environment</dt><dd>{system.plaid_env ?? 'not configured'}</dd></div>
    </dl>
  {:else}
    <p class="muted">Security status is not available (the Mac is unreachable or has not answered yet).</p>
  {/if}
</section>

<style>
  dl div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 0;
    border-bottom: 1px solid var(--line);
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
    text-align: right;
  }
</style>
