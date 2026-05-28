from __future__ import annotations

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


class DiscoverPortfoliosRequest(BaseModel):
    """catalog：从 cube_catalog 抽样（默认）；random/sequential 为旧模式。"""

    scan_mode: str = Field("catalog", description="catalog | random | sequential")
    batch_size: int = Field(30, ge=1, le=500, description="本批抽样数量")
    profiles: list[str] = Field(
        default_factory=lambda: ["mature_scaled", "young_high_return"],
        description="启用的画像：mature_scaled | young_high_return",
    )
    max_rebalance_per_month: int = Field(4, ge=1, le=30, description="近 3 个月每月调仓事件上限")
    nav_threshold_5y: float = Field(6.0, description="成立满 5 年时的净值门槛")
    nav_threshold_10y: float = Field(40.0, description="成立满 10 年时的净值门槛")
    young_min_cum_pct: float = Field(300.0, description="成立不足 5 年累计收益 % 下限")
    max_inactive_days: int | None = Field(90, description="最近 N 天无成功调仓则排除")
    exclude_followed: bool = Field(True, description="跳过已在库中的组合")
    zh_num_lo: int = Field(1_000_000, ge=1, description="[random] ZH 数字下限")
    zh_num_hi: int = Field(3_565_914, ge=1, description="[random] ZH 数字上限")
    zh_num_start: int | None = Field(None, ge=1, description="[sequential] 下一批起始数字")
    zh_num_end_goal: int | None = Field(None, ge=1, description="[sequential] 爬取目标上限")
    zh_num_min: int | None = Field(None, ge=1, description="[sequential] 区间下限")
    zh_num_max: int | None = Field(None, ge=1, description="[sequential] 区间上限")
    step: int = Field(1, ge=1, le=1000)
    max_scan: int = Field(80, ge=1, le=500)
    min_nav: float | None = Field(None, description="已废弃，保留兼容")
    min_cum_return_pct: float | None = Field(None)
    max_cum_return_pct: float | None = Field(None)

    @model_validator(mode="after")
    def _check_scan_mode(self) -> DiscoverPortfoliosRequest:
        if self.scan_mode == "sequential":
            if self.zh_num_start is not None:
                return self
            if self.zh_num_min is not None and self.zh_num_max is not None:
                return self
            raise ValueError("sequential 模式需提供 zh_num_start 或 zh_num_min + zh_num_max")
        if self.scan_mode == "random" and self.zh_num_lo > self.zh_num_hi:
            raise ValueError("zh_num_lo 不能大于 zh_num_hi")
        return self


class DiscoveredPortfolioItem(BaseModel):
    account_code: str
    account_name: str
    latest_nav: float
    cum_return_pct: float
    latest_nav_date: str | None = None
    latest_trade_time: str | None = None
    already_followed: bool = False
    matched_profiles: list[str] = Field(default_factory=list)
    required_nav_threshold: float | None = None
    inception_days: int | None = None
    inception_years: float | None = None


class DiscoverPortfoliosResponse(BaseModel):
    scanned: int
    matched_count: int
    not_found: int
    filtered_out: int
    items: list[DiscoveredPortfolioItem]
    scan_mode: str | None = None
    catalog_pool_size: int | None = None
    catalog_discovered_count: int | None = None
    catalog_remaining_count: int | None = None
    batch_start: int | None = None
    batch_end: int | None = None
    last_scanned_num: int | None = None
    next_checkpoint: int | None = None


class FollowPortfoliosRequest(BaseModel):
    account_codes: list[str] = Field(..., min_length=1)
    sync_after_follow: bool = Field(True, description="保留字段；服务端关注后始终全量同步")


class FollowPortfoliosResponse(BaseModel):
    ok: bool
    followed_count: int
    message: str
    account_codes: list[str]
    errors: list[str] = Field(default_factory=list)


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
    mark_price: float
    mark_price_hfq: float | None = None
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


class CopyBacktestRequest(BaseModel):
    initial_capital: float = 100_000.0
    max_stock_pct: float = 20.0
    star_unlock_profit: float = 500_000.0
    lot_size: int = 100
    min_new_position_pct: float = 1.0
    allow_star_market: bool = False


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
    skipped_lot: int
    skipped_small: int
    trade_log_count: int
    star_unlocked: bool
    star_unlock_profit: float
    max_stock_pct: float
    lot_size: int
    min_new_position_pct: float
    allow_star_market: bool = False
    source_stats: dict[str, int]
    positions: list[CopyBacktestPosition]
    equity_curve: list[CopyBacktestEquityPoint]
    trade_logs: list[CopyBacktestTradeLog]


class CubeCatalogStatsResponse(BaseModel):
    total_count: int
    discovered_count: int = 0
    remaining_count: int = 0
    last_updated_at: str | None = None


class ResetCubeCatalogDiscoverResponse(BaseModel):
    ok: bool
    message: str
    reset_count: int = 0


class SyncCubeCatalogResponse(BaseModel):
    ok: bool
    message: str
    fetched_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    total_count: int = 0
    sources_ok: list[str] = Field(default_factory=list)
    sources_skipped: list[str] = Field(default_factory=list)
    logs: list[dict[str, str]] = Field(default_factory=list)
