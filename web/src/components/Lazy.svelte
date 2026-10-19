<script lang="ts">
  import { onMount, type Component } from 'svelte';
  import type { DataPayload } from '../lib/types';

  interface Props {
    load: () => Promise<{ default: Component<{ payload: DataPayload }> }>;
    payload: DataPayload;
    minHeight?: number;
  }

  let { load, payload, minHeight = 200 }: Props = $props();

  let Loaded = $state.raw<Component<{ payload: DataPayload }> | null>(null);
  let placeholder: HTMLDivElement | undefined = $state();

  onMount(() => {
    let started = false;
    const go = () => {
      if (started) return;
      started = true;
      void load().then((module) => (Loaded = module.default));
    };
    let observer: IntersectionObserver | undefined;
    if (typeof IntersectionObserver !== 'undefined') {
      observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) go();
        },
        { rootMargin: '600px' },
      );
      if (placeholder) observer.observe(placeholder);
    }
    const idle = window.setTimeout(go, 2500);
    return () => {
      observer?.disconnect();
      clearTimeout(idle);
    };
  });
</script>

{#if Loaded}
  <Loaded {payload} />
{:else}
  <div bind:this={placeholder} style:min-height="{minHeight}px" class="card muted">Loading…</div>
{/if}
