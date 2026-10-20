import { mount, unmount, flushSync } from 'svelte';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const handlers: Record<string, (e: unknown) => void> = {};
const instance = {
  on: vi.fn((name: string, fn: (e: unknown) => void) => {
    handlers[name] = fn;
  }),
  setOption: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
};
vi.mock('../lib/charts', () => ({ echarts: { init: vi.fn(() => instance) } }));

import Chart from './Chart.svelte';

let resizeCallback: () => void = () => {};
let disconnected = false;

beforeEach(() => {
  for (const fn of Object.values(instance)) fn.mockClear();
  disconnected = false;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(cb: () => void) {
        resizeCallback = cb;
      }
      observe() {}
      disconnect() {
        disconnected = true;
      }
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('Chart', () => {
  it('inits, sets the option without merging, resizes and disposes', () => {
    const option = { series: [] };
    const component = mount(Chart, { target: document.body, props: { option, label: 'Spending chart' } });
    flushSync();
    expect(document.querySelector('[aria-label="Spending chart"]')).not.toBeNull();
    expect(instance.setOption).toHaveBeenCalledWith(option, { notMerge: true });
    resizeCallback();
    expect(instance.resize).toHaveBeenCalled();
    unmount(component);
    expect(instance.dispose).toHaveBeenCalled();
    expect(disconnected).toBe(true);
  });

  it('re-dispatches the last zoom range after a legend toggle', () => {
    const component = mount(Chart, { target: document.body, props: { option: {}, label: 'c' } });
    flushSync();
    handlers.datazoom({ batch: [{ start: 40, end: 90 }] });
    instance.dispatchAction.mockClear();
    handlers.legendselectchanged({});
    expect(instance.dispatchAction).toHaveBeenCalledWith({ type: 'dataZoom', start: 40, end: 90 });
    unmount(component);
  });

  it('forwards clicks', () => {
    const onclick = vi.fn();
    const component = mount(Chart, { target: document.body, props: { option: {}, label: 'c', onclick } });
    flushSync();
    handlers.click({ seriesId: 's', name: '2026-09' });
    expect(onclick).toHaveBeenCalledWith({ seriesId: 's', name: '2026-09' });
    unmount(component);
  });
});
