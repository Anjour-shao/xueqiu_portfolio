import type { CopyBacktestResponse, DashboardPayload, EquityPoint, GroupedStatItem, OverviewMetrics, PositionItem, TradeItem } from '../../types';
import { computeRiskMetricsFromCurve } from '../dashboard/riskMetricsClient';

function mapPositions(result: CopyBacktestResponse): PositionItem[] {
  return result.positions.map((p) => ({
    ts_code: p.ts_code,
    stock_name: p.stock_name,
    last_action: 'HOLD',
    current_weight: p.weight_pct,
    avg_cost_hfq: p.mark_price_hfq ?? p.mark_price,
    mark_price_hfq: p.mark_price_hfq ?? p.mark_price,
    return_pct: null,
    trade_count: 0,
    last_trade_time: null,
    is_holding: true,
  }));
}

function mapTrades(result: CopyBacktestResponse): TradeItem[] {
  return result.trade_logs.map((t, idx) => ({
    id: idx + 1,
    trade_time: t.trade_time,
    stock_name: t.stock_name,
    ts_code: t.ts_code,
    action: t.action,
    from_weight: typeof t.master_from === 'number' ? t.master_from : Number(t.master_from) || 0,
    to_weight: typeof t.master_to === 'number' ? t.master_to : Number(t.master_to) || 0,
    weight_delta: 0,
    price: t.price,
    price_hfq: t.price_hfq ?? null,
    return_pct: null,
    leg_return_pct: null,
    account_contrib_pct: null,
    nav_pre: t.nav_after,
  }));
}

function buildGroupedStats(trades: TradeItem[]): GroupedStatItem[] {
  const byCode = new Map<string, TradeItem[]>();
  for (const t of trades) {
    const list = byCode.get(t.ts_code) ?? [];
    list.push(t);
    byCode.set(t.ts_code, list);
  }
  const rows: GroupedStatItem[] = [];
  for (const [ts_code, list] of byCode) {
    const sorted = [...list].sort((a, b) => a.trade_time.localeCompare(b.trade_time));
    const last = sorted[sorted.length - 1];
    rows.push({
      ts_code,
      stock_name: last.stock_name,
      events: sorted.length,
      realized_count: sorted.length,
      wins: 0,
      losses: 0,
      win_rate: 0,
      cum_return_pct: 0,
      last_trade_time: last.trade_time,
      holding_days: null,
      is_holding: false,
    });
  }
  return rows.sort((a, b) => b.realized_count - a.realized_count);
}

export function copyBacktestToDashboard(result: CopyBacktestResponse): DashboardPayload {
  const equity_curve: EquityPoint[] = result.equity_curve.map((p) => ({
    trade_date: p.trade_time.slice(0, 10),
    trade_time: p.trade_time,
    cum_return_pct: p.cum_return_pct,
    nav: p.total_nav_hfq,
  }));

  for (let i = 1; i < equity_curve.length; i += 1) {
    const prev = equity_curve[i - 1].nav ?? 1;
    const cur = equity_curve[i].nav ?? prev;
    if (prev > 0) {
      equity_curve[i].period_return_pct = Math.round((cur / prev - 1) * 10000) / 100;
      equity_curve[i].nav_source = 'official';
    }
  }
  if (equity_curve.length > 0) {
    equity_curve[0].nav_source = 'official';
  }

  const risk = computeRiskMetricsFromCurve(equity_curve);
  const positions = mapPositions(result);
  const recent_trades = mapTrades(result);

  const overview: OverviewMetrics = {
    trade_count: result.trade_log_count,
    stock_count: new Set(recent_trades.map((t) => t.ts_code)).size,
    realized_events: recent_trades.length,
    win_rate: 0,
    avg_trade_return_pct: 0,
    cum_return_pct: result.return_pct,
    realized_return_pct: result.return_pct_raw,
    unrealized_return_pct: 0,
    latest_trade_time: result.end_time,
    holding_count: positions.length,
    rebalance_event_count: result.portfolio_count,
    buy_count: recent_trades.filter((t) => t.to_weight > t.from_weight).length,
    sell_count: recent_trades.filter((t) => t.to_weight < t.from_weight).length,
    nav_source: 'official',
    benchmark_return_pct: null,
    excess_return_pct: null,
    ...risk,
  };

  return {
    account: '抄作业模拟',
    overview,
    equity_curve,
    positions,
    recent_trades,
    grouped_stats: buildGroupedStats(recent_trades),
    nav_source: 'official',
  };
}
