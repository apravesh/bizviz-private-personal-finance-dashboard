<script lang="ts">
  import type { DataPayload } from '../lib/types';
  import MonthlyChart from './MonthlyChart.svelte';

  let { payload }: { payload: DataPayload } = $props();
</script>

<MonthlyChart
  {payload}
  kind="spending"
  title="Spending per month"
  splits={[
    { value: 'none', label: 'None' },
    { value: 'category', label: 'Category' },
    { value: 'merchant', label: 'Merchant' },
    { value: 'account', label: 'Account' },
    { value: 'recurring', label: 'Recurring vs one-off' },
  ]}
/>
