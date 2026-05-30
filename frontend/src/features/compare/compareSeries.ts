import type { EquityPoint } from '../../types';

export type CompareSeries = {
  accountCode: string;
  accountName: string;
  points: EquityPoint[];
};

export type CompareMode = 'rebased' | 'absolute';

export function mergeDates(seriesList: CompareSeries[]): string[] {
  const set = new Set<string>();
  for (const s of seriesList) {
    for (const p of s.points) {
      set.add(p.trade_date);
    }
  }
  return [...set].sort();
}

export function firstTradeDate(series: CompareSeries): string | null {
  return series.points[0]?.trade_date ?? null;
}

/** 共同起点 = 各组合首个交易日中最晚的那天 */
export function calcOverlapStart(seriesList: CompareSeries[]): string | null {
  const firstDates = seriesList.map(firstTradeDate).filter((d): d is string => d != null);
  if (!firstDates.length) return null;
  return firstDates.reduce((max, d) => (d > max ? d : max));
}

function compoundFactor(cumPct: number | null | undefined): number | null {
  if (cumPct == null || Number.isNaN(cumPct)) return null;
  return 1 + cumPct / 100;
}

function rebasePct(valuePct: number | null, basePct: number | null): number | null {
  const v = compoundFactor(valuePct);
  const b = compoundFactor(basePct);
  if (v == null || b == null || b <= 0) return null;
  return (v / b - 1) * 100;
}

function findPointOnOrAfter(points: EquityPoint[], date: string): EquityPoint | null {
  for (const p of points) {
    if (p.trade_date >= date) return p;
  }
  return null;
}

function findBenchAtDate(seriesList: CompareSeries[], date: string): number | null {
  for (const s of seriesList) {
    const p = s.points.find((pt) => pt.trade_date === date);
    if (p?.benchmark_return_pct != null) return p.benchmark_return_pct;
  }
  return null;
}

export type CompareChartData = {
  dates: string[];
  overlapStart: string | null;
  portfolioSeries: Array<{ name: string; data: (number | null)[] }>;
  benchData: (number | null)[] | null;
};

export function buildCompareChartData(seriesList: CompareSeries[], mode: CompareMode): CompareChartData {
  const allDates = mergeDates(seriesList);
  if (!allDates.length) {
    return { dates: [], overlapStart: null, portfolioSeries: [], benchData: null };
  }

  const overlapStart = calcOverlapStart(seriesList);
  const dates =
    mode === 'rebased' && overlapStart ? allDates.filter((d) => d >= overlapStart) : allDates;

  const portfolioSeries = seriesList.map((s) => {
    const byDate = new Map(s.points.map((p) => [p.trade_date, p]));

    if (mode === 'absolute') {
      return {
        name: s.accountName,
        data: dates.map((d) => byDate.get(d)?.cum_return_pct ?? null),
      };
    }

    const anchorDate = overlapStart ?? s.points[0]?.trade_date;
    const anchorPoint = anchorDate ? findPointOnOrAfter(s.points, anchorDate) : null;
    const anchorCum = anchorPoint?.cum_return_pct ?? null;

    return {
      name: s.accountName,
      data: dates.map((d) => {
        const p = byDate.get(d);
        if (!p || p.cum_return_pct == null) return null;
        if (anchorDate && d < anchorDate) return null;
        return rebasePct(p.cum_return_pct, anchorCum);
      }),
    };
  });

  const hasBench = seriesList.some((s) => s.points.some((p) => p.benchmark_return_pct != null));
  let benchData: (number | null)[] | null = null;

  if (hasBench) {
    if (mode === 'absolute') {
      benchData = dates.map((d) => findBenchAtDate(seriesList, d));
    } else if (overlapStart) {
      const anchorBench = findBenchAtDate(seriesList, overlapStart);
      benchData = dates.map((d) => {
        const raw = findBenchAtDate(seriesList, d);
        return rebasePct(raw, anchorBench);
      });
    }
  }

  return { dates, overlapStart, portfolioSeries, benchData };
}
