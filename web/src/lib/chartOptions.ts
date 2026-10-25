import type { EChartsCoreOption } from 'echarts/core';
import { money, moneyAxis } from './aggregate';
import type {
  CategoryBreakdown,
  CategorySlice,
  InvestmentAccountSeries,
  MonthlyResult,
  NetWorthSeriesPoint,
  SavingsResult,
  StackedMonths,
} from './aggregate';
import { escapeHtml, monthLabel } from './format';

export const PALETTE = [
  '#2f6fdd',
  '#e07b24',
  '#2a9d6f',
  '#b04bd6',
  '#d64550',
  '#8d6e3f',
  '#17a2b8',
  '#7a8b2e',
  '#c2559b',
  '#5b6b7b',
  '#e0b000',
  '#4a4adb',
];

export const DEFAULT_ZOOM_MONTHS = 24;

const CURRENT_MONTH_DECAL = {
  symbol: 'rect',
  rotation: Math.PI / 4,
  dashArrayX: [1, 0],
  dashArrayY: [2, 3],
  color: 'rgba(255,255,255,0.55)',
};

export const SAVED_COLOR = '#2a9d6f';
export const OVERSPENT_COLOR = '#d64550';

export function inProgressSuffix(month: string, inProgress: string): string {
  return month === inProgress ? ' (in progress)' : '';
}

function monthZoom(n: number): object[] {
  const startValue = Math.max(0, n - DEFAULT_ZOOM_MONTHS);
  return [
    { type: 'inside', startValue, endValue: n - 1, zoomLock: false },
    { type: 'slider', startValue, endValue: n - 1, height: 24, bottom: 8 },
  ];
}

function monthAxis(months: string[], inProgress: string): object {
  return {
    type: 'category',
    data: months,
    axisLabel: {
      hideOverlap: true,
      formatter: (value: string) => `${monthLabel(value)}${value === inProgress ? '\n(in progress)' : ''}`,
    },
  };
}

export function barTooltipHtml(
  result: MonthlyResult,
  index: number,
  noun: 'Spending' | 'Earnings',
  marker: (seriesKey: string) => string = () => '',
): string {
  const month = result.months[index];
  const lines = [`<strong>${escapeHtml(monthLabel(month))}${inProgressSuffix(month, result.inProgress)}</strong>`];
  const multi = result.series.length > 1;
  if (multi) {
    for (const s of result.series) {
      const value = s.values[index];
      if (value === 0) continue;
      lines.push(`${marker(s.key)}${escapeHtml(s.label)}: ${money(value)}`);
    }
  }
  lines.push(`<strong>${noun} total: ${money(result.totals[index])}</strong>`);
  const pending = result.pendingByMonth[index];
  if (pending !== 0) lines.push(`includes ${money(pending)} pending`);
  return lines.join('<br/>');
}

export function barOption(result: MonthlyResult, noun: 'Spending' | 'Earnings', stacked: boolean): EChartsCoreOption {
  const n = result.months.length;
  const multi = result.series.length > 1;
  return {
    color: PALETTE,
    aria: { enabled: true, decal: { show: false } },
    animation: false,
    grid: { left: 8, right: 12, top: multi ? 56 : 16, bottom: 64, containLabel: true },
    legend: multi ? { type: 'scroll', top: 0 } : { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      confine: true,
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number; seriesId: string; marker: string }[];
        if (!list.length) return '';
        const markers = new Map(list.map((p) => [p.seriesId, p.marker]));
        return barTooltipHtml(result, list[0].dataIndex, noun, (key) => markers.get(key) ?? '');
      },
    },
    xAxis: monthAxis(result.months, result.inProgress),
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: monthZoom(n),
    series: result.series.map((s) => ({
      id: s.key,
      name: s.label,
      type: 'bar',
      stack: stacked ? 'total' : undefined,
      barMaxWidth: 36,
      emphasis: { focus: 'series' },
      data: s.values.map((value, i) =>
        result.months[i] === result.inProgress ? { value, itemStyle: { decal: CURRENT_MONTH_DECAL } } : value,
      ),
    })),
  };
}

export function savingsTooltipHtml(result: SavingsResult, index: number): string {
  const month = result.months[index];
  const saved = result.savings[index];
  const lines = [
    `<strong>${escapeHtml(monthLabel(month))}${inProgressSuffix(month, result.inProgress)}</strong>`,
    `Earnings: ${money(result.earnings[index])}`,
    `Spending: ${money(result.spending[index])}`,
    `<strong>${saved < 0 ? 'Spent more than earned' : 'Saved'}: ${money(Math.abs(saved))}</strong>`,
  ];
  const pending = result.pendingByMonth[index];
  if (pending !== 0) lines.push(`includes ${money(pending)} net pending`);
  return lines.join('<br/>');
}

export function savingsOption(result: SavingsResult): EChartsCoreOption {
  return {
    aria: { enabled: true, decal: { show: false } },
    animation: false,
    grid: { left: 8, right: 12, top: 16, bottom: 64, containLabel: true },
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      confine: true,
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number }[];
        return list.length ? savingsTooltipHtml(result, list[0].dataIndex) : '';
      },
    },
    xAxis: monthAxis(result.months, result.inProgress),
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: monthZoom(result.months.length),
    series: [
      {
        id: 'savings',
        name: 'Savings',
        type: 'bar',
        barMaxWidth: 36,
        data: result.savings.map((value, i) => ({
          value,
          itemStyle: {
            color: value < 0 ? OVERSPENT_COLOR : SAVED_COLOR,
            ...(result.months[i] === result.inProgress ? { decal: CURRENT_MONTH_DECAL } : {}),
          },
        })),
      },
    ],
  };
}

export function pieTooltipHtml(slice: CategorySlice, total: number): string {
  const share = total > 0 ? Math.round((slice.value / total) * 1000) / 10 : 0;
  return `<strong>${escapeHtml(slice.label)}</strong><br/>${money(slice.value)} (${share}%)`;
}

export function categoryPieOption(breakdown: Pick<CategoryBreakdown, 'slices' | 'total'>): EChartsCoreOption {
  const byKey = new Map(breakdown.slices.map((s) => [s.key, s]));
  return {
    color: PALETTE,
    aria: { enabled: true, decal: { show: false } },
    animation: false,
    legend: { type: 'scroll', bottom: 0 },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: unknown) => {
        const slice = byKey.get((params as { data: { id: string } }).data.id);
        return slice ? pieTooltipHtml(slice, breakdown.total) : '';
      },
    },
    series: [
      {
        id: 'categories',
        name: 'Spending by category',
        type: 'pie',
        radius: ['35%', '68%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        cursor: 'pointer',
        label: { formatter: '{b}\n{d}%' },
        data: breakdown.slices.map((s) => ({ id: s.key, name: s.label, value: s.value })),
      },
    ],
  };
}

export function stackedMonthsTooltipHtml(
  result: StackedMonths,
  index: number,
  totalLabel: string,
  marker: (key: string) => string = () => '',
): string {
  const lines = [`<strong>${escapeHtml(monthLabel(result.months[index]))}</strong>`];
  for (const s of result.series) {
    const value = s.values[index];
    if (value !== null) lines.push(`${marker(s.key)}${escapeHtml(s.label)}: ${money(value)}`);
  }
  lines.push(`<strong>${escapeHtml(totalLabel)}: ${money(result.totals[index])}</strong>`);
  if (result.estimated[index]) lines.push('includes estimated values');
  return lines.join('<br/>');
}

/** One stacked bar per month (monthly averages); months with estimated values are hatched. */
export function stackedMonthsOption(result: StackedMonths, totalLabel: string): EChartsCoreOption {
  const n = result.months.length;
  return {
    color: PALETTE,
    aria: { enabled: true, decal: { show: false } },
    animation: false,
    grid: { left: 8, right: 12, top: 40, bottom: 64, containLabel: true },
    legend: { type: 'scroll', top: 0 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      confine: true,
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number; seriesId: string; marker: string }[];
        if (!list.length) return '';
        const markers = new Map(list.map((p) => [p.seriesId, p.marker]));
        return stackedMonthsTooltipHtml(result, list[0].dataIndex, totalLabel, (key) => markers.get(key) ?? '');
      },
    },
    xAxis: {
      type: 'category',
      data: result.months,
      axisLabel: { hideOverlap: true, formatter: (value: string) => monthLabel(value) },
    },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: monthZoom(n),
    series: result.series.map((s) => ({
      id: s.key,
      name: s.label,
      type: 'bar',
      stack: 'total',
      barMaxWidth: 36,
      emphasis: { focus: 'series' },
      data: s.values.map((value, i) =>
        result.estimated[i] && value !== null ? { value, itemStyle: { decal: CURRENT_MONTH_DECAL } } : value,
      ),
    })),
  };
}

type Pair = [string, number];

export function investmentTooltipHtml(
  rows: { seriesId: string; seriesName: string; marker: string; value: Pair }[],
): string {
  if (!rows.length) return '';
  const lines = [`<strong>${escapeHtml(rows[0].value[0])}</strong>`];
  for (const row of rows) {
    const kind = row.seriesId.endsWith(':estimated') ? 'estimated' : 'reported';
    lines.push(`${row.marker}${escapeHtml(row.seriesName)} (${kind}): ${money(row.value[1])}`);
  }
  return lines.join('<br/>');
}

export function investmentOption(accounts: InvestmentAccountSeries[]): EChartsCoreOption {
  const series = accounts.flatMap((account, i) => {
    const color = PALETTE[i % PALETTE.length];
    return [
      {
        id: `${account.account_id}:actual`,
        name: account.label,
        type: 'line',
        showSymbol: account.actual.length < 40,
        connectNulls: false,
        itemStyle: { color },
        lineStyle: { color, type: 'solid', width: 2 },
        data: account.actual,
      },
      {
        id: `${account.account_id}:estimated`,
        name: account.label,
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        itemStyle: { color },
        lineStyle: { color, type: 'dashed', width: 2, opacity: 0.55 },
        data: account.estimated,
      },
    ];
  });
  return {
    color: PALETTE,
    animation: false,
    grid: { left: 8, right: 16, top: 56, bottom: 56, containLabel: true },
    legend: { type: 'scroll', top: 0 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      formatter: (params: unknown) => investmentTooltipHtml(params as Parameters<typeof investmentTooltipHtml>[0]),
    },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 24, bottom: 8 },
    ],
    series,
  };
}

export const NET_WORTH_COLORS = {
  retirement: '#2a9d6f',
  otherAssets: '#2f6fdd',
  debts: '#d64550',
  net: '#1b2230',
};

export function netWorthTooltipHtml(point: NetWorthSeriesPoint): string {
  const lines = [
    `<strong>${escapeHtml(point.date)}</strong>`,
    `<strong>Net worth: ${money(point.net)}</strong>`,
    `Retirement &amp; tax-advantaged: ${money(point.retirement)}`,
    `Other assets: ${money(point.otherAssets)}`,
    `Debts: ${money(point.debts)}`,
  ];
  if (point.stale_accounts > 0) {
    lines.push(
      `${point.stale_accounts} account${point.stale_accounts === 1 ? '' : 's'} not updated recently`,
    );
  }
  return lines.join('<br/>');
}

/** Stacked bars of retirement and other assets above zero, debts below, and net worth as a line. */
export function netWorthOption(points: NetWorthSeriesPoint[]): EChartsCoreOption {
  const byDate = new Map(points.map((p) => [p.date, p]));
  const bar = (id: keyof typeof NET_WORTH_COLORS, name: string, stack: string, value: (p: NetWorthSeriesPoint) => number) => ({
    id,
    name,
    type: 'bar',
    stack,
    barMaxWidth: 18,
    itemStyle: { color: NET_WORTH_COLORS[id] },
    data: points.map((p) => [p.date, value(p)]),
  });
  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    grid: { left: 8, right: 16, top: 40, bottom: 56, containLabel: true },
    legend: { top: 0 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      confine: true,
      formatter: (params: unknown) => {
        const list = params as { value: [string, number] }[];
        const point = list.length ? byDate.get(list[0].value[0]) : undefined;
        return point ? netWorthTooltipHtml(point) : '';
      },
    },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 24, bottom: 8 },
    ],
    series: [
      bar('retirement', 'Retirement & tax-advantaged', 'assets', (p) => p.retirement),
      bar('otherAssets', 'Other assets', 'assets', (p) => p.otherAssets),
      bar('debts', 'Debts', 'debts', (p) => -p.debts),
      {
        id: 'net',
        name: 'Net worth',
        type: 'line',
        showSymbol: points.length < 40,
        connectNulls: false,
        itemStyle: { color: NET_WORTH_COLORS.net },
        lineStyle: { width: 2, color: NET_WORTH_COLORS.net },
        z: 3,
        data: points.map((p) => [p.date, p.net]),
      },
    ],
  };
}
