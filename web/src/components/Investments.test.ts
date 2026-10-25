import 'fake-indexeddb/auto';
import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const instance = { on: vi.fn(), setOption: vi.fn(), dispatchAction: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
vi.mock('../lib/charts', () => ({ echarts: { init: vi.fn(() => instance) } }));

import { makePayload } from '../lib/__fixtures__/payload';
import { resetForTests, store } from '../lib/store';
import Investments from './Investments.svelte';
import InvestmentsSection from './InvestmentsSection.svelte';

let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
  resetForTests();
  for (const name of ['ResizeObserver', 'IntersectionObserver']) {
    vi.stubGlobal(
      name,
      class {
        constructor(cb?: (entries: { isIntersecting: boolean }[]) => void) {
          // Treat every lazy section as visible so it loads straight away.
          if (name === 'IntersectionObserver') queueMicrotask(() => cb?.([{ isIntersecting: true }]));
        }
        observe() {}
        disconnect() {}
      },
    );
  }
});
afterEach(() => {
  if (mounted) unmount(mounted);
  mounted = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('Investments tab', () => {
  it('shows the charts first and the holdings list last', async () => {
    store.update((s) => ({ ...s, payload: makePayload(), online: true, loaded: true }));
    mounted = mount(Investments, { target: document.body });
    for (let i = 0; i < 20 && document.querySelectorAll('h2').length < 3; i++) {
      await new Promise((resolve) => setTimeout(resolve, 10));
      await tick();
    }
    const titles = [...document.querySelectorAll('h2')].map((h) => h.textContent);
    expect(titles).toEqual(['Investments', 'Net worth', 'Holdings']);
    const sections = [...document.querySelectorAll('section')];
    // The investments chart section no longer carries the per-account tables.
    expect(sections[0].querySelector('table')).toBeNull();
    const holdings = sections.at(-1)!;
    expect(holdings.querySelector('h3')?.textContent).toContain('Brokerage');
    expect(holdings.textContent).toContain('Index Fund (VTI)');
    // The joint IRA is tax-advantaged: it is on the Retirement tab, not here.
    expect(holdings.textContent).not.toContain('Joint IRA');
  });

  it('the investments section still lists holdings where it is used on its own', () => {
    mounted = mount(InvestmentsSection, { target: document.body, props: { payload: makePayload() } });
    flushSync();
    expect(document.querySelector('section table')).not.toBeNull();
  });
});

describe('estimate notes', () => {
  it('start collapsed behind a summary with a count, and expand on demand', () => {
    const payload = makePayload();
    payload.investments[0].estimate_meta!.warnings = ['first warning', 'second warning'];
    payload.data_version = 9001; // charts are memoized by data version
    mounted = mount(InvestmentsSection, { target: document.body, props: { payload } });
    flushSync();
    const details = document.querySelector('details.notes') as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.querySelector('summary')?.textContent).toBe('Notes on the estimates (2)');
    expect(details.querySelectorAll('li')).toHaveLength(2);
    details.querySelector('summary')!.click();
    expect(details.open).toBe(true);
  });

  it('are not shown when there are none', () => {
    mounted = mount(InvestmentsSection, { target: document.body, props: { payload: makePayload() } });
    flushSync();
    expect(document.querySelector('details.notes')).toBeNull();
  });
});
