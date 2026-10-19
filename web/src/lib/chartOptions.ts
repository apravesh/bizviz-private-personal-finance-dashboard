import type { EChartsCoreOption } from 'echarts/core';
import { money, moneyAxis } from './aggregate';
import type { InvestmentAccountSeries, MonthlyResult, NetWorthSeriesPoint } from './aggregate';
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

export function inProgressSuffix(month: string, inProgress: string): string {
  return month === inProgress ? ' (in progress)' : '';
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
    xAxis: {
      type: 'category',
      data: result.months,
      axisLabel: {
        hideOverlap: true,
        formatter: (value: string) => `${monthLabel(value)}${value === result.inProgress ? '\n(in progress)' : ''}`,
      },
    },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: [
      { type: 'inside', startValue: Math.max(0, n - DEFAULT_ZOOM_MONTHS), endValue: n - 1, zoomLock: false },
      {
        type: 'slider',
        startValue: Math.max(0, n - DEFAULT_ZOOM_MONTHS),
        endValue: n - 1,
        height: 24,
        bottom: 8,
      },
    ],
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

export function netWorthTooltipHtml(point: NetWorthSeriesPoint): string {
  const lines = [
    `<strong>${escapeHtml(point.date)}</strong>`,
    `Net worth: ${money(point.net)}`,
    `Assets: ${money(point.assets)}`,
    `Debts: ${money(point.debts)}`,
  ];
  if (point.stale_accounts > 0) {
    lines.push(
      `${point.stale_accounts} account${point.stale_accounts === 1 ? '' : 's'} not updated recently`,
    );
  }
  return lines.join('<br/>');
}

export function netWorthOption(points: NetWorthSeriesPoint[]): EChartsCoreOption {
  const byDate = new Map(points.map((p) => [p.date, p]));
  return {
    color: PALETTE,
    animation: false,
    grid: { left: 8, right: 16, top: 24, bottom: 56, containLabel: true },
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      confine: true,
      formatter: (params: unknown) => {
        const list = params as { value: [string, number] }[];
        const point = list.length ? byDate.get(list[0].value[0]) : undefined;
        return point ? netWorthTooltipHtml(point) : '';
      },
    },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => moneyAxis(v) } },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 24, bottom: 8 },
    ],
    series: [
      {
        id: 'net',
        name: 'Net worth',
        type: 'line',
        showSymbol: points.length < 40,
        connectNulls: false,
        lineStyle: { width: 2 },
        data: points.map((p) => [p.date, p.net]),
      },
    ],
  };
}
