<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';

  interface Props {
    title: string;
    onclose: () => void;
    children: Snippet;
    layer?: number;
  }

  let { title, onclose, children, layer = 10 }: Props = $props();
  let panel: HTMLDivElement;

  onMount(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.focus();
    return () => previous?.focus?.();
  });

  function onkeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onclose();
    }
  }
</script>

<div class="backdrop" style:z-index={layer} role="presentation" onclick={onclose} {onkeydown}>
  <div
    class="panel"
    bind:this={panel}
    role="dialog"
    aria-modal="true"
    aria-label={title}
    tabindex="-1"
    onclick={(e) => e.stopPropagation()}
    onkeydown={() => {}}
  >
    <header>
      <h2>{title}</h2>
      <button type="button" onclick={onclose} aria-label="Close">×</button>
    </header>
    {@render children()}
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(20, 25, 35, 0.5);
    display: flex;
    align-items: flex-end;
    justify-content: center;
  }
  .panel {
    background: #fff;
    width: 100%;
    max-width: 640px;
    max-height: 90vh;
    overflow-y: auto;
    border-radius: 16px 16px 0 0;
    padding: 16px;
    outline: none;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }
  @media (min-width: 700px) {
    .backdrop {
      align-items: center;
    }
    .panel {
      border-radius: 16px;
    }
  }
</style>
