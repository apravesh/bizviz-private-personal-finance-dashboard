import { describe, expect, it } from 'vitest';
import { investmentSeries, monthlySavings, monthlySpending, netWorthSeries } from './aggregate';
import {
  barOption,
  barTooltipHtml,
  investmentOption,
  investmentTooltipHtml,
  netWorthOption,
  netWorthTooltipHtml,
  OVERSPENT_COLOR,
  SAVED_COLOR,
  savingsOption,
  savingsTooltipHtml,
} from './chartOptions';
import { makePayload, TODAY } from './__fixtures__/payload';

type Opt = Record<string, any>;

describe('barOption', () => {
  const result = monthlySpending(makePayload(), 'mine', 'category', TODAY);
  const option = barOption(result, 'Spending', true) as Opt;

  it('stacks the series and uses the series key as id', () => {
    expect(option.series.map((s: Opt) => s.id)).toEqual(['groceries', 'dining']);
    expect(option.series.every((s: Opt) => s.stack === 'total')).toBe(true);
  });

  it('does not stack when asked not to', () => {
    const flat = barOption(result, 'Earnings', false) as Opt;
    expect(flat.series[0].stack).toBeUndefined();
  });

  it('puts a decal only on the current month bars', () => {
    const data = option.series[0].data;
    expect(data[0]).toBe(100);
    expect(data[2].itemStyle.decal).toBeDefined();
    expect(option.aria.enabled).toBe(true);
  });

  it('labels the in-progress month on the axis', () => {
    const fmt = option.xAxis.axisLabel.formatter as (v: string) => string;
    expect(fmt('2026-10')).toContain('in progress');
    expect(fmt('2026-09')).toBe('Sep 2026');
  });

  it('defaults the zoom window to the last 24 months', () => {
    const p = makePayload();
    p.transactions.push({ ...p.transactions[0], id: 'old', date: '2023-01-05' });
    const wide = barOption(monthlySpending(p, 'mine', 'none', TODAY), 'Spending', true) as Opt;
    const n = wide.xAxis.data.length;
    expect(n).toBe(46);
    for (const zoom of wide.dataZoom) {
      expect(zoom.startValue).toBe(n - 24);
      expect(zoom.endValue).toBe(n - 1);
    }
    expect(wide.dataZoom.map((z: Opt) => z.type)).toEqual(['inside', 'slider']);
  });

  it('shows the whole axis when shorter than 24 months', () => {
    expect(option.dataZoom[0].startValue).toBe(0);
  });

  it('hides the legend without a split', () => {
    const single = barOption(monthlySpending(makePayload(), 'mine', 'none', TODAY), 'Spending', true) as Opt;
    expect(single.legend.show).toBe(false);
  });
});

describe('savingsOption', () => {
  const result = monthlySavings(makePayload(), 'mine', TODAY);
  const option = savingsOption(result) as Opt;

  it('draws one unstacked bar series of the savings', () => {
    expect(option.series).toHaveLength(1);
    expect(option.series[0].stack).toBeUndefined();
    expect(option.series[0].data.map((d: Opt) => d.value)).toEqual([-120, 2800, -80]);
  });

  it('colors saved months and overspent months differently', () => {
    const colors = option.series[0].data.map((d: Opt) => d.itemStyle.color);
    expect(colors).toEqual([OVERSPENT_COLOR, SAVED_COLOR, OVERSPENT_COLOR]);
  });

  it('marks only the current month as in progress', () => {
    const data = option.series[0].data;
    expect(data[1].itemStyle.decal).toBeUndefined();
    expect(data[2].itemStyle.decal).toBeDefined();
    const fmt = option.xAxis.axisLabel.formatter as (v: string) => string;
    expect(fmt('2026-10')).toContain('in progress');
  });

  it('defaults the zoom window like the other monthly charts', () => {
    expect(option.dataZoom.map((z: Opt) => z.type)).toEqual(['inside', 'slider']);
    expect(option.dataZoom[0].endValue).toBe(2);
  });
});

describe('savingsTooltipHtml', () => {
  const result = monthlySavings(makePayload(), 'mine', TODAY);

  it('shows earnings, spending and what was saved', () => {
    const html = savingsTooltipHtml(result, 1);
    expect(html).toContain('Sep 2026');
    expect(html).toContain('Earnings: $3,000.00');
    expect(html).toContain('Spending: $200.00');
    expect(html).toContain('Saved: $2,800.00');
    expect(html).not.toContain('includes');
  });

  it('says plainly when spending was higher, with pending noted', () => {
    const html = savingsTooltipHtml(result, 2);
    expect(html).toContain('Oct 2026 (in progress)');
    expect(html).toContain('Spent more than earned: $80.00');
    expect(html).toContain('includes -$80.00 net pending');
  });
});

describe('barTooltipHtml', () => {
  it('shows per-series values, total, and pending', () => {
    const result = monthlySpending(makePayload(), 'mine', 'category', TODAY);
    const html = barTooltipHtml(result, 2, 'Spending');
    expect(html).toContain('Oct 2026 (in progress)');
    expect(html).toContain('Dining: $80.00');
    expect(html).not.toContain('Groceries');
    expect(html).toContain('Spending total: $80.00');
    expect(html).toContain('includes $80.00 pending');
  });

  it('omits the pending line when there is none', () => {
    const result = monthlySpending(makePayload(), 'mine', 'category', TODAY);
    expect(barTooltipHtml(result, 0, 'Spending')).not.toContain('includes');
  });

  it('escapes labels', () => {
    const result = monthlySpending(makePayload(), 'mine', 'merchant', TODAY);
    result.series[0].label = '<img src=x onerror=1>';
    expect(barTooltipHtml(result, 0, 'Spending')).not.toContain('<img');
  });
});

describe('investmentOption', () => {
  const accounts = investmentSeries(makePayload(), 'mine', 'daily');
  const option = investmentOption(accounts) as Opt;

  it('has an actual (solid) and estimated (dashed, lighter) series per account sharing a legend name', () => {
    expect(option.series).toHaveLength(2);
    const [actual, estimated] = option.series;
    expect(actual.name).toBe(estimated.name);
    expect(actual.lineStyle.type).toBe('solid');
    expect(estimated.lineStyle.type).toBe('dashed');
    expect(estimated.lineStyle.opacity).toBeLessThan(1);
    expect(actual.lineStyle.color).toBe(estimated.lineStyle.color);
  });

  it('never connects nulls', () => {
    expect(option.series.every((s: Opt) => s.connectNulls === false)).toBe(true);
  });

  it('tooltip distinguishes estimated from reported', () => {
    const html = investmentTooltipHtml([
      { seriesId: 'a:actual', seriesName: 'A', marker: '', value: ['2026-10-01', 10] },
      { seriesId: 'a:estimated', seriesName: 'A', marker: '', value: ['2026-10-01', 12] },
    ]);
    expect(html).toContain('A (reported): $10.00');
    expect(html).toContain('A (estimated): $12.00');
  });
});

describe('netWorthOption', () => {
  const { points } = netWorthSeries(makePayload(), 'mine', 'daily');

  it('plots net worth over time', () => {
    const option = netWorthOption(points) as Opt;
    expect(option.series[0].data[0]).toEqual(['2026-09-28', 500]);
  });

  it('tooltip mentions stale accounts only when there are some', () => {
    expect(netWorthTooltipHtml(points[0])).not.toContain('not updated');
    expect(netWorthTooltipHtml({ ...points[0], stale_accounts: 1 })).toContain('1 account not updated recently');
    expect(netWorthTooltipHtml({ ...points[0], stale_accounts: 3 })).toContain('3 accounts not updated recently');
  });

  it('tooltip formatter looks the point up by date', () => {
    const option = netWorthOption(points) as Opt;
    const html = option.tooltip.formatter([{ value: ['2026-10-02', 1100] }]);
    expect(html).toContain('2 accounts not updated recently');
  });
});
