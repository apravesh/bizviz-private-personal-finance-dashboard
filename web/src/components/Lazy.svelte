<script lang="ts">
  import { onMount, type Component } from 'svelte';
  import type { DataPayload } from '../lib/types';

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Loadable = Component<{ payload: DataPayload } & Record<string, any>>;

  interface Props {
    load: () => Promise<{ default: Loadable }>;
    payload: DataPayload;
    minHeight?: number;
    /** Extra props for the loaded component. */
    props?: Record<string, unknown>;
  }

  let { load, payload, minHeight = 200, props = {} }: Props = $props();

  let Loaded = $state.raw<Loadable | null>(null);
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
  <Loaded {payload} {...props} />
{:else}
  <div bind:this={placeholder} style:min-height="{minHeight}px" class="card muted">Loading…</div>
{/if}
