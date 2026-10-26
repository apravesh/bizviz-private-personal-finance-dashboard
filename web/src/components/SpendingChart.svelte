<script lang="ts">
  import type { DataPayload } from '../lib/types';
  import CardBillsChart from './CardBillsChart.svelte';
  import MonthlyChart from './MonthlyChart.svelte';
  import SpendingPie from './SpendingPie.svelte';

  let { payload }: { payload: DataPayload } = $props();
</script>

<MonthlyChart
  {payload}
  kind="spending"
  cardTitle="Spending"
  title="Spending per month"
  splits={[
    { value: 'none', label: 'None' },
    { value: 'category', label: 'Category' },
    { value: 'merchant', label: 'Merchant' },
    { value: 'account', label: 'Account' },
    { value: 'recurring', label: 'Recurring vs one-off' },
  ]}
>
  <CardBillsChart {payload} />
  <SpendingPie {payload} />
</MonthlyChart>
