<script lang="ts">
  import type { DataPayload } from '../lib/types';
  import MonthlyChart from './MonthlyChart.svelte';

  let { payload }: { payload: DataPayload } = $props();
</script>

<MonthlyChart
  {payload}
  kind="earning"
  title="Earnings per month"
  splits={[
    { value: 'none', label: 'None' },
    { value: 'category', label: 'Category' },
    { value: 'account', label: 'Account' },
  ]}
/>
