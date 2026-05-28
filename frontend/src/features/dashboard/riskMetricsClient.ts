import type { EquityPoint, OverviewMetrics } from '../../types';

/** 前端风险指标（与后端 risk_metrics.py 算法一致，供回测等场景） */
export function computeRiskMetricsFromCurve(curve: EquityPoint[]): Partial<OverviewMetrics> {
  if (curve.length < 2) return {};

  const navSeries: { date: string; nav: number }[] = [];
  const dailyReturns: number[] = [];

  for (const p of curve) {
    if (typeof p.nav === 'number' && p.nav > 0) {
      navSeries.push({ date: p.trade_date.slice(0, 10), nav: p.nav });
    }
    if (p.period_return_pct != null && Number.isFinite(p.period_return_pct)) {
      dailyReturns.push(p.period_return_pct);
    }
  }

  if (navSeries.length < 2) return {};

  let peak = navSeries[0].nav;
  let peakDate = navSeries[0].date;
  let maxDd = 0;
  let ddStart = peakDate;
  let ddEnd = peakDate;
  let currentPeakDate = peakDate;

  for (const { date, nav } of navSeries) {
    if (nav > peak) {
      peak = nav;
      currentPeakDate = date;
    }
    const dd = peak > 0 ? (nav / peak - 1) * 100 : 0;
    if (dd < maxDd) {
      maxDd = dd;
      ddStart = currentPeakDate;
      ddEnd = date;
    }
  }

  const firstNav = navSeries[0].nav;
  const lastNav = navSeries[navSeries.length - 1].nav;
  const nDays = navSeries.length;
  const totalRet = firstNav > 0 ? lastNav / firstNav - 1 : 0;
  const annualRet = nDays > 0 ? ((1 + totalRet) ** (252 / nDays) - 1) * 100 : 0;

  let volatilityPct: number | null = null;
  if (dailyReturns.length >= 2) {
    const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
    const variance = dailyReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
    volatilityPct = Math.round(Math.sqrt(variance) * Math.sqrt(252) * 100) / 100;
  }

  const sharpeRatio =
    volatilityPct != null && volatilityPct > 1e-9 ? Math.round((annualRet / volatilityPct) * 100) / 100 : null;
  const calmarRatio = maxDd < -1e-9 ? Math.round((annualRet / Math.abs(maxDd)) * 100) / 100 : null;
  const positiveDayRatio =
    dailyReturns.length > 0
      ? Math.round((dailyReturns.filter((r) => r > 0).length / dailyReturns.length) * 1000) / 10
      : null;

  return {
    max_drawdown_pct: Math.round(maxDd * 100) / 100,
    max_drawdown_start: ddStart,
    max_drawdown_end: ddEnd,
    volatility_pct: volatilityPct,
    sharpe_ratio: sharpeRatio,
    calmar_ratio: calmarRatio,
    positive_day_ratio: positiveDayRatio,
  };
}
