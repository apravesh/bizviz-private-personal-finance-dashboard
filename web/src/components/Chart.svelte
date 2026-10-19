<script lang="ts">
  import { onMount } from 'svelte';
  import { echarts } from '../lib/charts';
  import type { EChartsCoreOption, ECharts } from 'echarts/core';

  interface Props {
    option: EChartsCoreOption;
    label: string;
    height?: number;
    onclick?: (params: { seriesId?: string; name?: string; value?: unknown }) => void;
  }

  let { option, label, height = 320, onclick }: Props = $props();

  let element: HTMLDivElement;
  let chart: ECharts | undefined = $state.raw();
  let zoom: { start: number; end: number } | null = null;

  function applyZoom(): void {
    if (chart && zoom) chart.dispatchAction({ type: 'dataZoom', start: zoom.start, end: zoom.end });
  }

  onMount(() => {
    const instance = echarts.init(element);
    instance.on('click', (params) => onclick?.(params as { seriesId?: string; name?: string }));
    instance.on('datazoom', (event) => {
      const e = event as { start?: number; end?: number; batch?: { start?: number; end?: number }[] };
      const range = e.batch?.[0] ?? e;
      if (typeof range.start === 'number' && typeof range.end === 'number') {
        zoom = { start: range.start, end: range.end };
      }
    });
    // Legend toggles can reset dataZoom (ECharts issue #20392): put the last range back.
    instance.on('legendselectchanged', applyZoom);

    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => instance.resize());
      observer.observe(element);
    }
    chart = instance;
    return () => {
      observer?.disconnect();
      instance.dispose();
      chart = undefined;
    };
  });

  $effect(() => {
    if (!chart) return;
    chart.setOption(option, { notMerge: true });
    applyZoom();
  });
</script>

<div bind:this={element} class="chart" style:height="{height}px" role="img" aria-label={label}></div>

<style>
  .chart {
    width: 100%;
    touch-action: pan-y;
  }
</style>
