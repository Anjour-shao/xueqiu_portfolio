export interface AccountItem {
  id: string;
  name: string;
  internal_id: number;
}

export interface OverviewMetrics {
  trade_count: number;
  stock_count: number;
  realized_events: number;
  win_rate: number;
  avg_trade_return_pct: number;
  cum_return_pct: number;
  realized_return_pct: number;
  unrealized_return_pct: number;
  benchmark_return_pct?: number | null;
  excess_return_pct?: number | null;
  latest_trade_time: string | null;
  holding_count: number;
  rebalance_event_count?: number;
  buy_count?: number;
  sell_count?: number;
  max_trade_return_pct?: number | null;
  min_trade_return_pct?: number | null;
  best_stock_name?: string | null;
  best_stock_return_pct?: number | null;
  worst_stock_name?: string | null;
  worst_stock_return_pct?: number | null;
  total_realized_contrib_pct?: number;
  engine_version?: string;
  nav_source?: 'official' | 'pseudo';
  max_drawdown_pct?: number | null;
  max_drawdown_start?: string | null;
  max_drawdown_end?: string | null;
  volatility_pct?: number | null;
  sharpe_ratio?: number | null;
  calmar_ratio?: number | null;
  positive_day_ratio?: number | null;
}

export interface EquityHoldingItem {
  stock_name: string;
  weight: number;
}

export interface EquityTradeTodayItem {
  stock_name: string;
  action: string;
  from_weight: number;
  to_weight: number;
}

export interface EquityPoint {
  trade_date: string;
  trade_time: string;
  cum_return_pct: number;
  realized_return_pct?: number;
  unrealized_return_pct?: number;
  event_count?: number;
  nav?: number;
  nav_before?: number;
  period_return_pct?: number;
  period_benchmark_return_pct?: number | null;
  period_excess_return_pct?: number | null;
  growth_attribution_pct?: number | null;
  benchmark_return_pct?: number | null;
  excess_return_pct?: number | null;
  is_latest_mark?: boolean;
  nav_source?: 'official' | 'pseudo';
  holdings?: EquityHoldingItem[];
  trades_today?: EquityTradeTodayItem[];
  benchmark_daily_pct?: number | null;
}

export interface PortfolioOverviewItem {
  account_code: string;
  account_name: string;
  cum_return_pct?: number | null;
  benchmark_return_pct?: number | null;
  excess_return_pct?: number | null;
  holding_count: number;
  latest_trade_time?: string | null;
  latest_nav_date?: string | null;
  nav_source?: string | null;
}

export interface PortfoliosOverviewResponse {
  items: PortfolioOverviewItem[];
}

export interface FreshnessSection {
  latest_trade_time?: string | null;
  latest_date?: string | null;
  latest_date_raw?: string | null;
  trade_count?: number | null;
  symbol_count?: number | null;
  point_count?: number | null;
  ts_code?: string | null;
  latest_date_min?: string | null;
  latest_date_max?: string | null;
  latest_date_max_raw?: string | null;
  account_count?: number | null;
  zh_account_count?: number | null;
  stale_accounts?: number | null;
  status: string;
}

export interface DataFreshnessResponse {
  as_of: string;
  stale_threshold_days: number;
  rebalance: FreshnessSection;
  quotes: FreshnessSection;
  benchmark: FreshnessSection;
  cube_nav: FreshnessSection;
}

export interface OverviewWatchItem {
  account_code: string;
  account_name: string;
  reasons: string[];
  latest_nav_date?: string | null;
  cum_return_pct?: number | null;
}

export interface PortfoliosOverviewStatsResponse {
  summary: {
    portfolio_count: number;
    avg_cum_return_pct?: number | null;
    beat_benchmark_count: number;
    beat_benchmark_ratio: number;
    traded_today_count: number;
    freshness: DataFreshnessResponse;
  };
  top_performers: PortfolioOverviewItem[];
  bottom_performers: PortfolioOverviewItem[];
  watchlist: OverviewWatchItem[];
  items: PortfolioOverviewItem[];
}

export interface CopyBacktestRequest {
  initial_capital: number;
  max_stock_pct: number;
  star_unlock_profit: number;
  lot_size: number;
  min_new_position_pct: number;
  allow_star_market: boolean;
}

export interface PositionItem {
  ts_code: string;
  stock_name: string;
  last_action: string;
  current_weight: number;
  avg_cost_hfq: number | null;
  mark_price_hfq: number | null;
  return_pct: number | null;
  trade_count: number;
  last_trade_time: string | null;
  latest_quote_date?: string | null;
  is_holding?: boolean;
  holding_days?: number | null;
  holding_opened_at?: string | null;
}

export interface TradeItem {
  id: number;
  trade_time: string;
  stock_name: string;
  ts_code: string;
  action: string;
  from_weight: number;
  to_weight: number;
  weight_delta: number;
  price: number | null;
  price_hfq?: number | null;
  return_pct?: number | null;
  /** 相对持仓均价的单笔收益率（未乘权重） */
  leg_return_pct?: number | null;
  account_contrib_pct?: number | null;
}

export interface GroupedStatItem {
  ts_code: string;
  stock_name: string;
  events: number;
  realized_count: number;
  wins: number;
  losses: number;
  win_rate: number;
  cum_return_pct: number;
  /** @deprecated 与 cum_return_pct 相同，兼容旧后端 */
  avg_return_pct?: number;
  last_trade_time: string | null;
  is_holding?: boolean;
  holding_days?: number | null;
  holding_opened_at?: string | null;
}

export interface DashboardPayload {
  account: string;
  overview: OverviewMetrics;
  equity_curve: EquityPoint[];
  positions: PositionItem[];
  recent_trades: TradeItem[];
  grouped_stats: GroupedStatItem[];
  nav_source?: 'official' | 'pseudo';
}

export interface ParsedTradeItem {
  trade_time: string;
  stock_name: string;
  ts_code: string;
  from_weight: number;
  to_weight: number;
  weight_delta: number;
  price: number | null;
  action: 'BUY' | 'SELL' | 'INCREASE' | 'DECREASE' | 'HOLD';
  raw_block: string;
}

export interface ImportLogsPayload {
  account_id: string;
  account_name: string;
  trades: ParsedTradeItem[];
}

export interface ImportLogsResponse {
  account_id: string;
  account_name: string;
  inserted_count: number;
  skipped_duplicates: number;
  total_received: number;
}

export interface SyncLatestHfqResponse {
  account: string;
  synced_count: number;
  holding_count: number;
  message: string;
  overview: OverviewMetrics;
}

export interface SyncLogItem {
  level: 'info' | 'success' | 'warn' | 'error' | string;
  message: string;
}

export interface CubeCatalogStats {
  total_count: number;
  discovered_count: number;
  remaining_count: number;
  last_updated_at: string | null;
}

export interface SyncTradeResultItem {
  stock_name: string;
  ts_code: string;
  action: string;
  from_weight: number;
  to_weight: number;
  price: number | null;
  status: 'inserted' | 'skipped' | 'failed' | string;
}

export interface SyncXueqiuResponse {
  ok?: boolean;
  account_id: string;
  account_name: string;
  rebalance_time?: string | null;
  crawled_count?: number;
  inserted_count: number;
  skipped_duplicates?: number;
  total_received?: number;
  db_trade_count?: number;
  db_latest_trade_time?: string | null;
  logs: SyncLogItem[];
  trade_results: SyncTradeResultItem[];
  adj_sync_ok?: boolean;
  nav_sync_ok?: boolean;
  message: string;
  overview?: OverviewMetrics;
}

export interface SyncXueqiuAllResponse {
  account_count: number;
  failed_count: number;
  total_inserted: number;
  total_crawled: number;
  logs: SyncLogItem[];
  accounts: SyncXueqiuResponse[];
  message: string;
}

export interface SyncQuotesResponse {
  ok: boolean;
  message: string;
  logs: SyncLogItem[];
  hfq_count: number;
  benchmark_count: number;
}

export interface SyncCubeNavResultItem {
  ok: boolean;
  account_code?: string | null;
  account_name?: string | null;
  point_count?: number | null;
  latest_date?: string | null;
  error?: string | null;
}

export interface SyncCubeNavAllResponse {
  ok: boolean;
  message: string;
  logs: SyncLogItem[];
  account_count: number;
  ok_count: number;
  failed_count: number;
  results: SyncCubeNavResultItem[];
}

export interface CopyBacktestEquityPoint {
  trade_time: string;
  total_nav: number;
  total_nav_hfq: number;
  cum_return_pct: number;
  profit: number;
  profit_hfq: number;
}

export interface CopyBacktestPosition {
  ts_code: string;
  stock_name: string;
  qty: number;
  mark_price: number;
  mark_price_hfq?: number | null;
  value: number;
  weight_pct: number;
}

export interface CopyBacktestTradeLog {
  trade_time: string;
  source_portfolio: string;
  source_name: string;
  stock_name: string;
  ts_code: string;
  master_from?: number | string | null;
  master_to?: number | string | null;
  action: string;
  price: number;
  price_hfq?: number | null;
  qty_delta: number;
  our_weight_pct: number;
  nav_after: number;
  note?: string;
}

export interface CopyBacktestResponse {
  initial_capital: number;
  final_nav: number;
  final_nav_hfq: number;
  profit: number;
  profit_hfq: number;
  return_pct: number;
  return_pct_raw: number;
  cash: number;
  cash_pct: number;
  portfolio_count: number;
  start_time: string;
  end_time: string;
  blocked_688: number;
  cap_triggers: number;
  skipped_lot: number;
  skipped_small: number;
  trade_log_count: number;
  star_unlocked: boolean;
  star_unlock_profit: number;
  max_stock_pct: number;
  lot_size: number;
  min_new_position_pct: number;
  allow_star_market?: boolean;
  source_stats: Record<string, number>;
  positions: CopyBacktestPosition[];
  equity_curve: CopyBacktestEquityPoint[];
  trade_logs: CopyBacktestTradeLog[];
}

export interface DiscoverPortfoliosParams {
  scan_mode?: 'catalog' | 'random' | 'sequential';
  batch_size?: number;
  /** 留空本批数量时：连续多批直到停止 */
  continuous?: boolean;
  profiles?: string[];
  max_rebalance_per_month?: number;
  nav_threshold_5y?: number;
  nav_threshold_10y?: number;
  young_min_cum_pct?: number;
  max_inactive_days?: number | null;
  exclude_followed?: boolean;
}

export interface DiscoveredPortfolioItem {
  account_code: string;
  account_name: string;
  latest_nav: number;
  cum_return_pct: number;
  latest_nav_date?: string | null;
  latest_trade_time?: string | null;
  already_followed?: boolean;
  matched_profiles?: string[];
  required_nav_threshold?: number | null;
  inception_days?: number | null;
  inception_years?: number | null;
}

export interface DiscoverPortfoliosResponse {
  scanned: number;
  matched_count: number;
  not_found: number;
  filtered_out: number;
  items: DiscoveredPortfolioItem[];
  scan_mode?: string | null;
  catalog_pool_size?: number | null;
  catalog_discovered_count?: number | null;
  catalog_remaining_count?: number | null;
  batch_start?: number | null;
  batch_end?: number | null;
  last_scanned_num?: number | null;
  next_checkpoint?: number | null;
}

export type DiscoverLogLevel = 'info' | 'success' | 'warn' | 'error';

export interface DiscoverLogItem {
  level: DiscoverLogLevel;
  message: string;
}

export type DiscoverStreamEvent =
  | { type: 'progress'; current: number; total: number; code: string }
  | { type: 'skip'; code: string; reason: string; preview?: DiscoveredPortfolioItem }
  | { type: 'hit'; item: DiscoveredPortfolioItem }
  | { type: 'log'; level?: DiscoverLogLevel; message: string }
  | ({ type: 'done'; ok: boolean; message?: string } & Partial<DiscoverPortfoliosResponse>);

export interface FollowPortfoliosResponse {
  ok: boolean;
  followed_count: number;
  message: string;
  account_codes: string[];
  errors: string[];
}

export interface DeleteAccountResponse {
  ok: boolean;
  account_code: string;
  trades_deleted: number;
  nav_points_deleted: number;
  message: string;
}
