<script lang="ts">
  import { computeBanners, isIosSafari, type BannerAction } from '../lib/banners';
  import { dismiss, dismissed } from '../lib/dismissed';
  import { isLocalhost, linkProgress, openLink } from '../lib/plaid';
  import { store } from '../lib/store';
  import { errorMessage } from '../lib/actions';

  let fixError = $state('');

  const banners = $derived(
    computeBanners({
      state: $store,
      isLocal: isLocalhost(),
      standalone: (navigator as Navigator & { standalone?: boolean }).standalone === true,
      iosSafari: isIosSafari(navigator.userAgent),
      dismissed: $dismissed,
      now: Date.now(),
    }),
  );

  let reloading = false;

  async function reload(): Promise<void> {
    if (reloading) return;
    reloading = true;
    const registration = await navigator.serviceWorker?.getRegistration();
    if (!registration) {
      location.reload();
      return;
    }
    navigator.serviceWorker.addEventListener('controllerchange', () => location.reload(), { once: true });
    await registration.update().catch(() => undefined);
    const installing = registration.installing;
    if (installing && !registration.waiting) {
      await new Promise<void>((resolve) => {
        installing.addEventListener('statechange', () => {
          if (installing.state !== 'installing') resolve();
        });
      });
    }
    if (registration.waiting) {
      registration.waiting.postMessage({ type: 'SKIP_WAITING' });
    } else {
      location.reload();
    }
  }

  async function act(action: BannerAction): Promise<void> {
    fixError = '';
    if (action.kind === 'reload') await reload();
    else if (action.kind === 'link' && action.href) location.hash = action.href;
    else if (action.kind === 'fix') {
      try {
        await openLink({ mode: 'update', itemId: action.itemId, items: $store.payload?.status.items ?? [] });
      } catch (error) {
        fixError = errorMessage(error);
      }
    }
  }
</script>

<div class="banners" aria-live="polite">
  {#each banners as banner (banner.id)}
    <div class="banner {banner.level}" role="status">
      <span>{banner.text}</span>
      {#if banner.action}
        <button type="button" onclick={() => act(banner.action!)}>{banner.action.label}</button>
      {/if}
      {#if banner.dismissKey}
        <button type="button" aria-label="Dismiss" onclick={() => dismiss(banner.dismissKey!)}>Dismiss</button>
      {/if}
    </div>
  {/each}
  {#if $linkProgress.state !== 'idle'}
    <div class="banner {$linkProgress.state === 'error' ? 'warn' : 'info'}" role="status">
      {$linkProgress.message}
    </div>
  {/if}
  {#if fixError}<div class="banner warn" role="alert">{fixError}</div>{/if}
</div>

<style>
  .banners {
    max-width: 980px;
    margin: 0 auto;
    padding: 8px 12px 0;
  }
  .banner {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    margin-bottom: 8px;
    border-radius: 8px;
    border: 1px solid var(--info-line);
    background: var(--info-bg);
  }
  .banner.warn {
    border-color: var(--warn-line);
    background: var(--warn-bg);
  }
  .banner span {
    flex: 1 1 220px;
  }
</style>
