<script lang="ts">
  import { money } from '../lib/aggregate';
  import { formatDate } from '../lib/format';
  import { store } from '../lib/store';
  import { TAX_GROUP_LABELS, taxGroup, taxSummary, withInvestments, yearlyMoneyIn } from '../lib/taxAdvantaged';
  import Lazy from './Lazy.svelte';

  const payload = $derived($store.payload);
  const summary = $derived(payload ? taxSummary(payload, $store.scope) : null);
  const yearly = $derived(payload ? yearlyMoneyIn(payload, $store.scope) : []);
  const thisYear = String(new Date().getFullYear());
  const accountsById = $derived(new Map((payload?.accounts ?? []).map((a) => [a.account_id, a])));

  function groupOf(accountId: string): { key: string; label: string } {
    const account = accountsById.get(accountId);
    const group = account ? taxGroup(account) : null;
    return group ? { key: group, label: TAX_GROUP_LABELS[group] } : { key: 'unknown', label: 'Unknown' };
  }
</script>

{#if payload && summary}
  <section class="card" aria-labelledby="retirement-title">
    <h2 id="retirement-title">Retirement and tax-advantaged accounts</h2>
    {#if summary.groups.length === 0}
      <p class="muted">
        No tax-advantaged accounts in this view. 401(k), 403(b), IRA, Roth, HSA and 529 accounts appear here once
        linked.
      </p>
    {:else}
      <p class="total">{money(summary.total)} <span class="muted">total current value</span></p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr><th>Account</th><th class="num">Value</th><th>As of</th></tr>
          </thead>
          {#each summary.groups as group (group.group)}
            <tbody>
              <tr class="group">
                <th scope="rowgroup">{group.label}</th>
                <td class="num">{money(group.total)}</td>
                <td></td>
              </tr>
              {#each group.rows as row (row.account_id)}
                <tr>
                  <td class="indent">{row.label}</td>
                  <td class="num">{row.value === null ? '—' : money(row.value)}</td>
                  <td class="muted">{row.asOf ? formatDate(row.asOf) : 'unknown'}</td>
                </tr>
              {/each}
            </tbody>
          {/each}
        </table>
      </div>
      {#if summary.groups.some((g) => g.missingValues > 0)}
        <p class="muted">Accounts showing — have no balance reported yet and are not in the totals.</p>
      {/if}
      <p class="muted">
        Grouped by the account type your institution reports. A 401(k) that holds both pre-tax and Roth money is
        reported as one account, so it appears once, under pre-tax.
      </p>
    {/if}
  </section>

  {#if summary.groups.length > 0}
    <Lazy
      payload={withInvestments(payload, true)}
      minHeight={420}
      load={() => import('./InvestmentsSection.svelte')}
      props={{
        id: 'retirement-investments',
        title: 'Value over time',
        emptyText: 'No value history for these accounts yet.',
        stacked: true,
        groupOf,
        groupLabel: 'tax treatment',
      }}
    />

    <section class="card" aria-labelledby="moved-in-title">
      <h2 id="moved-in-title">Money moved in per year</h2>
      {#if yearly.length === 0}
        <p class="muted">No contributions, deposits or withdrawals have been reported for these accounts yet.</p>
      {:else}
        <div class="table-scroll">
          <table>
            <thead>
              <tr><th>Year</th><th>Account</th><th class="num">Moved in</th><th class="num">Moved out</th></tr>
            </thead>
            <tbody>
              {#each yearly as row (`${row.year}|${row.account_id}`)}
                <tr>
                  <td>{row.year}{row.year === thisYear ? ' (in progress)' : ''}</td>
                  <td>{row.label}</td>
                  <td class="num">{money(row.movedIn)}</td>
                  <td class="num">{row.movedOut === 0 ? '—' : money(row.movedOut)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted">
          Contributions, deposits and transfers in, and withdrawals and transfers out, as your institution reports
          them. Employer contributions are included only if the institution reports them as contributions. Plaid
          provides up to 24 months of this history, so the earliest year may be incomplete.
        </p>
      {/if}
    </section>
  {/if}
{/if}

<style>
  .total {
    font-size: 1.4rem;
    font-weight: 600;
    margin: 4px 0 12px;
  }
  .total .muted {
    font-size: 0.9rem;
    font-weight: 400;
  }
  .group th,
  .group td {
    font-weight: 600;
    border-top: 1px solid var(--line);
  }
  .indent {
    padding-left: 20px;
  }
</style>
