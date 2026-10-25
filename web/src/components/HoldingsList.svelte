<script lang="ts">
  import { money } from '../lib/aggregate';
  import { formatDate } from '../lib/format';
  import type { DataPayload } from '../lib/types';

  interface Props {
    payload: DataPayload;
    accounts: { account_id: string; label: string }[];
  }

  let { payload, accounts }: Props = $props();

  const rows = $derived(
    accounts.map((account) => ({
      account,
      holdings: payload.investments.find((i) => i.account_id === account.account_id)?.holdings ?? [],
    })),
  );
</script>

{#each rows as { account, holdings } (account.account_id)}
  <h3>{account.label}</h3>
  {#if holdings.length === 0}
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
          {#each holdings as row}
            <tr>
              <td>
                {row.security_name ?? row.ticker ?? 'Unknown'}{row.ticker && row.security_name ? ` (${row.ticker})` : ''}
                {#if row.price_as_of}<div class="muted">price as of {formatDate(row.price_as_of)}</div>{/if}
              </td>
              <td class="num">{row.quantity.toLocaleString('en-US', { maximumFractionDigits: 6 })}</td>
              <td class="num">{row.price === null ? '—' : money(row.price)}</td>
              <td class="num">{row.value === null ? '—' : money(row.value)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/each}
