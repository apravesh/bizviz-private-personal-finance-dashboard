import 'fake-indexeddb/auto';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const instance = { on: vi.fn(), setOption: vi.fn(), dispatchAction: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
vi.mock('../lib/charts', () => ({ echarts: { init: vi.fn(() => instance) } }));

import { makePayload } from '../lib/__fixtures__/payload';
import { resetForTests, store } from '../lib/store';
import Dashboard from './Dashboard.svelte';

let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
  resetForTests();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  if (mounted) unmount(mounted);
  mounted = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('Dashboard', () => {
  it('shows spending first, then savings, earnings and recurring, without investments', () => {
    store.update((s) => ({ ...s, payload: makePayload(), online: true, loaded: true }));
    mounted = mount(Dashboard, { target: document.body });
    flushSync();
    const titles = [...document.querySelectorAll('h2')].map((h) => h.textContent);
    expect(titles).toEqual(['Spending', 'Savings per month', 'Earnings per month', 'Recurring charges']);
    // Inside the Spending card: card bills, the category pies, then the monthly bars last.
    const spending = document.querySelector('section')!;
    expect([...spending.querySelectorAll('h3')].map((h) => h.textContent)).toEqual([
      'Credit card bills per month',
      'Spending by category',
      'Spending per month',
    ]);
    // The Split by control moved with the monthly bars.
    expect(spending.querySelector('.bars select[aria-label="Split Spending per month"]')).not.toBeNull();
  });
});
