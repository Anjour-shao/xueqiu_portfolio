from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class AccountItem(BaseModel):
    id: str
    name: str
    internal_id: int


class OverviewMetrics(BaseModel):
    trade_count: int
    stock_count: int
    realized_events: int
    win_rate: float
    avg_trade_return_pct: float
    cum_return_pct: float
    realized_return_pct: float
    unrealized_return_pct: float
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    latest_trade_time: str | None
    holding_count: int
    rebalance_event_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    max_trade_return_pct: float | None = None
    min_trade_return_pct: float | None = None
    best_stock_name: str | None = None
    best_stock_return_pct: float | None = None
    worst_stock_name: str | None = None
    worst_stock_return_pct: float | None = None
    total_realized_contrib_pct: float = 0.0
    final_nav: float = 1.0
    engine_version: str = "unknown"
    nav_source: str | None = None
    max_drawdown_pct: float | None = None
    max_drawdown_start: str | None = None
    max_drawdown_end: str | None = None
    volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    calmar_ratio: float | None = None
    positive_day_ratio: float | None = None


class EquityHoldingItem(BaseModel):
    stock_name: str
    weight: float


class EquityTradeTodayItem(BaseModel):
    stock_name: str
    action: str
    from_weight: float
    to_weight: float


class EquityPoint(BaseModel):
    trade_date: str
    trade_time: str
    cum_return_pct: float
    realized_return_pct: float = 0.0
    unrealized_return_pct: float = 0.0
    event_count: int = 0
    nav: float | None = None
    nav_before: float | None = None
    period_return_pct: float | None = None
    period_benchmark_return_pct: float | None = None
    period_excess_return_pct: float | None = None
    growth_attribution_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    is_latest_mark: bool = False
    nav_source: str | None = None
    holdings: list[EquityHoldingItem] = []
    trades_today: list[EquityTradeTodayItem] = []
    benchmark_daily_pct: float | None = None


class PortfolioOverviewItem(BaseModel):
    account_code: str
    account_name: str
    cum_return_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    holding_count: int = 0
    latest_trade_time: str | None = None
    latest_nav_date: str | None = None
    nav_source: str | None = None


class PortfoliosOverviewResponse(BaseModel):
    items: list[PortfolioOverviewItem]


class FreshnessSection(BaseModel):
    latest_trade_time: str | None = None
    latest_date: str | None = None
    latest_date_raw: str | None = None
    trade_count: int | None = None
    symbol_count: int | None = None
    point_count: int | None = None
    ts_code: str | None = None
    latest_date_min: str | None = None
    latest_date_max: str | None = None
    latest_date_max_raw: str | None = None
    account_count: int | None = None
    zh_account_count: int | None = None
    stale_accounts: int | None = None
    status: str = "empty"


class DataFreshnessResponse(BaseModel):
    as_of: str
    stale_threshold_days: int
    rebalance: FreshnessSection
    quotes: FreshnessSection
    benchmark: FreshnessSection
    cube_nav: FreshnessSection


class OverviewSummaryStats(BaseModel):
    portfolio_count: int
    avg_cum_return_pct: float | None = None
    beat_benchmark_count: int = 0
    beat_benchmark_ratio: float = 0
    traded_today_count: int = 0
    freshness: DataFreshnessResponse


class OverviewWatchItem(BaseModel):
    account_code: str
    account_name: str
    reasons: list[str]
    latest_nav_date: str | None = None
    cum_return_pct: float | None = None


class PortfoliosOverviewStatsResponse(BaseModel):
    summary: OverviewSummaryStats
    top_performers: list[PortfolioOverviewItem]
    bottom_performers: list[PortfolioOverviewItem]
    watchlist: list[OverviewWatchItem]
    items: list[PortfolioOverviewItem]


class DeleteAccountResponse(BaseModel):
    ok: bool
    account_code: str
    trades_deleted: int
    nav_points_deleted: int
    message: str


class PositionItem(BaseModel):
    ts_code: str
    stock_name: str
    last_action: str
    current_weight: float
    avg_cost: float | None = None
    mark_price: float | None = None
    avg_cost_hfq: float | None
    mark_price_hfq: float | None
    return_pct: float | None
    trade_count: int
    last_trade_time: str | None
    latest_quote_date: str | None = None
    is_holding: bool = True
    holding_days: int | None = None
    holding_opened_at: str | None = None


class TradeItem(BaseModel):
    id: int
    trade_time: str
    stock_name: str
    ts_code: str
    action: str
    from_weight: float
    to_weight: float
    weight_delta: float
    price: float | None
    price_hfq: float | None = None
    return_pct: float | None = None
    leg_return_pct: float | None = None
    account_contrib_pct: float | None = None


class GroupedStatItem(BaseModel):
    ts_code: str
    stock_name: str
    events: int
    realized_count: int
    wins: int
    losses: int
    win_rate: float
    cum_return_pct: float
    avg_return_pct: float | None = None
    last_trade_time: str | None
    is_holding: bool = False
    holding_days: int | None = None
    holding_opened_at: str | None = None


class ImportedTradeItem(BaseModel):
    trade_time: str
    stock_name: str
    ts_code: str
    from_weight: float
    to_weight: float
    weight_delta: float
    price: float | None = None
    action: str
    raw_block: str = Field(min_length=1)


class ImportRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    account_name: str = Field(min_length=1, max_length=255)
    trades: list[ImportedTradeItem]


class ImportResponse(BaseModel):
    account_id: str
    account_name: str
    inserted_count: int
    skipped_duplicates: int
    total_received: int


class RecomputeResponse(BaseModel):
    account: str
    trade_count: int
    cum_return_pct: float
    realized_return_pct: float
    unrealized_return_pct: float
    holding_count: int


class SyncLatestHfqResponse(BaseModel):
    account: str
    synced_count: int
    holding_count: int
    message: str
    overview: OverviewMetrics


class SyncLogItem(BaseModel):
    level: str
    message: str


class SyncTradeResultItem(BaseModel):
    stock_name: str
    ts_code: str
    action: str
    from_weight: float
    to_weight: float
    price: float | None = None
    status: str


class SyncXueqiuResponse(BaseModel):
    ok: bool = True
    account_id: str
    account_name: str
    rebalance_time: str | None = None
    crawled_count: int = 0
    inserted_count: int = 0
    skipped_duplicates: int = 0
    total_received: int = 0
    db_trade_count: int = 0
    db_latest_trade_time: str | None = None
    logs: list[SyncLogItem] = []
    trade_results: list[SyncTradeResultItem] = []
    adj_sync_ok: bool = True
    nav_sync_ok: bool = True
    message: str = ""
    overview: OverviewMetrics | None = None


class SyncXueqiuAllResponse(BaseModel):
    account_count: int
    failed_count: int
    total_inserted: int
    total_crawled: int
    logs: list[SyncLogItem]
    accounts: list[SyncXueqiuResponse]
    message: str


class SyncQuotesResponse(BaseModel):
    ok: bool = True
    message: str
    logs: list[SyncLogItem] = []
    hfq_count: int = 0
    benchmark_count: int = 0


class SyncCubeNavResultItem(BaseModel):
    ok: bool = False
    account_code: str | None = None
    account_name: str | None = None
    point_count: int | None = None
    latest_date: str | None = None
    error: str | None = None


class SyncCubeNavAllResponse(BaseModel):
    ok: bool = True
    message: str
    logs: list[SyncLogItem] = []
    account_count: int = 0
    ok_count: int = 0
    failed_count: int = 0
    results: list[SyncCubeNavResultItem] = []


class DashboardPayload(BaseModel):
    account: str
    overview: OverviewMetrics
    equity_curve: list[EquityPoint]
    positions: list[PositionItem]
    recent_trades: list[TradeItem]
    grouped_stats: list[GroupedStatItem]
    nav_source: str | None = None


class CopyBacktestEquityPoint(BaseModel):
    trade_time: str
    total_nav: float
    total_nav_hfq: float
    cum_return_pct: float
    profit: float
    profit_hfq: float


class CopyBacktestPosition(BaseModel):
    ts_code: str
    stock_name: str
    qty: float
    avg_cost: float | None = None
    mark_price: float | None = None
    avg_cost_hfq: float | None = None
    mark_price_hfq: float | None = None
    return_pct: float | None = None
    return_pct_hfq: float | None = None
    return_pct_raw: float | None = None
    value: float
    weight_pct: float


class CopyBacktestTradeLog(BaseModel):
    trade_time: str
    source_portfolio: str
    source_name: str
    stock_name: str
    ts_code: str
    master_from: float | str | None = None
    master_to: float | str | None = None
    action: str
    price: float
    price_hfq: float | None = None
    qty_delta: float
    our_weight_pct: float
    nav_after: float
    note: str = ""
    trigger: str | None = None
    leg_return_pct: float | None = None
    slice_qty_before: float | None = None
    slice_qty_after: float | None = None
    physical_qty: float | None = None


class CopyBacktestRequest(BaseModel):
    initial_capital: float = 1_000_000.0
    max_stock_pct: float = 20.0
    min_new_position_pct: float = 1.0
    max_positions: int = 5
    strategy_id: str = "route_f_partition_mimic"
    start_date: str | None = None
    end_date: str | None = None


class StrategyCatalogItem(BaseModel):
    id: str
    label: str
    description: str
    style: str


class StrategyCompareSummary(BaseModel):
    strategy_id: str | None = None
    label: str | None = None
    description: str | None = None
    style: str | None = None
    return_pct: float | None = None
    return_since_entry: float | None = None
    entry_date: str | None = None
    return_since_2020: float | None = None
    return_since_2023: float | None = None
    max_drawdown_pct: float | None = None
    sharpe_proxy: float | None = None
    cash_pct: float | None = None
    position_count: int | None = None
    orphan_sell_count: int | None = None
    rotate_count: int | None = None
    rebalance_count: int | None = None
    final_nav_hfq: float | None = None
    current_leader: str | None = None
    leader_switches: int | None = None


class EntrySweepItem(BaseModel):
    date: str
    strategy_id: str
    label: str | None = None
    return_pct: float | None = None
    max_drawdown_pct: float | None = None
    cash_pct: float | None = None
    position_count: int | None = None


class StrategyCompareRequest(BaseModel):
    initial_capital: float = 1_000_000.0
    strategy_ids: list[str]
    start_date: str | None = None
    end_date: str | None = None
    entry_sweep_dates: list[str] | None = None


class StrategyCompareResponse(BaseModel):
    initial_capital: float
    start_date: str | None = None
    end_date: str | None = None
    results: list[StrategyCompareSummary]
    consensus_stats: dict[str, Any] = {}
    entry_sweep: list[EntrySweepItem] = []


class CopyBacktestResponse(BaseModel):
    initial_capital: float
    final_nav: float
    final_nav_hfq: float
    profit: float
    profit_hfq: float
    return_pct: float
    return_pct_raw: float
    cash: float
    cash_pct: float
    portfolio_count: int
    start_time: str
    end_time: str
    blocked_688: int
    cap_triggers: int
    rotate_triggers: int = 0
    rebalance_triggers: int = 0
    skipped_lot: int
    skipped_small: int
    trade_log_count: int
    star_unlocked: bool
    max_stock_pct: float
    min_new_position_pct: float
    max_positions: int
    overview_win_rate: float = 0.0
    diagnostics: dict[str, Any] = {}
    source_stats: dict[str, int]
    positions: list[CopyBacktestPosition]
    equity_curve: list[CopyBacktestEquityPoint]
    trade_logs: list[CopyBacktestTradeLog]
    grouped_stats: list[GroupedStatItem] = []


class DiscoveryStatsResponse(BaseModel):
    total_count: int
    auto_pass_count: int = 0
    pending_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0
    imported_count: int = 0


class MinedCubeItem(BaseModel):
    account_code: str
    account_name: str
    owner_uid: int | None = None
    owner_name: str | None = None
    source_user_uid: int | None = None
    source_account_code: str | None = None
    source_type: str | None = None
    source_symbol: str | None = None
    depth: int = 1
    cum_return_pct: float | None = None
    nav_latest_date: str | None = None
    has_non_a_share: bool = False
    auto_pass: bool = False
    reject_reasons: list[str] = Field(default_factory=list)
    selected: int | None = None
    note: str | None = None
    imported_at: str | None = None
    first_seen_at: str | None = None
    updated_at: str | None = None


class MinedCubeListResponse(BaseModel):
    items: list[MinedCubeItem]


class DiscoveryMineRequest(BaseModel):
    max_depth: int = 1
    modes: list[str] = Field(default_factory=lambda: ["watchlist"])


class DiscoverySymbolPoolItem(BaseModel):
    symbol: str
    stock_name: str | None = None
    note: str | None = None
    enabled: bool = True
    sort_order: int = 0
    is_builtin: bool = False
    volume_rank_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DiscoverySymbolPoolMeta(BaseModel):
    total_count: int
    enabled_count: int
    volume_rank_date: str | None = None


class DiscoverySymbolPoolResponse(BaseModel):
    meta: DiscoverySymbolPoolMeta
    items: list[DiscoverySymbolPoolItem]


class ReplaceDiscoverySymbolPoolRequest(BaseModel):
    items: list[DiscoverySymbolPoolItem]


class DiscoveryImportRequest(BaseModel):
    account_codes: list[str] = Field(default_factory=list)


class DiscoveryMineResponse(BaseModel):
    ok: bool
    message: str
    stats: dict[str, int] = Field(default_factory=dict)


class UpdateMinedCubeRequest(BaseModel):
    selected: int | None = None
    note: str | None = None


class ImportMinedCubeResponse(BaseModel):
    ok: bool
    message: str
    account_code: str
    sync: dict[str, Any] = Field(default_factory=dict)


class DiscoveryCubePreviewHolding(BaseModel):
    stock_name: str
    symbol: str
    weight: float


class DiscoveryCubePreviewTrade(BaseModel):
    action: str
    stock_name: str
    symbol: str
    weight_change: str


class DiscoveryCubePreviewRebalance(BaseModel):
    trade_time: str | None = None
    trades: list[DiscoveryCubePreviewTrade] = Field(default_factory=list)


class DiscoveryCubePreviewRecentRebalance(BaseModel):
    trade_time: str
    actions: list[str] = Field(default_factory=list)


class DiscoveryCubePreviewResponse(BaseModel):
    account_code: str
    account_name: str
    owner_name: str | None = None
    description: str | None = None
    market: str | None = None
    created_at: str | None = None
    follower_count: int = 0
    net_value: float | None = None
    total_gain_pct: float | None = None
    monthly_gain_pct: float | None = None
    daily_gain_pct: float | None = None
    annualized_gain_pct: float | None = None
    top_gainer_name: str | None = None
    top_gainer_symbol: str | None = None
    holdings: list[DiscoveryCubePreviewHolding] = Field(default_factory=list)
    latest_rebalance: DiscoveryCubePreviewRebalance = Field(default_factory=DiscoveryCubePreviewRebalance)
    recent_rebalances: list[DiscoveryCubePreviewRecentRebalance] = Field(default_factory=list)
    xueqiu_url: str


class PersonalHoldingItem(BaseModel):
    ts_code: str
    stock_name: str
    shares: int
    cost_price: float
    opened_at: str | None = None
    holding_days: int | None = None
    price: float | None = None
    market_value: float | None = None
    weight_pct: float | None = None
    unrealized_pnl_pct: float | None = None
    unrealized_pnl_amount: float | None = None


class PersonalAccountResponse(BaseModel):
    name: str
    cash: float
    strategy_id: str
    market_value: float
    total_assets: float
    daily_pnl: float
    daily_pnl_pct: float | None = None
    holding_pnl: float
    holding_pnl_pct: float | None = None
    holdings: list[PersonalHoldingItem] = Field(default_factory=list)
    updated_at: str | None = None


class PersonalCashUpdateRequest(BaseModel):
    cash: float


class PersonalStrategyUpdateRequest(BaseModel):
    strategy_id: str


class PersonalTradeRequest(BaseModel):
    action: str
    ts_code: str
    shares: int
    price: float | None = None
    stock_name: str | None = None


class CopyRebalanceAction(BaseModel):
    action: str
    ts_code: str
    stock_name: str
    shares_delta: int
    current_shares: int
    target_shares: int
    current_weight_pct: float
    target_weight_pct: float
    price: float | None = None
    amount: float | None = None


class CopyRebalancePlanResponse(BaseModel):
    strategy_id: str
    strategy_label: str
    total_assets: float
    sim_capital: float | None = None
    actions: list[CopyRebalanceAction] = Field(default_factory=list)
    note: str = ""
