import type { EquityPoint } from '../../types';

export type CalendarCell = {
  date: Date;
  dateKey: string;
  inMonth: boolean;
  isToday: boolean;
  isWeekend: boolean;
  periodReturnPct: number | null;
  nav: number | null;
};

export type MonthSummary = {
  periodReturnPct: number | null;
  tradingDays: number;
};

const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

export function weekdayLabels() {
  return WEEKDAY_LABELS;
}

export function toDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function parseTradeDate(tradeDate: string): Date {
  const [y, m, d] = tradeDate.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function buildPointsByDate(points: EquityPoint[]): Map<string, EquityPoint> {
  const map = new Map<string, EquityPoint>();
  for (const p of points) {
    if (p.trade_date) map.set(p.trade_date.slice(0, 10), p);
  }
  return map;
}

export function buildMonthGrid(year: number, month: number, points: EquityPoint[]): CalendarCell[] {
  const byDate = buildPointsByDate(points);
  const first = new Date(year, month, 1);
  const startOffset = first.getDay();
  const gridStart = new Date(year, month, 1 - startOffset);
  const todayKey = toDateKey(new Date());
  const cells: CalendarCell[] = [];

  for (let i = 0; i < 42; i += 1) {
    const date = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const dateKey = toDateKey(date);
    const inMonth = date.getMonth() === month;
    const day = date.getDay();
    const isWeekend = day === 0 || day === 6;
    const point = byDate.get(dateKey);
    cells.push({
      date,
      dateKey,
      inMonth,
      isToday: dateKey === todayKey,
      isWeekend,
      periodReturnPct: point?.period_return_pct ?? null,
      nav: point?.nav ?? null,
    });
  }
  return cells;
}

export function summarizeMonth(points: EquityPoint[], year: number, month: number): MonthSummary {
  const inMonth = points
    .filter((p) => {
      const d = parseTradeDate(p.trade_date);
      return d.getFullYear() === year && d.getMonth() === month;
    })
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date));

  if (!inMonth.length) {
    return { periodReturnPct: null, tradingDays: 0 };
  }

  const withNav = inMonth.filter((p) => typeof p.nav === 'number' && p.nav > 0);
  if (withNav.length >= 2) {
    const first = withNav[0].nav!;
    const last = withNav[withNav.length - 1].nav!;
    const pct = first > 0 ? ((last / first - 1) * 100) : null;
    return { periodReturnPct: pct != null ? Math.round(pct * 100) / 100 : null, tradingDays: inMonth.length };
  }

  return { periodReturnPct: null, tradingDays: inMonth.length };
}

export function summarizeYearMonths(points: EquityPoint[], year: number): { month: number; summary: MonthSummary }[] {
  return Array.from({ length: 12 }, (_, month) => ({
    month,
    summary: summarizeMonth(points, year, month),
  }));
}

export function formatYearMonth(year: number, month: number) {
  return `${year}年 ${month + 1}月`;
}

export function shiftMonth(year: number, month: number, delta: number): { year: number; month: number } {
  const d = new Date(year, month + delta, 1);
  return { year: d.getFullYear(), month: d.getMonth() };
}

export function hasOfficialNavSeries(points: EquityPoint[]) {
  return points.some((p) => p.nav_source === 'official');
}

/** 官方净值或具备足够日涨跌序列时均可展示盈亏日历 */
export function hasCalendarSeries(points: EquityPoint[]) {
  if (hasOfficialNavSeries(points)) return true;
  const withDaily = points.filter((p) => p.period_return_pct != null).length;
  return withDaily >= 5;
}

function filterPeriodPoints(points: EquityPoint[], year: number, month?: number) {
  return points
    .filter((p) => {
      const d = parseTradeDate(p.trade_date);
      if (d.getFullYear() !== year) return false;
      if (month != null && d.getMonth() !== month) return false;
      return true;
    })
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date));
}

/** 上证累计收益区间涨跌（首尾 benchmark_return_pct 复合） */
export function summarizeBenchmarkPeriod(points: EquityPoint[], year: number, month?: number): number | null {
  const inPeriod = filterPeriodPoints(points, year, month).filter(
    (p) => p.benchmark_return_pct != null && Number.isFinite(p.benchmark_return_pct),
  );
  if (inPeriod.length < 2) return null;
  const start = inPeriod[0].benchmark_return_pct!;
  const end = inPeriod[inPeriod.length - 1].benchmark_return_pct!;
  const pct = (1 + end / 100) / (1 + start / 100) - 1;
  return Math.round(pct * 10000) / 100;
}
