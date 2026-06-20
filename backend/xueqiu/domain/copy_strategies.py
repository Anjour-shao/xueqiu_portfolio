"""
多策略抄作业回测：对比 legacy / 稳健共识 / 动态头狼 等方案。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from xueqiu.domain.copy_backtest import (
    INITIAL_CAPITAL,
    STAR_UNLOCK_PROFIT,
    BacktestConfig,
    SliceLedger,
    _build_hfq_prices,
    _build_raw_prices,
    _floor_lots,
    _is_star_market,
    _lot_size,
    _slippage_price,
    _update_mirror,
    _weight_pct,
    enforce_stock_cap,
    load_portfolio_trades,
    resolve_trade_price_raw,
    run_backtest,
)
from xueqiu.domain.copy_conviction import (
    HEAVY_HOLDER_PCT,
    HeavyLegEvent,
    build_heavy_leg_events,
    consensus_trust_for_code,
    conviction_cap_pct,
    portfolio_trust_at,
)
from xueqiu.domain.nav_engine import (
    TradeInput,
    compute_pseudo_nav,
    fmt_trade_date,
    load_adj_map,
    resolve_price_hfq,
)

BOOST_PORTFOLIO = "_增强"


class StrategyId(str, Enum):
    LEGACY_K5 = "legacy_k5"
    LEGACY_K10 = "legacy_k10"
    ROUTE_B_MERGED = "route_b_merged"
    ROUTE_B_MERGED_BOOST = "route_b_merged_boost"
    ROUTE_A_LEADER = "route_a_leader"
    ROUTE_A_LEADER_100 = "route_a_leader_100"
    ROUTE_C_FUNNEL = "route_c_funnel"
    ROUTE_C_BELIEF = "route_c_belief"
    ROUTE_C_WINNER = "route_c_winner"
    ROUTE_C_WEIGHTED = "route_c_weighted"
    ROUTE_D_BOOST_AGGRESSIVE = "route_d_boost_aggressive"
    ROUTE_D_BOOST_CAP12 = "route_d_boost_cap12"
    ROUTE_E_FOF_TOP3_BOOST = "route_e_fof_top3_boost"
    ROUTE_E_LAG_RESONANCE_BOOST = "route_e_lag_resonance_boost"
    ROUTE_E_DECAY_INTENSITY = "route_e_decay_intensity"
    ROUTE_E_DUAL_POOL_BOOST = "route_e_dual_pool_boost"
    ROUTE_F_PARTITION_MIMIC = "route_f_partition_mimic"
    ROUTE_G_CONVICTION_TRUST = "route_g_conviction_trust"


@dataclass
class StrategySpec:
    id: StrategyId
    label: str
    description: str = ""
    style: str = "balanced"  # legacy | aggressive | momentum


STRATEGY_CATALOG: list[StrategySpec] = [
    StrategySpec(
        StrategyId.ROUTE_G_CONVICTION_TRUST,
        "信念分级·师傅信用",
        "16师傅合并修正版：≥20%重仓才强跟；20槽位；单票/信念上限25%；腾位不得卖出仍被师傅重仓的核心票",
        "aggressive",
    ),
    StrategySpec(
        StrategyId.ROUTE_F_PARTITION_MIMIC,
        "分仓模仿·20%",
        "每师傅按仓位×20%开仓，加仓减仓完全跟调，单票封顶20%",
        "aggressive",
    ),
    StrategySpec(
        StrategyId.ROUTE_B_MERGED_BOOST,
        "合并+共识加仓",
        "单组合信号开仓，≥2组合持有时加仓至15%（对照）",
        "aggressive",
    ),
    StrategySpec(
        StrategyId.ROUTE_E_DUAL_POOL_BOOST,
        "双池·再平衡",
        "活跃/稳健池各50%，池内共识加仓，季末现金再平衡",
        "balanced",
    ),
]

STYLE_POOL_ACTIVE = "active"
STYLE_POOL_STABLE = "stable"

STYLE_POOL_MAP: dict[str, str] = {
    "ZH1797852": STYLE_POOL_ACTIVE,
    "ZH3472193": STYLE_POOL_ACTIVE,
    "ZH3207026": STYLE_POOL_ACTIVE,
    "ZH3365207": STYLE_POOL_ACTIVE,
    "ZH810445": STYLE_POOL_STABLE,
    "ZH3337164": STYLE_POOL_STABLE,
    "ZH3459601": STYLE_POOL_STABLE,
    "ZH3481002": STYLE_POOL_STABLE,
    "ZH3558598": STYLE_POOL_STABLE,
}

STRATEGY_FOCUS: dict[str, str] = {
    StrategyId.ROUTE_G_CONVICTION_TRUST.value: "主推",
    StrategyId.ROUTE_F_PARTITION_MIMIC.value: "对照",
    StrategyId.ROUTE_B_MERGED_BOOST.value: "对照",
    StrategyId.ROUTE_E_DUAL_POOL_BOOST.value: "防御",
}

PARTITION_BUDGET_PCT = 0.20


def _strategy_spec(strategy_id: StrategyId) -> StrategySpec:
    for s in STRATEGY_CATALOG:
        if s.id == strategy_id:
            return s
    if strategy_id == StrategyId.LEGACY_K5:
        return StrategySpec(strategy_id, "Legacy K5", "旧版5仓跟单", "legacy")
    if strategy_id == StrategyId.LEGACY_K10:
        return StrategySpec(strategy_id, "Legacy K10", "旧版10仓跟单", "legacy")
    raise ValueError(f"未知或已下架策略: {strategy_id.value}")


@dataclass
class RunContext:
    initial_capital: float = INITIAL_CAPITAL
    max_stock_pct: float = 0.20
    max_positions: int = 0
    belief_cap_pct: float = 0.40
    main_account_pct: float = 0.85
    min_consensus_count: int = 1
    open_on_signal: bool = False
    consensus_boost: bool = False
    consensus_boost_pct: float = 0.15
    skip_orphan_reduce: bool = True
    weighted_open_threshold: float = 0.25
    leader_lookback_days: int = 730
    portfolio_weights: dict[str, float] = field(default_factory=dict)
    current_leader: str = ""
    metrics: dict[str, int] = field(default_factory=dict)
    # 信号漏斗：每批仅允许得分最高的 K 个 (组合, 股票) 买入
    signal_funnel_top_k: int = 0
    allowed_buys: set[tuple[str, str]] | None = None
    portfolio_return_lookback: int = 365
    # 信念仓
    belief_bet_mode: bool = False
    belief_min_master_pct: float = 35.0
    # 赢家/斩亏
    winner_cut_mode: bool = False
    loser_cut_pct: float = -15.0
    pyramid_cap_pct: float = 0.30
    # 动态权重跟单
    weighted_follow: bool = False
    top_portfolio_n: int = 0
    min_follow_weight: float = 0.05
    # FoF 师傅动量
    fof_filter: bool = False
    fof_top_n: int = 0
    fof_lookback_days: int = 180
    active_portfolios: set[str] = field(default_factory=set)
    # 衰减信息强度
    decay_mode: bool = False
    decay_scores: dict[str, float] = field(default_factory=dict)
    decay_half_life_days: int = 45
    last_batch_time: str = ""
    # 双池 / 分仓子账户
    dual_pool_mode: bool = False
    partition_mimic_mode: bool = False
    portfolio_budget_pct: float = PARTITION_BUDGET_PCT
    portfolio_pool: dict[str, str] = field(default_factory=dict)
    pool_cash_alloc: dict[str, float] = field(default_factory=dict)
    # 滞后共振
    lag_resonance_boost: bool = False
    lag_boost_pct: float = 0.22
    lag_resonance_days: int = 5
    recent_buys: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # 信念分级 + 师傅信用
    conviction_tier_mode: bool = False
    conviction_min_master_pct: float = HEAVY_HOLDER_PCT
    forbid_rotate_heavy: bool = False
    heavy_leg_events: list[HeavyLegEvent] = field(default_factory=list)
    trust_min_legs: int = 3
    mirror: dict[tuple[str, str], float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _group_batches(
    all_trades: list[tuple[str, str, TradeInput]],
) -> list[tuple[str, list[tuple[str, str, TradeInput]]]]:
    batches: list[tuple[str, list[tuple[str, str, TradeInput]]]] = []
    i = 0
    while i < len(all_trades):
        t0 = all_trades[i][2].trade_time
        batch: list[tuple[str, str, TradeInput]] = []
        while i < len(all_trades) and all_trades[i][2].trade_time == t0:
            batch.append(all_trades[i])
            i += 1
        batches.append((t0, batch))
    return batches


def _holders_for_code(mirror: dict[tuple[str, str], float], code: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for (p, c), w in mirror.items():
        if c == code and w > 1e-9:
            out[p] = w
    return out


def _consensus_count(mirror: dict[tuple[str, str], float], code: str) -> int:
    return len(_holders_for_code(mirror, code))


def _target_pct_by_count(count: int, max_pct: float) -> float:
    """按同时持有组合数分配目标仓位（双共识已很罕见，给予更高权重）。"""
    if count >= 3:
        return min(max_pct, 0.15)
    if count >= 2:
        return min(max_pct, 0.12)
    if count >= 1:
        return min(max_pct, 0.10)
    return 0.0


def _target_pct_by_weighted(score: float, max_pct: float) -> float:
    if score >= 0.6:
        return max_pct
    if score >= 0.4:
        return min(max_pct, 0.15)
    if score >= 0.25:
        return min(max_pct, 0.10)
    return 0.0


def _split_buy_to_slices(
    fund: SliceLedger,
    code: str,
    buy_qty: float,
    buy_price: float,
    owner_weights: dict[str, float],
) -> float:
    if buy_qty <= 1e-12 or not owner_weights:
        return 0.0
    lot = _lot_size(code)
    buy_qty = _floor_lots(code, buy_qty)
    if buy_qty < lot:
        return 0.0
    total_w = sum(owner_weights.values())
    if total_w <= 0:
        return 0.0
    owners = sorted(owner_weights.items(), key=lambda x: -x[1])

    # 整手不够拆分时，全部记到权重最高的组合 slice
    if buy_qty < lot * len(owners):
        return fund._buy_to_slice(owners[0][0], code, buy_qty, buy_price)

    bought_total = 0.0
    remaining = buy_qty
    for idx, (portfolio, w) in enumerate(owners):
        if idx == len(owners) - 1:
            portion = _floor_lots(code, remaining)
        else:
            portion = _floor_lots(code, buy_qty * (w / total_w))
        portion = min(portion, remaining)
        if portion < lot:
            continue
        got = fund._buy_to_slice(portfolio, code, portion, buy_price)
        bought_total += got
        remaining -= got
        if remaining < lot:
            break
    if bought_total <= 0 and remaining >= lot:
        return fund._buy_to_slice(owners[0][0], code, _floor_lots(code, remaining), buy_price)
    return bought_total


def _try_buy_to_target(
    fund: SliceLedger,
    code: str,
    raw_px: float,
    target_pct: float,
    nav: float,
    owner_weights: dict[str, float],
    star_unlocked: bool,
) -> float:
    if _is_star_market(code) and not star_unlocked:
        return 0.0
    if target_pct <= 0 or nav <= 0:
        return 0.0
    buy_price = _slippage_price(raw_px, True)
    phys = fund.physical_qty(code)
    current_val = phys * raw_px
    target_val = target_pct * nav
    gap = target_val - current_val
    if gap <= buy_price * _lot_size(code) * 0.5:
        return 0.0
    buy_qty = _floor_lots(code, gap / buy_price)
    if buy_qty < _lot_size(code):
        return 0.0
    cap_qty = target_pct * nav / buy_price
    max_add = max(0.0, cap_qty - phys)
    buy_qty = min(buy_qty, _floor_lots(code, max_add))
    if buy_qty < _lot_size(code):
        return 0.0
    return _split_buy_to_slices(fund, code, buy_qty, buy_price, owner_weights)


def _precompute_portfolio_curves(
    all_trades: list[tuple[str, str, TradeInput]],
) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[TradeInput]] = defaultdict(list)
    for acct, _, trade in all_trades:
        by_code[acct].append(trade)
    curves: dict[str, list[dict[str, Any]]] = {}
    for acct, trades in by_code.items():
        curves[acct] = compute_pseudo_nav(trades).get("equity_curve", [])
    return curves


def _parse_ts(trade_time: str) -> tuple[int, int, int]:
    d = trade_time[:10]
    y, m, day = d.split("-")
    return int(y), int(m), int(day)


def _days_between(t0: str, t1: str) -> int:
    y0, m0, d0 = _parse_ts(t0)
    y1, m1, d1 = _parse_ts(t1)
    from datetime import date

    return (date(y1, m1, d1) - date(y0, m0, d0)).days


def _nav_at_or_before(curve: list[dict], trade_time: str) -> float | None:
    nav = None
    for pt in curve:
        if pt["trade_time"] <= trade_time:
            nav = float(pt.get("nav") or pt.get("total_nav_hfq") or 0)
        else:
            break
    return nav


def _rolling_return(curve: list[dict], trade_time: str, lookback_days: int) -> float | None:
    end_nav = _nav_at_or_before(curve, trade_time)
    if not end_nav or end_nav <= 0:
        return None
    start_time = trade_time
    best_start_nav = None
    for pt in curve:
        if pt["trade_time"] > trade_time:
            break
        if _days_between(pt["trade_time"], trade_time) >= lookback_days:
            start_time = pt["trade_time"]
            best_start_nav = float(pt.get("nav") or 0)
    if best_start_nav is None or best_start_nav <= 0:
        for pt in curve:
            if pt["trade_time"] <= trade_time:
                best_start_nav = float(pt.get("nav") or 0)
            else:
                break
        if not best_start_nav or best_start_nav <= 0:
            return None
    return (end_nav / best_start_nav) - 1.0


def _pick_leader(
    curves: dict[str, list[dict]],
    trade_time: str,
    lookback_days: int = 730,
) -> str:
    best_code = ""
    best_ret = -1e18
    for code, curve in curves.items():
        ret = _rolling_return(curve, trade_time, lookback_days)
        if ret is not None and ret > best_ret:
            best_ret = ret
            best_code = code
    return best_code or next(iter(curves.keys()), "")


def _pick_top_portfolios(
    curves: dict[str, list[dict]],
    trade_time: str,
    lookback_days: int,
    top_n: int,
) -> set[str]:
    ranked: list[tuple[float, str]] = []
    for code, curve in curves.items():
        ret = _rolling_return(curve, trade_time, lookback_days)
        ranked.append((ret if ret is not None else -1e18, code))
    ranked.sort(key=lambda x: -x[0])
    return {code for _, code in ranked[: max(top_n, 1)]}


def _liquidate_portfolio_slices(
    fund: SliceLedger,
    portfolio: str,
    raw_prices: dict[str, float],
    trade_logs: list[dict],
    trade_time: str,
    ctx: RunContext,
) -> None:
    for (p, c), sl in list(fund.slices.items()):
        if p != portfolio or sl.qty <= 1e-12:
            continue
        px = raw_prices.get(c) or fund.last_raw_marks.get(c, 0.0)
        if px <= 0:
            continue
        nav = fund.nav(raw_prices)
        sell_px = _slippage_price(px, False)
        weight_before = _our_weight_pct(fund, c, raw_prices, nav=nav)
        sold = fund._sell_from_slice(p, c, sl.qty, sell_px, nav)
        if sold > 0:
            proceeds = sold * sell_px
            _adjust_pool_cash(ctx, p, proceeds)
            nav_after = fund.nav(raw_prices)
            _append_simple_log(
                trade_logs,
                trade_time=trade_time,
                source_portfolio="系统",
                action="FoF出池清仓",
                ts_code=c,
                stock_name=fund.stock_names.get(c, c),
                qty_delta=-sold,
                nav_after=nav_after,
                trigger="FoF汰弱",
                price=sell_px,
                our_weight_pct=_our_weight_pct(fund, c, raw_prices, nav=nav_after),
                master_from=weight_before,
                master_to=0.0,
            )


def _pool_slice_value(
    fund: SliceLedger,
    raw_prices: dict[str, float],
    pool_id: str,
    portfolio_pool: dict[str, str],
) -> float:
    total = 0.0
    for (p, c), sl in fund.slices.items():
        if portfolio_pool.get(p) != pool_id or sl.qty <= 1e-12:
            continue
        px = raw_prices.get(c) or fund.last_raw_marks.get(c, 0.0)
        if px > 0:
            total += sl.qty * px
    return total


def _virtual_pool_mode(ctx: RunContext) -> bool:
    return ctx.dual_pool_mode or ctx.partition_mimic_mode


def _pool_budget_fallback(ctx: RunContext) -> float:
    if ctx.partition_mimic_mode:
        return ctx.initial_capital * ctx.portfolio_budget_pct
    return ctx.initial_capital * 0.5


def _pool_nav(
    fund: SliceLedger,
    raw_prices: dict[str, float],
    ctx: RunContext,
    pool_id: str,
) -> float:
    cash_part = ctx.pool_cash_alloc.get(pool_id, 0.0)
    pos_part = _pool_slice_value(fund, raw_prices, pool_id, ctx.portfolio_pool)
    nav = cash_part + pos_part
    return nav if nav > 0 else _pool_budget_fallback(ctx)


def _nav_for_account(
    fund: SliceLedger,
    raw_prices: dict[str, float],
    ctx: RunContext,
    acct_code: str,
) -> float:
    if _virtual_pool_mode(ctx) and acct_code in ctx.portfolio_pool:
        return _pool_nav(fund, raw_prices, ctx, ctx.portfolio_pool[acct_code])
    return fund.nav(raw_prices)


def _adjust_pool_cash(ctx: RunContext, acct_code: str, cash_delta: float) -> None:
    if not _virtual_pool_mode(ctx):
        return
    pool = ctx.portfolio_pool.get(acct_code)
    if not pool:
        return
    ctx.pool_cash_alloc[pool] = ctx.pool_cash_alloc.get(pool, 0.0) + cash_delta


def _rebalance_pool_cash(fund: SliceLedger, raw_prices: dict[str, float], ctx: RunContext) -> None:
    if not ctx.dual_pool_mode or ctx.partition_mimic_mode:
        return
    nav_a = _pool_nav(fund, raw_prices, ctx, STYLE_POOL_ACTIVE)
    nav_s = _pool_nav(fund, raw_prices, ctx, STYLE_POOL_STABLE)
    total = nav_a + nav_s
    if total <= 0:
        return
    target = total * 0.5
    transfer = target - nav_a
    ctx.pool_cash_alloc[STYLE_POOL_ACTIVE] = ctx.pool_cash_alloc.get(STYLE_POOL_ACTIVE, 0.0) + transfer
    ctx.pool_cash_alloc[STYLE_POOL_STABLE] = ctx.pool_cash_alloc.get(STYLE_POOL_STABLE, 0.0) - transfer


def _apply_decay_elapsed(ctx: RunContext, trade_time: str) -> None:
    if not ctx.decay_mode:
        return
    if not ctx.last_batch_time:
        ctx.last_batch_time = trade_time
        return
    days = _days_between(ctx.last_batch_time, trade_time)
    if days > 0:
        factor = 0.5 ** (days / ctx.decay_half_life_days)
        ctx.decay_scores = {
            k: v * factor for k, v in ctx.decay_scores.items() if v * factor > 1e-9
        }
    ctx.last_batch_time = trade_time


def _update_decay_scores_from_batch(ctx: RunContext, batch: list[tuple[str, str, TradeInput]]) -> None:
    if not ctx.decay_mode:
        return
    for _acct, _name, trade in batch:
        code = trade.ts_code
        dw = float(trade.to_weight) - float(trade.from_weight)
        if dw > 1e-9:
            ctx.decay_scores[code] = ctx.decay_scores.get(code, 0.0) + max(dw, 1.0)
        elif dw < -1e-9:
            cur = ctx.decay_scores.get(code, 0.0)
            if cur <= 0:
                continue
            reduction = max(abs(dw), 1.0)
            nxt = max(0.0, cur - reduction)
            if nxt <= 1e-9:
                ctx.decay_scores.pop(code, None)
            else:
                ctx.decay_scores[code] = nxt


def _decay_intensity_multiplier(ctx: RunContext, code: str) -> float:
    scores = [v for v in ctx.decay_scores.values() if v > 1e-9]
    if not scores:
        return 1.0
    s = ctx.decay_scores.get(code, 0.0)
    if s <= 1e-9:
        return 0.6
    scores_sorted = sorted(scores)
    p90_idx = max(0, int(math.ceil(len(scores_sorted) * 0.9)) - 1)
    p90 = scores_sorted[min(p90_idx, len(scores_sorted) - 1)]
    if p90 <= 1e-9:
        return 1.0
    ratio = min(s / p90, 1.0)
    return min(1.0, 0.6 + 0.8 * ratio)


def _prune_recent_buys(ctx: RunContext, trade_time: str) -> None:
    d_end = trade_time[:10]
    for code in list(ctx.recent_buys):
        kept = [
            (d, a)
            for d, a in ctx.recent_buys[code]
            if _days_between(d, d_end) <= ctx.lag_resonance_days
        ]
        if kept:
            ctx.recent_buys[code] = kept
        else:
            del ctx.recent_buys[code]


def _record_batch_buys(ctx: RunContext, batch: list[tuple[str, str, TradeInput]], trade_time: str) -> None:
    if not ctx.lag_resonance_boost:
        return
    d = trade_time[:10]
    for acct, _, trade in batch:
        if trade.to_weight > trade.from_weight + 1e-9:
            ctx.recent_buys.setdefault(trade.ts_code, []).append((d, acct))
    _prune_recent_buys(ctx, trade_time)


def _has_lag_resonance(ctx: RunContext, code: str, acct: str, trade_time: str) -> bool:
    entries = ctx.recent_buys.get(code, [])
    accts = {a for d, a in entries if _days_between(d, trade_time[:10]) <= ctx.lag_resonance_days}
    accts.discard(acct)
    return len(accts) >= 1


def _apply_lag_resonance_boost(
    fund: SliceLedger,
    batch: list[tuple[str, str, TradeInput]],
    batch_prices: dict[str, float],
    ctx: RunContext,
    trade_logs: list[dict],
    trade_time: str,
    star_unlocked: bool,
) -> None:
    if not ctx.lag_resonance_boost:
        return
    raw_prices = _build_raw_prices(fund, batch_prices)
    nav = fund.nav(raw_prices)
    boosted: set[str] = set()
    for acct, _, trade in batch:
        if trade.to_weight <= trade.from_weight + 1e-9:
            continue
        code = trade.ts_code
        if code in boosted:
            continue
        if not _has_lag_resonance(ctx, code, acct, trade_time):
            continue
        if fund.physical_qty(code) <= 1e-12:
            continue
        raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
        if not raw_px:
            continue
        holders = {acct: float(trade.to_weight)}
        bought = _try_buy_to_target(
            fund, code, raw_px, ctx.lag_boost_pct, nav, holders, star_unlocked
        )
        if bought > 0:
            boosted.add(code)
            nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
            _append_simple_log(
                trade_logs,
                trade_time=trade_time,
                source_portfolio="共振",
                action="滞后共振加码",
                ts_code=code,
                stock_name=fund.stock_names.get(code, code),
                qty_delta=bought,
                nav_after=nav_after,
                trigger="滞后共振",
                price=raw_px,
                our_weight_pct=_our_weight_pct(fund, code, raw_prices, nav=nav_after),
            )


def _softmax_weights(returns: dict[str, float], temperature: float = 0.15) -> dict[str, float]:
    valid = {k: v for k, v in returns.items() if v is not None}
    if not valid:
        n = len(returns)
        return {k: 1.0 / n for k in returns} if n else {}
    mx = max(valid.values())
    exps = {k: math.exp((v - mx) / temperature) for k, v in valid.items()}
    total = sum(exps.values())
    if total <= 0:
        n = len(valid)
        return {k: 1.0 / n for k in valid}
    weights = {k: v / total for k, v in exps.items()}
    for k in returns:
        if k not in weights:
            weights[k] = 0.0
    low = {k: w for k, w in weights.items() if w < 0.03}
    for k in low:
        weights[k] = 0.0
    total = sum(weights.values())
    if total <= 0:
        n = len([w for w in weights.values() if w > 0]) or len(weights)
        return {k: (1.0 / n if weights.get(k, 0) >= 0 else 0.0) for k in weights}
    return {k: w / total for k, w in weights.items()}


def _weighted_consensus(
    mirror: dict[tuple[str, str], float],
    code: str,
    weights: dict[str, float],
) -> float:
    score = 0.0
    for p, w in _holders_for_code(mirror, code).items():
        score += weights.get(p, 0.0)
    return score


def _quarter_key(trade_time: str) -> str:
    y, m, _ = _parse_ts(trade_time)
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


def _max_drawdown(equity: list[dict]) -> float:
    peak = 0.0
    mdd = 0.0
    for pt in equity:
        nav = float(pt.get("total_nav_hfq") or pt.get("nav") or 0)
        if nav <= 0:
            continue
        peak = max(peak, nav)
        if peak > 0:
            mdd = max(mdd, (peak - nav) / peak)
    return round(mdd * 100, 2)


def _sharpe_proxy(equity: list[dict]) -> float | None:
    rets: list[float] = []
    prev = None
    for pt in equity:
        nav = float(pt.get("total_nav_hfq") or pt.get("nav") or 0)
        if nav <= 0:
            continue
        if prev and prev > 0:
            rets.append(nav / prev - 1.0)
        prev = nav
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var) if var > 0 else 0.0
    if std <= 1e-12:
        return None
    return round(mean / std * math.sqrt(len(rets)), 2)


def _return_since(equity: list[dict], start_prefix: str) -> float | None:
    start_nav = None
    end_nav = None
    for pt in equity:
        t = pt.get("trade_time", "")
        nav = float(pt.get("total_nav_hfq") or pt.get("nav") or 0)
        if nav <= 0:
            continue
        if t >= start_prefix and start_nav is None:
            start_nav = nav
        end_nav = nav
    if start_nav and end_nav and start_nav > 0:
        return round((end_nav / start_nav - 1.0) * 100, 2)
    return None


def _held_codes(fund: SliceLedger) -> list[str]:
    return [code for code, h in fund.holdings.items() if h.qty > 1e-12]


def _our_weight_pct(
    fund: SliceLedger,
    code: str,
    raw_prices: dict[str, float],
    *,
    nav: float | None = None,
) -> float:
    nav_v = nav if nav is not None else fund.nav(raw_prices)
    if nav_v <= 0:
        return 0.0
    holding = fund.holdings.get(code)
    if not holding or holding.qty <= 1e-12:
        return 0.0
    px = raw_prices.get(code, fund.last_raw_marks.get(code, 0.0))
    if px <= 0:
        return 0.0
    return round(_weight_pct(holding.qty, px, nav_v), 2)


def _append_simple_log(
    trade_logs: list[dict],
    *,
    trade_time: str,
    action: str,
    ts_code: str,
    stock_name: str,
    source_portfolio: str = "",
    source_name: str = "",
    qty_delta: float = 0.0,
    nav_after: float = 0.0,
    trigger: str | None = None,
    price: float = 0.0,
    our_weight_pct: float = 0.0,
    master_from: float | None = None,
    master_to: float | None = None,
) -> None:
    trade_logs.append(
        {
            "trade_time": trade_time,
            "source_portfolio": source_portfolio,
            "source_name": source_name or source_portfolio,
            "action": action,
            "ts_code": ts_code,
            "stock_name": stock_name,
            "qty_delta": qty_delta,
            "trigger": trigger,
            "nav_after": round(nav_after, 2),
            "price": round(price, 4) if price else 0.0,
            "our_weight_pct": our_weight_pct,
            "master_from": master_from,
            "master_to": master_to,
        }
    )


def _sell_weakest_position(
    fund: SliceLedger,
    raw_prices: dict[str, float],
    trade_logs: list[dict],
    trade_time: str,
    *,
    trigger: str = "换仓腾位",
    ctx: RunContext | None = None,
    new_code: str = "",
) -> bool:
    held = [code for code in _held_codes(fund) if code != new_code]
    if not held:
        return False
    ranked: list[tuple[float, str]] = []
    protected = 0
    for code in held:
        heavy_holders = (
            {
                acct: weight
                for acct, weight in _holders_for_code(ctx.mirror, code).items()
                if weight >= ctx.conviction_min_master_pct
            }
            if ctx and ctx.forbid_rotate_heavy
            else {}
        )
        if heavy_holders:
            protected += 1
            continue
        holding = fund.holdings[code]
        px = raw_prices.get(code, fund.last_raw_marks.get(code, 0.0))
        if px <= 0 or holding.vwap <= 0:
            ret = -999.0
        else:
            ret = px / holding.vwap - 1.0
        ranked.append((ret, code))
    if ctx is not None and protected:
        ctx.diagnostics["rotate_protected_positions"] = ctx.diagnostics.get("rotate_protected_positions", 0) + protected
    if not ranked:
        if ctx is not None:
            ctx.diagnostics["skipped_by_no_sellable_slot"] = ctx.diagnostics.get("skipped_by_no_sellable_slot", 0) + 1
        _append_simple_log(
            trade_logs,
            trade_time=trade_time,
            source_portfolio="系统",
            action="腾位跳过",
            ts_code=new_code or "",
            stock_name=fund.stock_names.get(new_code, new_code) if new_code else "无可腾位标的",
            qty_delta=0.0,
            nav_after=fund.nav(raw_prices),
            trigger="换仓跳过",
            price=raw_prices.get(new_code, 0.0) if new_code else 0.0,
            our_weight_pct=0.0,
            master_from=None,
            master_to=None,
        )
        return False
    ranked.sort(key=lambda x: (x[0], x[1]))
    worst_code = ranked[0][1]
    px = raw_prices.get(worst_code, fund.last_raw_marks.get(worst_code, 0.0))
    if px <= 0:
        return False
    nav = fund.nav(raw_prices)
    sell_px = _slippage_price(px, False)
    weight_before = _our_weight_pct(fund, worst_code, raw_prices, nav=nav)
    sold = fund.liquidate_all_slices(worst_code, sell_px, nav)
    if sold > 0:
        if ctx is not None:
            ctx.diagnostics["forced_liquidation_count"] = ctx.diagnostics.get("forced_liquidation_count", 0) + 1
            ctx.diagnostics["forced_liquidation_amount"] = ctx.diagnostics.get("forced_liquidation_amount", 0.0) + sold * sell_px
        nav_after = fund.nav(raw_prices)
        _append_simple_log(
            trade_logs,
            trade_time=trade_time,
            source_portfolio="系统",
            action="腾位卖出",
            ts_code=worst_code,
            stock_name=fund.stock_names.get(worst_code, worst_code),
            qty_delta=-sold,
            nav_after=nav_after,
            trigger=trigger,
            price=sell_px,
            our_weight_pct=0.0,
            master_from=weight_before,
            master_to=0.0,
        )
        return True
    return False


def _ensure_position_slot(
    fund: SliceLedger,
    ctx: RunContext,
    raw_prices: dict[str, float],
    trade_logs: list[dict],
    trade_time: str,
    new_code: str,
) -> None:
    if ctx.max_positions <= 0:
        return
    while True:
        held = [c for c in _held_codes(fund) if c != new_code]
        if len(held) < ctx.max_positions:
            return
        if not _sell_weakest_position(
            fund,
            raw_prices,
            trade_logs,
            trade_time,
            ctx=ctx,
            new_code=new_code,
        ):
            return


def _portfolio_return_rank(
    portfolio_curves: dict[str, list[dict]],
    trade_time: str,
    lookback: int,
) -> dict[str, float]:
    rets: dict[str, float] = {}
    for code, curve in portfolio_curves.items():
        ret = _rolling_return(curve, trade_time, lookback)
        rets[code] = ret if ret is not None else -1.0
    return rets


def _top_portfolios(rets: dict[str, float], n: int) -> set[str]:
    if n <= 0:
        return set(rets.keys())
    ranked = sorted(rets.items(), key=lambda x: -x[1])
    return {k for k, _ in ranked[:n]}


def _prepare_funnel_allows(
    ctx: RunContext,
    batch: list[tuple[str, str, TradeInput]],
    trade_time: str,
    portfolio_curves: dict[str, list[dict]],
) -> None:
    if ctx.signal_funnel_top_k <= 0:
        ctx.allowed_buys = None
        return
    ranked: list[tuple[float, str, str]] = []
    for acct, _, trade in batch:
        if trade.to_weight <= trade.from_weight + 1e-9:
            continue
        ret = _rolling_return(
            portfolio_curves.get(acct, []), trade_time, ctx.portfolio_return_lookback
        )
        ret_v = ret if ret is not None else 0.0
        dw = max(float(trade.to_weight) - float(trade.from_weight), 1.0)
        ranked.append((ret_v * dw, acct, trade.ts_code))
    ranked.sort(key=lambda x: -x[0])
    ctx.allowed_buys = {(acct, code) for _, acct, code in ranked[: ctx.signal_funnel_top_k]}


def _apply_consensus_boost_open(
    fund: SliceLedger,
    mirror: dict[tuple[str, str], float],
    batch_prices: dict[str, float],
    ctx: RunContext,
    trade_logs: list[dict],
    trade_time: str,
    star_unlocked: bool,
) -> None:
    if not ctx.consensus_boost:
        return
    nav = fund.nav(_build_raw_prices(fund, batch_prices))
    boost_codes = {c for c, h in fund.holdings.items() if h.qty > 1e-12}
    boost_codes |= {c for (_p, c), w in mirror.items() if w > 1e-9}
    for code in boost_codes:
        holders = _holders_for_code(mirror, code)
        if len(holders) < 2:
            continue
        raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
        if not raw_px:
            continue
        bought = _try_buy_to_target(
            fund, code, raw_px, ctx.consensus_boost_pct, nav, holders, star_unlocked
        )
        if bought > 0:
            nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
            _append_simple_log(
                trade_logs,
                trade_time=trade_time,
                source_portfolio="共识",
                action="共识加仓",
                ts_code=code,
                stock_name=fund.stock_names.get(code, code),
                qty_delta=bought,
                nav_after=nav_after,
                trigger="双共识加仓",
                price=raw_px,
                our_weight_pct=_our_weight_pct(fund, code, _build_raw_prices(fund, batch_prices), nav=nav_after),
            )


def _conviction_target_for_open(
    ctx: RunContext,
    mirror: dict[tuple[str, str], float],
    acct_code: str,
    code: str,
    master_to_weight: float,
    trade_time: str,
) -> float:
    temp_mirror = dict(mirror)
    temp_mirror[(acct_code, code)] = float(master_to_weight)
    trust = portfolio_trust_at(
        ctx.heavy_leg_events,
        acct_code,
        trade_time,
        min_legs=ctx.trust_min_legs,
        threshold=ctx.conviction_min_master_pct,
    )
    return conviction_cap_pct(
        float(master_to_weight),
        temp_mirror,
        code,
        trust,
        hard_cap=ctx.belief_cap_pct,
        heavy_pct=ctx.conviction_min_master_pct,
    )


def _apply_conviction_consensus_align(
    fund: SliceLedger,
    mirror: dict[tuple[str, str], float],
    batch_prices: dict[str, float],
    ctx: RunContext,
    trade_logs: list[dict],
    trade_time: str,
    star_unlocked: bool,
) -> None:
    if not ctx.conviction_tier_mode:
        return
    raw_prices = _build_raw_prices(fund, batch_prices)
    nav = fund.nav(raw_prices)
    seen: set[str] = set()
    for (_p, code), weight in mirror.items():
        if weight < ctx.conviction_min_master_pct or code in seen:
            continue
        holders = _holders_for_code(mirror, code)
        heavy_count = sum(1 for w in holders.values() if w >= ctx.conviction_min_master_pct)
        if heavy_count < 2:
            continue
        seen.add(code)
        max_master = max(holders.values(), default=0.0)
        trust = consensus_trust_for_code(
            ctx.heavy_leg_events,
            mirror,
            code,
            trade_time,
            min_legs=ctx.trust_min_legs,
            heavy_pct=ctx.conviction_min_master_pct,
        )
        cap = conviction_cap_pct(
            max_master,
            mirror,
            code,
            trust,
            hard_cap=ctx.belief_cap_pct,
            heavy_pct=ctx.conviction_min_master_pct,
        )
        if cap <= 0:
            continue
        raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
        if not raw_px:
            continue
        if fund.physical_qty(code) <= 1e-12:
            _ensure_position_slot(fund, ctx, raw_prices, trade_logs, trade_time, code)
            nav = fund.nav(raw_prices)
        bought = _try_buy_to_target(fund, code, raw_px, cap, nav, holders, star_unlocked)
        if bought > 0:
            _append_simple_log(
                trade_logs,
                trade_time=trade_time,
                source_portfolio="共识",
                source_name="信念共识",
                action="信念共识加仓",
                ts_code=code,
                stock_name=fund.stock_names.get(code, code),
                qty_delta=bought,
                nav_after=fund.nav(raw_prices),
                trigger="信念共识",
            )


def _apply_belief_bets(
    fund: SliceLedger,
    batch: list[tuple[str, str, TradeInput]],
    mirror: dict[tuple[str, str], float],
    batch_prices: dict[str, float],
    ctx: RunContext,
    trade_logs: list[dict],
    trade_time: str,
    star_unlocked: bool,
) -> None:
    if not ctx.belief_bet_mode:
        return
    raw_prices = _build_raw_prices(fund, batch_prices)
    for _acct, _name, trade in batch:
        if float(trade.to_weight) < ctx.belief_min_master_pct:
            continue
        holders = _holders_for_code(mirror, trade.ts_code)
        if len(holders) < 2:
            continue
        raw_px = batch_prices.get(trade.ts_code) or fund.last_raw_marks.get(trade.ts_code)
        if not raw_px:
            continue
        nav = fund.nav(raw_prices)
        if fund.physical_qty(trade.ts_code) <= 1e-12:
            _ensure_position_slot(fund, ctx, raw_prices, trade_logs, trade_time, trade.ts_code)
            nav = fund.nav(raw_prices)
        bought = _try_buy_to_target(
            fund,
            trade.ts_code,
            raw_px,
            ctx.belief_cap_pct,
            nav,
            holders,
            star_unlocked,
        )
        if bought > 0:
            _append_simple_log(
                trade_logs,
                trade_time=trade_time,
                source_portfolio=_acct,
                source_name=_name,
                action="信念加仓",
                ts_code=trade.ts_code,
                stock_name=trade.stock_name,
                qty_delta=bought,
                nav_after=fund.nav(raw_prices),
                trigger="信念仓",
            )


def _apply_winner_loser_rules(
    fund: SliceLedger,
    batch: list[tuple[str, str, TradeInput]],
    batch_prices: dict[str, float],
    ctx: RunContext,
    trade_logs: list[dict],
    trade_time: str,
    star_unlocked: bool,
) -> None:
    if not ctx.winner_cut_mode:
        return
    raw_prices = _build_raw_prices(fund, batch_prices)
    nav = fund.nav(raw_prices)
    added_codes = {t.ts_code for _, _, t in batch if t.to_weight > t.from_weight + 1e-9}
    for code, holding in list(fund.holdings.items()):
        if holding.qty <= 1e-12:
            continue
        px = raw_prices.get(code, fund.last_raw_marks.get(code, 0.0))
        if px <= 0 or holding.vwap <= 0:
            continue
        ret_pct = (px / holding.vwap - 1.0) * 100
        if ret_pct < ctx.loser_cut_pct:
            sell_px = _slippage_price(px, False)
            sold = fund.liquidate_all_slices(code, sell_px, nav)
            if sold > 0:
                _append_simple_log(
                    trade_logs,
                    trade_time=trade_time,
                    source_portfolio="系统",
                    action="斩亏平仓",
                    ts_code=code,
                    stock_name=fund.stock_names.get(code, code),
                    qty_delta=-sold,
                    nav_after=fund.nav(raw_prices),
                    trigger="浮亏止损",
                )
        elif ret_pct > 0 and code in added_codes:
            nav = fund.nav(raw_prices)
            bought = _try_buy_to_target(
                fund,
                code,
                px,
                ctx.pyramid_cap_pct,
                nav,
                {"_赢家": 1.0},
                star_unlocked,
            )
            if bought > 0:
                _append_simple_log(
                    trade_logs,
                    trade_time=trade_time,
                    source_portfolio="系统",
                    action="赢家加仓",
                    ts_code=code,
                    stock_name=fund.stock_names.get(code, code),
                    qty_delta=bought,
                    nav_after=fund.nav(raw_prices),
                    trigger="浮盈加码",
                )


def _finalize(
    fund: SliceLedger,
    cfg: RunContext,
    account_names: dict[str, str],
    all_trades: list,
    equity_curve: list[dict],
    trade_logs: list[dict],
    strategy_id: StrategyId,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    holding_codes = {code for code, h in fund.holdings.items() if h.qty > 1e-12}
    hfq_mark_prices = dict(fund.last_hfq_marks)
    raw_mark_prices = {code: fund.last_raw_marks.get(code, 0.0) for code in holding_codes}
    final_nav = fund.nav(raw_mark_prices)
    final_nav_hfq = fund.nav_hfq(hfq_mark_prices)
    cash_pct = round(fund.cash / final_nav * 100, 2) if final_nav > 0 else 0.0

    orphan = sum(1 for log in trade_logs if "无slice" in str(log.get("action", "")))
    rotate = sum(1 for log in trade_logs if log.get("trigger") in ("换仓", "换仓腾位"))
    rebalance = sum(1 for log in trade_logs if str(log.get("trigger", "")).startswith("配平"))

    positions = []
    for code, holding in fund.holdings.items():
        if holding.qty <= 1e-12:
            continue
        raw_price = raw_mark_prices.get(code, 0.0)
        hfq_price = hfq_mark_prices.get(code, 0.0)
        value = holding.qty * raw_price
        return_pct = None
        if raw_price > 0 and holding.vwap > 0:
            return_pct = round((raw_price / holding.vwap - 1.0) * 100, 2)
        positions.append(
            {
                "ts_code": code,
                "stock_name": fund.stock_names.get(code, code),
                "qty": round(holding.qty, 0),
                "avg_cost": round(holding.vwap, 4),
                "mark_price": round(raw_price, 4),
                "mark_price_hfq": round(hfq_price, 4) if hfq_price else None,
                "return_pct": return_pct,
                "value": round(value, 2),
                "weight_pct": round(value / final_nav * 100, 2) if final_nav > 0 else 0.0,
            }
        )
    positions.sort(key=lambda x: -x["value"])

    all_stock_codes = set(fund.trade_counts.keys()) | holding_codes
    grouped_stats = [
        fund.grouped_row(code, hfq_mark_prices.get(code))
        for code in sorted(all_stock_codes, key=lambda c: (-fund.trade_counts.get(c, 0), c))
    ]

    source_stats: dict[str, int] = {}
    for log in trade_logs:
        if log.get("action") not in ("688拦截", "封顶减仓"):
            src = log.get("source_portfolio") or ""
            if src:
                source_stats[src] = source_stats.get(src, 0) + 1

    realized_trades = [leg for st in fund.stats.values() for leg in st.sell_legs]
    wins = sum(1 for leg in realized_trades if leg["leg_return_pct"] >= 0)
    overview_win_rate = round(wins / len(realized_trades) * 100, 2) if realized_trades else 0.0
    friction_legs = [
        leg
        for leg in realized_trades
        if -2.10 <= float(leg["leg_return_pct"]) <= -1.80
    ]
    friction_loss = sum(
        cfg.initial_capital
        * float(leg["weight_sold"])
        / 100.0
        * abs(float(leg["leg_return_pct"]))
        / 100.0
        for leg in friction_legs
    )
    traded_accounts = {
        str(log.get("source_portfolio") or "")
        for log in trade_logs
        if str(log.get("source_portfolio") or "") not in ("", "系统", "共识", "共振")
        and abs(float(log.get("qty_delta") or 0.0)) > 1e-12
    }
    diagnostics = {
        "configured_accounts_count": len(account_names),
        "loaded_accounts_count": len(account_names),
        "active_accounts_count": len({acct for acct, _, _trade in all_trades}),
        "heavy_signal_accounts_count": len(
            {acct for acct, _, trade in all_trades if float(trade.to_weight) >= cfg.conviction_min_master_pct}
        ),
        "traded_accounts_count": len(traded_accounts),
        "forced_liquidation_count": int(cfg.diagnostics.get("forced_liquidation_count", 0)),
        "forced_liquidation_amount": round(float(cfg.diagnostics.get("forced_liquidation_amount", 0.0)), 2),
        "forced_exit_while_master_heavy": int(cfg.diagnostics.get("forced_exit_while_master_heavy", 0)),
        "skipped_by_no_sellable_slot": int(cfg.diagnostics.get("skipped_by_no_sellable_slot", 0)),
        "rotate_protected_positions": int(cfg.diagnostics.get("rotate_protected_positions", 0)),
        "friction_leg_count": len(friction_legs),
        "friction_loss_estimate": round(friction_loss, 2),
        "friction_nav_impact_pct": round(friction_loss / cfg.initial_capital * 100.0, 2)
        if cfg.initial_capital > 0
        else 0.0,
    }

    blocked_688 = cfg.metrics.get("blocked_688", 0)
    cap_triggers = sum(1 for log in trade_logs if log.get("trigger") == "封顶减仓")
    skipped_lot = sum(1 for log in trade_logs if "低于最小手数" in str(log.get("action", "")))

    result = {
        "strategy_id": strategy_id.value,
        "initial_capital": cfg.initial_capital,
        "final_nav": round(final_nav, 2),
        "final_nav_hfq": round(final_nav_hfq, 2),
        "profit": round(final_nav - cfg.initial_capital, 2),
        "profit_hfq": round(final_nav_hfq - cfg.initial_capital, 2),
        "return_pct": round((final_nav_hfq / cfg.initial_capital - 1.0) * 100, 2),
        "return_pct_raw": round((final_nav / cfg.initial_capital - 1.0) * 100, 2),
        "cash": round(fund.cash, 2),
        "cash_pct": cash_pct,
        "position_count": len(positions),
        "max_drawdown_pct": _max_drawdown(equity_curve),
        "sharpe_proxy": _sharpe_proxy(equity_curve),
        "return_since_2023": _return_since(equity_curve, "2023-01-01"),
        "return_since_2020": _return_since(equity_curve, "2020-01-01"),
        "orphan_sell_count": orphan,
        "rotate_count": rotate,
        "rebalance_count": rebalance,
        "rotate_triggers": rotate,
        "rebalance_triggers": rebalance,
        "trade_log_count": len(trade_logs),
        "current_leader": cfg.current_leader,
        "start_time": all_trades[0][2].trade_time,
        "end_time": all_trades[-1][2].trade_time,
        "portfolio_count": len(account_names),
        "positions": positions,
        "equity_curve": equity_curve,
        "trade_logs": trade_logs,
        "grouped_stats": grouped_stats,
        "source_stats": source_stats,
        "overview_win_rate": overview_win_rate,
        "diagnostics": diagnostics,
        "blocked_688": blocked_688,
        "cap_triggers": cap_triggers,
        "skipped_lot": skipped_lot,
        "skipped_small": 0,
        "star_unlocked": (final_nav - cfg.initial_capital) >= STAR_UNLOCK_PROFIT,
        "max_stock_pct": cfg.max_stock_pct * 100,
        "min_new_position_pct": 1.0,
        "max_positions": cfg.max_positions,
    }
    if extra:
        result.update(extra)
    return result


def _run_owners_batch(
    fund: SliceLedger,
    batch: list[tuple[str, str, TradeInput]],
    batch_prices: dict[str, float],
    batch_hfq: dict[str, float],
    trade_time: str,
    mirror: dict[tuple[str, str], float],
    ctx: RunContext,
    trade_logs: list[dict],
    *,
    use_weighted: bool = False,
    leader: str = "",
    leader_mode: bool = False,
    belief_mode: bool = False,
) -> None:
    from xueqiu.domain.copy_backtest import _result_to_log

    raw_prices = _build_raw_prices(fund, batch_prices)
    nav_before = fund.nav(raw_prices)
    star_unlocked = nav_before - ctx.initial_capital >= STAR_UNLOCK_PROFIT - 1e-6
    ctx.mirror = dict(mirror)
    for acct_code, _, trade in batch:
        if trade.to_weight <= 1e-9:
            ctx.mirror.pop((acct_code, trade.ts_code), None)
        else:
            ctx.mirror[(acct_code, trade.ts_code)] = float(trade.to_weight)

    for acct_code, acct_name, trade in batch:
        if leader_mode and acct_code != leader and acct_code != BOOST_PORTFOLIO:
            if trade.to_weight + 1e-9 < trade.from_weight:
                pass
            else:
                continue
        if ctx.fof_filter and acct_code not in ctx.active_portfolios:
            if trade.to_weight + 1e-9 < trade.from_weight:
                pass
            else:
                continue
        raw_px = batch_prices.get(trade.ts_code)
        hfq_px = batch_hfq.get(trade.ts_code)
        if not raw_px:
            continue
        fund.stock_names[trade.ts_code] = trade.stock_name
        if trade.to_weight + 1e-9 < trade.from_weight:
            sl = fund._slice(acct_code, trade.ts_code)
            if sl.qty <= 1e-12 and ctx.skip_orphan_reduce:
                continue
            nav_pre = fund.nav(_build_raw_prices(fund, batch_prices))
            result = fund.apply_reduce_signal(
                acct_code,
                trade.ts_code,
                trade.stock_name,
                float(trade.from_weight),
                float(trade.to_weight),
                raw_px,
                nav_pre,
            )
            fund.trade_counts[trade.ts_code] += 1
            if result:
                if "无slice" in result.action:
                    ctx.metrics["orphan_sell"] = ctx.metrics.get("orphan_sell", 0) + 1
                if result.qty_delta < -1e-12:
                    _adjust_pool_cash(
                        ctx, acct_code, -result.qty_delta * _slippage_price(raw_px, False)
                    )
                nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                trade_logs.append(
                    _result_to_log(trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after)
                )
        elif trade.to_weight > trade.from_weight + 1e-9:
            if ctx.conviction_tier_mode and float(trade.to_weight) < ctx.conviction_min_master_pct:
                continue
            if _is_star_market(trade.ts_code) and not star_unlocked:
                ctx.metrics["blocked_688"] = ctx.metrics.get("blocked_688", 0) + 1
                continue
            if ctx.allowed_buys is not None and (acct_code, trade.ts_code) not in ctx.allowed_buys:
                continue
            nav_pre = _nav_for_account(fund, _build_raw_prices(fund, batch_prices), ctx, acct_code)
            sl = fund._slice(acct_code, trade.ts_code)
            if sl.qty > 1e-12:
                result = fund.apply_increase_existing_slice(
                    acct_code,
                    trade.ts_code,
                    trade.stock_name,
                    float(trade.from_weight),
                    float(trade.to_weight),
                    raw_px,
                    nav_pre,
                )
                fund.trade_counts[trade.ts_code] += 1
                if result and abs(result.qty_delta) > 1e-12:
                    if result.qty_delta > 0:
                        _adjust_pool_cash(ctx, acct_code, -result.qty_delta * _slippage_price(raw_px, True))
                    nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                    trade_logs.append(
                        _result_to_log(trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after)
                    )
            elif ctx.open_on_signal:
                if ctx.top_portfolio_n > 0 and ctx.portfolio_weights:
                    pw = ctx.portfolio_weights.get(acct_code, 0.0)
                    if pw < ctx.min_follow_weight:
                        continue
                if ctx.weighted_follow and ctx.portfolio_weights:
                    pw = ctx.portfolio_weights.get(acct_code, 0.0)
                    if pw < ctx.min_follow_weight:
                        continue
                target = min(ctx.max_stock_pct, float(trade.to_weight) / 100.0)
                if ctx.weighted_follow and ctx.portfolio_weights:
                    pw = max(ctx.portfolio_weights.get(acct_code, 0.0), 0.0)
                    n = max(len(ctx.portfolio_weights), 1)
                    target = min(ctx.max_stock_pct, target * min(1.0, pw * n))
                if ctx.decay_mode:
                    target = min(
                        ctx.max_stock_pct,
                        target * _decay_intensity_multiplier(ctx, trade.ts_code),
                    )
                if ctx.conviction_tier_mode:
                    target = _conviction_target_for_open(
                        ctx,
                        mirror,
                        acct_code,
                        trade.ts_code,
                        float(trade.to_weight),
                        trade_time,
                    )
                    if target <= 0:
                        continue
                elif ctx.partition_mimic_mode:
                    target = min(
                        ctx.max_stock_pct,
                        float(trade.to_weight) / 100.0 * ctx.portfolio_budget_pct,
                    )
                _ensure_position_slot(
                    fund, ctx, raw_prices, trade_logs, trade_time, trade.ts_code
                )
                nav_pre = _nav_for_account(fund, _build_raw_prices(fund, batch_prices), ctx, acct_code)
                bought = _try_buy_to_target(
                    fund,
                    trade.ts_code,
                    raw_px,
                    target,
                    nav_pre,
                    {acct_code: float(trade.to_weight)},
                    star_unlocked,
                )
                fund.trade_counts[trade.ts_code] += 1
                if bought > 0:
                    _adjust_pool_cash(ctx, acct_code, -bought * _slippage_price(raw_px, True))
                    nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                    trade_logs.append(
                        {
                            "trade_time": trade_time,
                            "source_portfolio": acct_code,
                            "source_name": acct_name,
                            "action": "信号开仓",
                            "ts_code": trade.ts_code,
                            "stock_name": trade.stock_name,
                            "qty_delta": bought,
                            "trigger": "单组合信号",
                            "nav_after": round(nav_after, 2),
                            "price": round(raw_px, 4),
                            "our_weight_pct": _our_weight_pct(
                                fund, trade.ts_code, _build_raw_prices(fund, batch_prices), nav=nav_after
                            ),
                            "master_from": 0.0,
                            "master_to": float(trade.to_weight),
                        }
                    )

    _update_mirror(mirror, batch)
    ctx.mirror = dict(mirror)

    batch_buy_codes = {
        t.ts_code for _, _, t in batch if t.to_weight > t.from_weight + 1e-9
    }

    # 共识开仓 / 补仓（仅非 open_on_signal 模式使用候选池）
    if not leader_mode and not ctx.open_on_signal:
        candidates: list[tuple[str, float, dict[str, float]]] = []
        codes = set(batch_buy_codes)
        for (_p, c), _w in mirror.items():
            codes.add(c)
        for code in codes:
            holders = _holders_for_code(mirror, code)
            if not holders:
                continue
            if use_weighted:
                score = _weighted_consensus(mirror, code, ctx.portfolio_weights)
                target = _target_pct_by_weighted(score, ctx.max_stock_pct)
                if score < ctx.weighted_open_threshold and fund.physical_qty(code) <= 1e-12:
                    continue
                if score < ctx.weighted_open_threshold and fund.physical_qty(code) > 1e-12:
                    target = 0.0
            else:
                count = len(holders)
                if count < ctx.min_consensus_count and fund.physical_qty(code) <= 1e-12:
                    continue
                score = float(count)
                target = _target_pct_by_count(count, ctx.max_stock_pct)
            if target <= 0:
                if use_weighted:
                    wscore = _weighted_consensus(mirror, code, ctx.portfolio_weights)
                    if fund.physical_qty(code) > 1e-12 and wscore < ctx.weighted_open_threshold:
                        sell_price = _slippage_price(
                            batch_prices.get(code) or fund.last_raw_marks.get(code, 0), False
                        )
                        nav_pre = fund.nav(_build_raw_prices(fund, batch_prices))
                        for p in list(holders):
                            sl = fund._slice(p, code)
                            if sl.qty > 1e-12:
                                fund._sell_from_slice(p, code, sl.qty, sell_price, nav_pre)
                continue
            candidates.append((code, score, holders))

        candidates.sort(key=lambda x: (-x[1], x[0]))
        for code, _score, holders in candidates:
            raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
            if not raw_px:
                continue
            nav = fund.nav(_build_raw_prices(fund, batch_prices))
            if use_weighted:
                target = _target_pct_by_weighted(
                    _weighted_consensus(mirror, code, ctx.portfolio_weights), ctx.max_stock_pct
                )
            else:
                target = _target_pct_by_count(len(holders), ctx.max_stock_pct)
            bought = _try_buy_to_target(fund, code, raw_px, target, nav, holders, star_unlocked)
            if bought > 0:
                trade_logs.append(
                    {
                        "trade_time": trade_time,
                        "source_portfolio": "共识",
                        "action": "共识买入",
                        "ts_code": code,
                        "stock_name": fund.stock_names.get(code, code),
                        "qty_delta": bought,
                        "trigger": "共识开仓" if fund.physical_qty(code) <= bought + 1e-6 else "共识加仓",
                        "nav_after": round(fund.nav(_build_raw_prices(fund, batch_prices)), 2),
                    }
                )

        if ctx.consensus_boost:
            nav = fund.nav(_build_raw_prices(fund, batch_prices))
            boost_codes = {c for c, h in fund.holdings.items() if h.qty > 1e-12}
            boost_codes |= {c for (_p, c), w in mirror.items() if w > 1e-9}
            for code in boost_codes:
                holders = _holders_for_code(mirror, code)
                if len(holders) < 2:
                    continue
                raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
                if not raw_px:
                    continue
                bought = _try_buy_to_target(
                    fund, code, raw_px, ctx.consensus_boost_pct, nav, holders, star_unlocked
                )
                if bought > 0:
                    trade_logs.append(
                        {
                            "trade_time": trade_time,
                            "source_portfolio": "共识",
                            "action": "共识加仓",
                            "ts_code": code,
                            "stock_name": fund.stock_names.get(code, code),
                            "qty_delta": bought,
                            "trigger": "双共识加仓",
                            "nav_after": round(fund.nav(_build_raw_prices(fund, batch_prices)), 2),
                        }
                    )
    else:
        # 头狼：映射主账户目标仓位
        cap_default = ctx.max_stock_pct
        belief_codes: set[str] = set()
        for acct_code, _, trade in batch:
            if acct_code != leader:
                continue
            code = trade.ts_code
            if trade.to_weight <= 1e-9:
                continue
            others = sum(
                1 for p, w in _holders_for_code(mirror, code).items() if p != leader and w > 1e-9
            )
            batch_others = sum(
                1
                for p, _, t in batch
                if t.ts_code == code and p != leader and t.to_weight > t.from_weight + 1e-9
            )
            if (
                belief_mode
                and trade.to_weight >= 35.0
                and (others + batch_others) >= 3
                and trade.to_weight >= trade.from_weight
            ):
                belief_codes.add(code)

        for acct_code, acct_name, trade in batch:
            if acct_code != leader:
                continue
            code = trade.ts_code
            raw_px = batch_prices.get(code)
            if not raw_px:
                continue
            nav = fund.nav(_build_raw_prices(fund, batch_prices))
            cap = ctx.belief_cap_pct if code in belief_codes else ctx.max_stock_pct
            master_pct = float(trade.to_weight) / 100.0
            target = min(cap, master_pct * ctx.main_account_pct)
            phys = fund.physical_qty(code)
            if trade.to_weight <= 1e-9 and phys <= 1e-12:
                continue
            if trade.to_weight > trade.from_weight + 1e-9 or (phys <= 1e-12 and trade.to_weight > 1e-9):
                holders = {leader: float(trade.to_weight)}
                bought = _try_buy_to_target(fund, code, raw_px, target, nav, holders, star_unlocked)
                if bought > 0:
                    trade_logs.append(
                        {
                            "trade_time": trade_time,
                            "source_portfolio": leader,
                            "action": "头狼买入",
                            "ts_code": code,
                            "stock_name": trade.stock_name,
                            "qty_delta": bought,
                            "trigger": "信念" if code in belief_codes else "头狼",
                            "nav_after": round(fund.nav(_build_raw_prices(fund, batch_prices)), 2),
                        }
                    )

        if belief_mode:
            for code in belief_codes:
                raw_px = batch_prices.get(code) or fund.last_raw_marks.get(code)
                if not raw_px:
                    continue
                nav = fund.nav(_build_raw_prices(fund, batch_prices))
                phys_pct = phys = fund.physical_qty(code) * raw_px / nav if nav > 0 else 0
                if phys_pct >= ctx.belief_cap_pct - 1e-6:
                    continue
                boost_holders = _holders_for_code(mirror, code)
                boost_holders = {p: w for p, w in boost_holders.items() if p != leader}
                if len(boost_holders) < 3:
                    continue
                _try_buy_to_target(
                    fund, code, raw_px, ctx.belief_cap_pct, nav, {BOOST_PORTFOLIO: 1.0}, star_unlocked
                )

    for code, raw_px in batch_prices.items():
        fund.last_raw_marks[code] = raw_px
    for code, hfq in batch_hfq.items():
        fund.last_hfq_marks[code] = hfq


def _run_single_portfolio_follow(
    trades: list[tuple[str, str, TradeInput]],
    capital: float,
    max_stock_pct: float,
) -> dict[str, Any]:
    from xueqiu.domain.copy_backtest import _result_to_log

    fund = SliceLedger(initial_cash=capital)
    ts_codes = {t.ts_code for _, _, t in trades}
    trade_dates = {fmt_trade_date(t.trade_time) for _, _, t in trades}
    adj_map = load_adj_map(ts_codes, trade_dates)
    mirror: dict[tuple[str, str], float] = {}
    equity_curve: list[dict] = []
    trade_logs: list[dict] = []

    for trade_time, batch in _group_batches(trades):
        batch_prices: dict[str, float] = {}
        batch_hfq: dict[str, float] = {}
        for acct_code, acct_name, trade in batch:
            raw_px = resolve_trade_price_raw(trade, adj_map)
            hfq_px = resolve_price_hfq(trade, adj_map)
            if not raw_px or raw_px <= 0:
                continue
            batch_prices[trade.ts_code] = raw_px
            if hfq_px and hfq_px > 0:
                batch_hfq[trade.ts_code] = hfq_px
            fund.stock_names[trade.ts_code] = trade.stock_name

        if not batch_prices:
            continue

        nav_before = fund.nav(_build_raw_prices(fund, batch_prices))
        star_unlocked = nav_before - capital >= STAR_UNLOCK_PROFIT - 1e-6

        for acct_code, acct_name, trade in batch:
            raw_px = batch_prices.get(trade.ts_code)
            hfq_px = batch_hfq.get(trade.ts_code)
            if not raw_px:
                continue
            if trade.to_weight + 1e-9 < trade.from_weight:
                nav_pre = fund.nav(_build_raw_prices(fund, batch_prices))
                result = fund.apply_reduce_signal(
                    acct_code,
                    trade.ts_code,
                    trade.stock_name,
                    float(trade.from_weight),
                    float(trade.to_weight),
                    raw_px,
                    nav_pre,
                )
                if result:
                    nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                    trade_logs.append(
                        _result_to_log(trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after)
                    )
            elif trade.to_weight > trade.from_weight + 1e-9:
                if _is_star_market(trade.ts_code) and not star_unlocked:
                    continue
                nav_pre = fund.nav(_build_raw_prices(fund, batch_prices))
                sl = fund._slice(acct_code, trade.ts_code)
                if sl.qty > 1e-12:
                    result = fund.apply_increase_existing_slice(
                        acct_code,
                        trade.ts_code,
                        trade.stock_name,
                        float(trade.from_weight),
                        float(trade.to_weight),
                        raw_px,
                        nav_pre,
                    )
                    if result and abs(result.qty_delta) > 1e-12:
                        nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                        trade_logs.append(
                            _result_to_log(
                                trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after
                            )
                        )
                else:
                    target = min(max_stock_pct, float(trade.to_weight) / 100.0)
                    bought = _try_buy_to_target(
                        fund, trade.ts_code, raw_px, target, nav_pre, {acct_code: float(trade.to_weight)}, star_unlocked
                    )
                    if bought > 0:
                        trade_logs.append(
                            {
                                "trade_time": trade_time,
                                "source_portfolio": acct_code,
                                "action": "独立开仓",
                                "ts_code": trade.ts_code,
                                "stock_name": trade.stock_name,
                                "qty_delta": bought,
                                "nav_after": round(fund.nav(_build_raw_prices(fund, batch_prices)), 2),
                            }
                        )

        _update_mirror(mirror, batch)
        cap_logs = enforce_stock_cap(fund, _build_raw_prices(fund, batch_prices), max_stock_pct, trade_time)
        trade_logs.extend(cap_logs)

        for code, raw_px in batch_prices.items():
            fund.last_raw_marks[code] = raw_px
        for code, hfq in batch_hfq.items():
            fund.last_hfq_marks[code] = hfq

        nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
        nav_hfq = fund.nav_hfq(_build_hfq_prices(fund, batch_hfq))
        equity_curve.append(
            {
                "trade_time": trade_time,
                "total_nav": round(nav_after, 2),
                "total_nav_hfq": round(nav_hfq, 2),
                "cum_return_pct": round((nav_hfq / capital - 1.0) * 100, 2),
            }
        )

    acct = trades[0][0] if trades else ""
    return _finalize(
        fund,
        RunContext(initial_capital=capital),
        {acct: acct},
        trades,
        equity_curve,
        trade_logs,
        StrategyId.ROUTE_B_MERGED,
    )


def _enrich_legacy_result(result: dict) -> dict:
    eq = result.get("equity_curve", [])
    result["max_drawdown_pct"] = _max_drawdown(eq)
    result["sharpe_proxy"] = _sharpe_proxy(eq)
    result["return_since_2023"] = _return_since(eq, "2023-01-01")
    result["return_since_2020"] = _return_since(eq, "2020-01-01")
    result["orphan_sell_count"] = sum(
        1 for log in result.get("trade_logs", []) if "无slice" in str(log.get("action", ""))
    )
    result["rotate_count"] = result.get("rotate_triggers", 0)
    result["rebalance_count"] = result.get("rebalance_triggers", 0)
    result["position_count"] = len(result.get("positions", []))
    return result


def run_strategy(
    strategy_id: StrategyId,
    initial_capital: float = INITIAL_CAPITAL,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    import xueqiu.domain.copy_backtest as cb

    cb._RUN_CFG = None
    if strategy_id in (StrategyId.LEGACY_K5, StrategyId.LEGACY_K10):
        k = 5 if strategy_id == StrategyId.LEGACY_K5 else 10
        spec = _strategy_spec(strategy_id)
        return _enrich_legacy_result(
            run_backtest(
                BacktestConfig(
                    initial_capital=initial_capital,
                    max_positions=k,
                    forbid_rotate_heavy=False,
                )
            )
            | {
                "strategy_id": strategy_id.value,
                "label": spec.label,
                "description": spec.description,
                "style": spec.style,
            }
        )

    account_names, all_trades = load_portfolio_trades()
    if not all_trades:
        raise ValueError("无调仓数据")

    ts_codes = {t.ts_code for _, _, t in all_trades}
    trade_dates = {fmt_trade_date(t.trade_time) for _, _, t in all_trades}
    adj_map = load_adj_map(ts_codes, trade_dates)
    portfolio_curves = _precompute_portfolio_curves(all_trades)

    spec = _strategy_spec(strategy_id)
    ctx = RunContext(initial_capital=initial_capital)
    if strategy_id == StrategyId.ROUTE_B_MERGED:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
    elif strategy_id == StrategyId.ROUTE_B_MERGED_BOOST:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
    elif strategy_id == StrategyId.ROUTE_D_BOOST_AGGRESSIVE:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
        ctx.consensus_boost_pct = 0.20
        ctx.max_stock_pct = 0.25
    elif strategy_id == StrategyId.ROUTE_D_BOOST_CAP12:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
        ctx.max_positions = 12
    elif strategy_id == StrategyId.ROUTE_E_FOF_TOP3_BOOST:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
        ctx.fof_filter = True
        ctx.fof_top_n = 3
        ctx.fof_lookback_days = 180
    elif strategy_id == StrategyId.ROUTE_E_LAG_RESONANCE_BOOST:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
        ctx.lag_resonance_boost = True
        ctx.lag_boost_pct = 0.22
        ctx.lag_resonance_days = 5
    elif strategy_id == StrategyId.ROUTE_E_DECAY_INTENSITY:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.decay_mode = True
        ctx.decay_half_life_days = 45
    elif strategy_id == StrategyId.ROUTE_E_DUAL_POOL_BOOST:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.consensus_boost = True
        ctx.dual_pool_mode = True
        ctx.portfolio_pool = dict(STYLE_POOL_MAP)
        half = initial_capital * 0.5
        ctx.pool_cash_alloc = {STYLE_POOL_ACTIVE: half, STYLE_POOL_STABLE: half}
    elif strategy_id == StrategyId.ROUTE_F_PARTITION_MIMIC:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.partition_mimic_mode = True
        ctx.portfolio_budget_pct = PARTITION_BUDGET_PCT
    elif strategy_id == StrategyId.ROUTE_G_CONVICTION_TRUST:
        ctx.min_consensus_count = 1
        ctx.open_on_signal = True
        ctx.conviction_tier_mode = True
        # 16 师傅合并跟单：降低单票拥挤，放宽槽位，并禁止为新信号卖出仍被师傅重仓的核心票。
        ctx.max_stock_pct = 0.25
        ctx.belief_cap_pct = 0.25
        ctx.max_positions = 20
        ctx.forbid_rotate_heavy = True
        ctx.heavy_leg_events = build_heavy_leg_events(all_trades, adj_map)

        cb._RUN_CFG = BacktestConfig(
            initial_capital=initial_capital,
            max_stock_pct=0.25,
            max_positions=20,
            min_new_position_pct=2.0,
            forbid_rotate_heavy=True,
        )
    elif strategy_id == StrategyId.ROUTE_C_FUNNEL:
        ctx.open_on_signal = True
        ctx.max_positions = 5
        ctx.signal_funnel_top_k = 3
        ctx.portfolio_return_lookback = 365
    elif strategy_id == StrategyId.ROUTE_C_BELIEF:
        ctx.open_on_signal = True
        ctx.max_positions = 5
        ctx.belief_bet_mode = True
        ctx.belief_min_master_pct = 35.0
        ctx.belief_cap_pct = 0.35
        ctx.top_portfolio_n = 3
        ctx.portfolio_return_lookback = 365
    elif strategy_id == StrategyId.ROUTE_C_WINNER:
        ctx.open_on_signal = True
        ctx.max_positions = 8
        ctx.winner_cut_mode = True
        ctx.loser_cut_pct = -15.0
        ctx.pyramid_cap_pct = 0.30
    elif strategy_id == StrategyId.ROUTE_C_WEIGHTED:
        ctx.open_on_signal = True
        ctx.max_positions = 10
        ctx.weighted_follow = True
        ctx.portfolio_return_lookback = 365
    elif strategy_id == StrategyId.ROUTE_A_LEADER_100:
        ctx.main_account_pct = 1.0

    use_weighted = False
    leader_mode = strategy_id in (StrategyId.ROUTE_A_LEADER, StrategyId.ROUTE_A_LEADER_100)
    belief_mode = False

    fund = SliceLedger(initial_cash=initial_capital)
    mirror: dict[tuple[str, str], float] = {}
    equity_curve: list[dict] = []
    trade_logs: list[dict] = []
    last_quarter = ""
    last_pool_quarter = ""
    last_month = ""
    last_fof_month = ""
    leader_switches = 0
    fof_switches = 0

    if ctx.fof_filter:
        first_date = start_date or all_trades[0][2].trade_time
        ctx.active_portfolios = _pick_top_portfolios(
            portfolio_curves, first_date, ctx.fof_lookback_days, ctx.fof_top_n
        )

    for trade_time, batch in _group_batches(all_trades):
        d = trade_time[:10]
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            break

        month_key = trade_time[:7]
        if month_key != last_month and (ctx.weighted_follow or ctx.top_portfolio_n > 0):
            last_month = month_key
            rets = _portfolio_return_rank(portfolio_curves, trade_time, ctx.portfolio_return_lookback)
            ctx.portfolio_weights = _softmax_weights(rets)

        qk = _quarter_key(trade_time)
        if qk != last_quarter:
            last_quarter = qk
            if use_weighted:
                rets = {
                    code: _rolling_return(curve, trade_time, ctx.leader_lookback_days)
                    for code, curve in portfolio_curves.items()
                }
                rets_clean = {k: (v if v is not None else -1.0) for k, v in rets.items()}
                ctx.portfolio_weights = _softmax_weights(rets_clean)
            if leader_mode:
                new_leader = _pick_leader(portfolio_curves, trade_time, ctx.leader_lookback_days)
                if ctx.current_leader and new_leader != ctx.current_leader:
                    leader_switches += 1
                ctx.current_leader = new_leader

        batch_prices: dict[str, float] = {}
        batch_hfq: dict[str, float] = {}
        for acct_code, acct_name, trade in batch:
            raw_px = resolve_trade_price_raw(trade, adj_map)
            hfq_px = resolve_price_hfq(trade, adj_map)
            if not raw_px or raw_px <= 0:
                continue
            batch_prices[trade.ts_code] = raw_px
            if hfq_px and hfq_px > 0:
                batch_hfq[trade.ts_code] = hfq_px
            fund.stock_names[trade.ts_code] = trade.stock_name

        if not batch_prices:
            continue

        if ctx.fof_filter and month_key != last_fof_month:
            last_fof_month = month_key
            new_active = _pick_top_portfolios(
                portfolio_curves, trade_time, ctx.fof_lookback_days, ctx.fof_top_n
            )
            dropped = ctx.active_portfolios - new_active
            raw_for_liq = _build_raw_prices(fund, batch_prices)
            for p in dropped:
                _liquidate_portfolio_slices(fund, p, raw_for_liq, trade_logs, trade_time, ctx)
            if new_active != ctx.active_portfolios:
                fof_switches += 1
            ctx.active_portfolios = new_active

        if ctx.dual_pool_mode and not ctx.partition_mimic_mode and qk != last_pool_quarter:
            last_pool_quarter = qk
            _rebalance_pool_cash(fund, _build_raw_prices(fund, batch_prices), ctx)
            ctx.metrics["pool_rebalance"] = ctx.metrics.get("pool_rebalance", 0) + 1

        leader = ctx.current_leader if leader_mode else ""
        if leader_mode and not leader:
            leader = _pick_leader(portfolio_curves, trade_time, ctx.leader_lookback_days)
            ctx.current_leader = leader

        _prepare_funnel_allows(ctx, batch, trade_time, portfolio_curves)

        _prune_recent_buys(ctx, trade_time)
        _apply_decay_elapsed(ctx, trade_time)

        _run_owners_batch(
            fund,
            batch,
            batch_prices,
            batch_hfq,
            trade_time,
            mirror,
            ctx,
            trade_logs,
            use_weighted=use_weighted,
            leader=leader,
            leader_mode=leader_mode,
            belief_mode=belief_mode,
        )

        _update_decay_scores_from_batch(ctx, batch)

        raw_prices = _build_raw_prices(fund, batch_prices)
        nav_before = fund.nav(raw_prices)
        star_unlocked = nav_before - initial_capital >= STAR_UNLOCK_PROFIT - 1e-6

        if ctx.open_on_signal and ctx.consensus_boost:
            _apply_consensus_boost_open(
                fund, mirror, batch_prices, ctx, trade_logs, trade_time, star_unlocked
            )
        _record_batch_buys(ctx, batch, trade_time)
        if ctx.lag_resonance_boost:
            _apply_lag_resonance_boost(
                fund, batch, batch_prices, ctx, trade_logs, trade_time, star_unlocked
            )
        _apply_belief_bets(
            fund, batch, mirror, batch_prices, ctx, trade_logs, trade_time, star_unlocked
        )
        _apply_winner_loser_rules(
            fund, batch, batch_prices, ctx, trade_logs, trade_time, star_unlocked
        )

        raw_prices = _build_raw_prices(fund, batch_prices)
        cap_logs = enforce_stock_cap(fund, raw_prices, ctx.max_stock_pct, trade_time)
        trade_logs.extend(cap_logs)

        nav_after = fund.nav(raw_prices)
        nav_hfq = fund.nav_hfq(_build_hfq_prices(fund, batch_hfq))
        equity_curve.append(
            {
                "trade_time": trade_time,
                "total_nav": round(nav_after, 2),
                "total_nav_hfq": round(nav_hfq, 2),
                "cum_return_pct": round((nav_hfq / initial_capital - 1.0) * 100, 2),
                "profit": round(nav_after - initial_capital, 2),
                "profit_hfq": round(nav_hfq - initial_capital, 2),
            }
        )

    extra: dict[str, Any] = {}
    if leader_mode:
        extra["leader_switches"] = leader_switches
    if ctx.fof_filter:
        extra["fof_switches"] = fof_switches
    result = _finalize(
        fund, ctx, account_names, all_trades, equity_curve, trade_logs, strategy_id, extra
    )
    cb._RUN_CFG = None
    result["label"] = spec.label
    result["description"] = spec.description
    result["style"] = spec.style
    result["trade_log_count"] = len(trade_logs)
    if equity_curve:
        result["entry_date"] = start_date or equity_curve[0]["trade_time"][:10]
        result["start_time"] = equity_curve[0]["trade_time"]
        result["end_time"] = equity_curve[-1]["trade_time"]
    else:
        result["entry_date"] = start_date or ""
        result["start_time"] = ""
        result["end_time"] = ""
    result["return_since_entry"] = result.get("return_pct")
    return result


def list_strategy_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": s.id.value,
            "label": s.label,
            "description": s.description,
            "style": s.style,
        }
        for s in STRATEGY_CATALOG
    ]


def strategy_to_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": result.get("strategy_id"),
        "label": result.get("label"),
        "description": result.get("description"),
        "style": result.get("style"),
        "return_pct": result.get("return_pct"),
        "return_since_entry": result.get("return_since_entry", result.get("return_pct")),
        "entry_date": result.get("entry_date"),
        "return_since_2020": result.get("return_since_2020"),
        "return_since_2023": result.get("return_since_2023"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "sharpe_proxy": result.get("sharpe_proxy"),
        "cash_pct": result.get("cash_pct"),
        "position_count": result.get("position_count"),
        "orphan_sell_count": result.get("orphan_sell_count"),
        "rotate_count": result.get("rotate_count"),
        "rebalance_count": result.get("rebalance_count"),
        "final_nav_hfq": result.get("final_nav_hfq"),
        "current_leader": result.get("current_leader"),
        "leader_switches": result.get("leader_switches"),
    }


def _normalize_equity_point(point: dict[str, Any], initial_capital: float) -> dict[str, Any]:
    total_nav = float(point.get("total_nav") or initial_capital)
    total_nav_hfq = float(point.get("total_nav_hfq") or total_nav)
    out = dict(point)
    out["total_nav"] = round(total_nav, 2)
    out["total_nav_hfq"] = round(total_nav_hfq, 2)
    out.setdefault("cum_return_pct", round((total_nav_hfq / initial_capital - 1.0) * 100, 2))
    out.setdefault("profit", round(total_nav - initial_capital, 2))
    out.setdefault("profit_hfq", round(total_nav_hfq - initial_capital, 2))
    return out


def _normalize_trade_log(log: dict[str, Any]) -> dict[str, Any]:
    out = dict(log)
    out.setdefault("trade_time", "")
    out.setdefault("source_portfolio", "")
    out.setdefault("source_name", out.get("source_portfolio") or "")
    out.setdefault("stock_name", out.get("ts_code") or "")
    out.setdefault("ts_code", "")
    out.setdefault("master_from", None)
    out.setdefault("master_to", None)
    out.setdefault("action", "")
    out.setdefault("price", 0.0)
    out.setdefault("price_hfq", None)
    out.setdefault("qty_delta", 0.0)
    out.setdefault("our_weight_pct", 0.0)
    out.setdefault("nav_after", 0.0)
    out.setdefault("note", "")
    out.setdefault("trigger", None)
    return out


def strategy_to_backtest_response(result: dict[str, Any]) -> dict[str, Any]:
    """将策略结果补齐为 CopyBacktestResponse 字段。"""
    ic = float(result.get("initial_capital") or INITIAL_CAPITAL)
    final_hfq = float(result.get("final_nav_hfq") or ic)
    final_nav = float(result.get("final_nav") or final_hfq)
    out = dict(result)
    out.setdefault("profit", round(final_nav - ic, 2))
    out.setdefault("profit_hfq", round(final_hfq - ic, 2))
    out.setdefault("return_pct", round((final_hfq / ic - 1.0) * 100, 2))
    out.setdefault("return_pct_raw", round((final_nav / ic - 1.0) * 100, 2))
    out.setdefault("cash", 0.0)
    out.setdefault("cash_pct", 0.0)
    out.setdefault("portfolio_count", out.get("portfolio_count", 0))
    out.setdefault("start_time", "")
    out.setdefault("end_time", "")
    out.setdefault("blocked_688", 0)
    out.setdefault("cap_triggers", 0)
    out.setdefault("rotate_triggers", out.get("rotate_count", 0))
    out.setdefault("rebalance_triggers", out.get("rebalance_count", 0))
    out.setdefault("skipped_lot", 0)
    out.setdefault("skipped_small", 0)
    out.setdefault("trade_log_count", len(out.get("trade_logs") or []))
    out.setdefault("star_unlocked", False)
    out.setdefault("max_stock_pct", 20.0)
    out.setdefault("min_new_position_pct", 1.0)
    out.setdefault("max_positions", 0)
    out.setdefault("overview_win_rate", 0.0)
    out.setdefault("diagnostics", {})
    out.setdefault("source_stats", {})
    out.setdefault("positions", [])
    out.setdefault("grouped_stats", [])
    out["equity_curve"] = [_normalize_equity_point(p, ic) for p in (out.get("equity_curve") or [])]
    out["trade_logs"] = [_normalize_trade_log(log) for log in (out.get("trade_logs") or [])]
    return out


def run_strategy_compare(
    strategy_ids: list[str],
    initial_capital: float = INITIAL_CAPITAL,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    entry_sweep_dates: list[str] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for sid in strategy_ids:
        spec = next((s for s in STRATEGY_CATALOG if s.id.value == sid), None)
        if not spec:
            raise ValueError(f"未知策略: {sid}")
        raw = run_strategy(
            spec.id,
            initial_capital=initial_capital,
            start_date=start_date,
            end_date=end_date,
        )
        results.append(strategy_to_summary(raw))
    sort_key = "return_since_entry" if start_date else "return_pct"
    results.sort(key=lambda x: -(x.get(sort_key) or 0))

    entry_sweep: list[dict[str, Any]] = []
    if entry_sweep_dates:
        for entry_d in entry_sweep_dates:
            for sid in strategy_ids:
                spec = next((s for s in STRATEGY_CATALOG if s.id.value == sid), None)
                if not spec:
                    continue
                raw = run_strategy(
                    spec.id,
                    initial_capital=initial_capital,
                    start_date=entry_d,
                    end_date=end_date,
                )
                summary = strategy_to_summary(raw)
                entry_sweep.append(
                    {
                        "date": entry_d,
                        "strategy_id": sid,
                        "label": summary.get("label"),
                        "return_pct": summary.get("return_since_entry"),
                        "max_drawdown_pct": summary.get("max_drawdown_pct"),
                        "cash_pct": summary.get("cash_pct"),
                        "position_count": summary.get("position_count"),
                    }
                )

    return {
        "initial_capital": initial_capital,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
        "consensus_stats": analyze_consensus_stats(),
        "entry_sweep": entry_sweep,
    }


def analyze_consensus_stats() -> dict[str, Any]:
    """统计数据中组合共识出现频率（解释共识策略能触发多少次）。"""
    _, all_trades = load_portfolio_trades()
    mirror: dict[tuple[str, str], float] = {}
    dist: dict[int, int] = defaultdict(int)
    same_day_pair = 0
    batches = _group_batches(all_trades)
    for _tt, batch in batches:
        _update_mirror(mirror, batch)
        by_code: dict[str, int] = defaultdict(int)
        for (_p, c), w in mirror.items():
            if w > 1e-9:
                by_code[c] += 1
        for cnt in by_code.values():
            dist[cnt] += 1
        buys: dict[str, set[str]] = defaultdict(set)
        for acct, _, t in batch:
            if t.to_weight > t.from_weight + 1e-9:
                buys[t.ts_code].add(acct)
        if any(len(v) >= 2 for v in buys.values()):
            same_day_pair += 1
    return {
        "total_batches": len(batches),
        "same_day_2plus_buy_batches": same_day_pair,
        "holder_snapshots": dict(sorted(dist.items())),
        "pair_or_more_snapshots": sum(v for k, v in dist.items() if k >= 2),
        "triple_snapshots": dist.get(3, 0),
        "max_holders_ever": max(dist.keys()) if dist else 0,
    }


def run_all_strategies(initial_capital: float = INITIAL_CAPITAL) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for spec in STRATEGY_CATALOG:
        r = run_strategy(spec.id, initial_capital=initial_capital)
        r["label"] = spec.label
        r["description"] = spec.description
        results.append(r)
    return results


def compute_portfolio_benchmarks(initial_capital: float = INITIAL_CAPITAL) -> list[dict[str, Any]]:
    account_names, all_trades = load_portfolio_trades()
    by_code: dict[str, list[TradeInput]] = defaultdict(list)
    for acct, _, trade in all_trades:
        by_code[acct].append(trade)
    rows: list[dict[str, Any]] = []
    for acct, trades in sorted(by_code.items()):
        nav = compute_pseudo_nav(trades)
        eq = nav.get("equity_curve", [])
        eq_normalized = [
            {
                "trade_time": pt.get("trade_time", pt.get("trade_date", "")),
                "total_nav_hfq": pt.get("nav"),
                "nav": pt.get("nav"),
            }
            for pt in eq
        ]
        if eq_normalized:
            final_nav = float(eq_normalized[-1].get("nav") or initial_capital)
        else:
            final_nav = float(nav.get("final_nav") or initial_capital)
        rows.append(
            {
                "portfolio": acct,
                "name": account_names.get(acct.upper(), acct),
                "return_pct": round((final_nav / initial_capital - 1.0) * 100, 2),
                "return_since_2023": _return_since(eq_normalized, "2023-01-01"),
                "return_since_2020": _return_since(eq_normalized, "2020-01-01"),
                "max_drawdown_pct": _max_drawdown(eq_normalized),
            }
        )
    rows.sort(key=lambda x: -x["return_pct"])
    return rows
