<script lang="ts">
  import { investmentSeries, memoized, money, type Granularity } from '../lib/aggregate';
  import { investmentOption } from '../lib/chartOptions';
  import { formatDate } from '../lib/format';
  import { store } from '../lib/store';
  import type { DataPayload } from '../lib/types';
  import Chart from './Chart.svelte';

  let { payload }: { payload: DataPayload } = $props();

  let granularity = $state<Granularity>('daily');

  const accounts = $derived(
    memoized(['investments', payload.data_version, $store.scope, granularity], () =>
      investmentSeries(payload, $store.scope, granularity),
    ),
  );
  const option = $derived(investmentOption(accounts));
  const holdings = $derived(
    accounts.map((a) => ({
      account: a,
      rows: payload.investments.find((i) => i.account_id === a.account_id)?.holdings ?? [],
    })),
  );
</script>

<section class="card" aria-labelledby="investments-title">
  <div class="row head">
    <h2 id="investments-title">Investments</h2>
    <div class="seg" role="group" aria-label="Investments granularity">
      <button type="button" aria-pressed={granularity === 'daily'} onclick={() => (granularity = 'daily')}>Daily</button>
      <button type="button" aria-pressed={granularity === 'monthly'} onclick={() => (granularity = 'monthly')}>
        Monthly average
      </button>
    </div>
  </div>

  {#if accounts.length === 0}
    <p class="muted">No investment accounts in this view.</p>
  {:else}
    <Chart {option} label="Investment account values over time" height={360} />
    <p class="muted">
      Dashed = estimated from transactions and public prices; solid = values reported by your institution.
    </p>
    <ul class="notes">
      {#each accounts as account (account.account_id)}
        {#if account.meta}
          {#if account.meta.status === 'downloading'}
            <li>{account.label}: Investment history is still downloading.</li>
          {/if}
          {#if account.meta.excluded_count > 0}
            <li>
              {account.label}: Estimate excludes {account.meta.excluded_count}
              {account.meta.excluded_count === 1 ? 'holding' : 'holdings'} ({money(account.meta.excluded_value)} of today's value).
            </li>
          {/if}
          {#each account.meta.warnings as warning}
            <li>{account.label}: {warning}</li>
          {/each}
        {/if}
      {/each}
    </ul>

    {#each holdings as { account, rows } (account.account_id)}
      <h3>{account.label}</h3>
      {#if rows.length === 0}
        <p class="muted">No holdings reported.</p>
      {:else}
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Holding</th><th class="num">Quantity</th><th class="num">Price</th><th class="num">Value</th>
              </tr>
            </thead>
            <tbody>
              {#each rows as row}
                <tr>
                  <td>
                    {row.security_name ?? row.ticker ?? 'Unknown'}{row.ticker && row.security_name ? ` (${row.ticker})` : ''}
                    {#if row.price_as_of}<div class="muted">price as of {formatDate(row.price_as_of)}</div>{/if}
                  </td>
                  <td class="num">{row.quantity}</td>
                  <td class="num">{row.price === null ? '—' : money(row.price)}</td>
                  <td class="num">{row.value === null ? '—' : money(row.value)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/each}
  {/if}
</section>

<style>
  .head {
    justify-content: space-between;
  }
  .notes {
    padding-left: 20px;
  }
</style>
