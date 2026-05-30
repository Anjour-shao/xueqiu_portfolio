"""
虚拟净值法 (Pseudo-NAV)

时间轴仅包含调仓时刻（rebalance_trades.trade_time），不依赖每日全量日线。
价格来源：新浪后复权价 close_hfq；TuShare adj_factor 仅作后备。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from xueqiu.config import BENCHMARK_TS_CODE
from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.storage.db import benchmark_table, get_conn, quote_points_table, rebalance_trades_table


@dataclass
class TradeInput:
    id: int
    trade_time: str
    stock_name: str
    ts_code: str
    action: str
    from_weight: float
    to_weight: float
    weight_delta: float
    price: float | None
    price_hfq: float | None


def fmt_trade_date(trade_time: str) -> str:
    return trade_time[:10].replace("-", "")


def _parse_trade_dt(trade_time: str) -> datetime:
    text = trade_time.strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if len(text) >= 8 and text[:4].isdigit():
        if "-" in text[:10]:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        return datetime.strptime(text[:8], "%Y%m%d")
    return datetime.now()


def _as_of_time(quote_date: str | None, fallback: str) -> str:
    if quote_date and len(quote_date) >= 8:
        if quote_date[4] == "-":
            return f"{quote_date[:10]} 15:00:00"
        return f"{quote_date[:4]}-{quote_date[4:6]}-{quote_date[6:8]} 15:00:00"
    return fallback


def holding_days_between(start_time: str, end_time: str) -> int:
    start = _parse_trade_dt(start_time)
    end = _parse_trade_dt(end_time)
    return max(0, (end.date() - start.date()).days)


def resolve_price_hfq(trade: TradeInput, adj_map: dict[tuple[str, str], float]) -> float | None:
    if trade.price_hfq is not None and trade.price_hfq > 0:
        return float(trade.price_hfq)
    if trade.price is None or trade.price <= 0:
        return None
    code = to_xueqiu_code(trade.ts_code)
    trade_date = fmt_trade_date(trade.trade_time)
    adj = adj_map.get((code, trade_date), 1.0)
    if adj <= 0:
        adj = 1.0
    return float(trade.price) * adj


def load_adj_map(ts_codes: set[str], trade_dates: set[str]) -> dict[tuple[str, str], float]:
    if not ts_codes or not trade_dates:
        return {}
    normalized = {to_xueqiu_code(c) for c in ts_codes}
    with get_conn() as conn:
        rows = conn.execute(
            select(
                quote_points_table.c.ts_code,
                quote_points_table.c.trade_date,
                quote_points_table.c.adj_factor,
            ).where(
                quote_points_table.c.ts_code.in_(sorted(normalized))
                & quote_points_table.c.trade_date.in_(sorted(trade_dates))
            )
        ).fetchall()
    return {(str(r.ts_code).upper(), str(r.trade_date)): max(float(r.adj_factor), 1e-12) for r in rows}


def load_latest_hfq_marks(ts_codes: set[str]) -> dict[str, tuple[str, float]]:
    """从 quote_points.close_hfq 读取新浪后复权收盘价。"""
    if not ts_codes:
        return {}
    normalized = {to_xueqiu_code(c) for c in ts_codes}
    with get_conn() as conn:
        rows = conn.execute(
            select(
                quote_points_table.c.ts_code,
                quote_points_table.c.trade_date,
                quote_points_table.c.close_hfq,
            ).where(
                quote_points_table.c.ts_code.in_(sorted(normalized))
                & quote_points_table.c.close_hfq.isnot(None)
            )
        ).fetchall()

    best: dict[str, tuple[str, float]] = {}
    for row in rows:
        code = to_xueqiu_code(str(row.ts_code))
        trade_date = str(row.trade_date)
        close_hfq = float(row.close_hfq)
        if close_hfq <= 0:
            continue
        if code not in best or trade_date > best[code][0]:
            best[code] = (trade_date, close_hfq)
    return best


def load_latest_adj_factors(ts_codes: set[str]) -> dict[str, tuple[str, float]]:
    """读取各标的最新复权因子，用于后复权价换算为未复权价。"""
    if not ts_codes:
        return {}
    normalized = {to_xueqiu_code(c) for c in ts_codes}
    with get_conn() as conn:
        rows = conn.execute(
            select(
                quote_points_table.c.ts_code,
                quote_points_table.c.trade_date,
                quote_points_table.c.adj_factor,
            ).where(quote_points_table.c.ts_code.in_(sorted(normalized)))
        ).fetchall()

    best: dict[str, tuple[str, float]] = {}
    for row in rows:
        code = to_xueqiu_code(str(row.ts_code))
        trade_date = str(row.trade_date)
        adj = float(row.adj_factor)
        if adj <= 0:
            continue
        if code not in best or trade_date > best[code][0]:
            best[code] = (trade_date, adj)
    return best


def _raw_mark_from_hfq(
    mark_hfq: float,
    vwap_hfq: float,
    raw_vwap: float | None,
    adj_entry: tuple[str, float] | None = None,
) -> float | None:
    """后复权现价 → 未复权现价；优先用持仓成本比例，避免 adj_factor=1 时换算失效。"""
    if mark_hfq <= 0:
        return None
    if raw_vwap and raw_vwap > 0 and vwap_hfq > 0:
        return round(mark_hfq * raw_vwap / vwap_hfq, 4)
    if adj_entry and adj_entry[1] > 1.0 + 1e-9:
        return round(mark_hfq / adj_entry[1], 4)
    return None


def load_benchmark_closes(trade_dates: set[str]) -> dict[str, float]:
    if not trade_dates:
        return {}
    ts_code = BENCHMARK_TS_CODE.upper()
    with get_conn() as conn:
        rows = conn.execute(
            select(benchmark_table.c.trade_date, benchmark_table.c.close).where(
                benchmark_table.c.ts_code == ts_code
            )
        ).fetchall()
    min_date = min(trade_dates)
    return {
        str(r.trade_date): float(r.close)
        for r in rows
        if r.close and float(r.close) > 0 and str(r.trade_date) >= min_date
    }


def _bench_return_pct(bench_closes: dict[str, float], bench_base: float | None, date_key: str) -> float | None:
    if not bench_base or date_key not in bench_closes:
        return None
    return round((bench_closes[date_key] / bench_base - 1.0) * 100, 2)


INITIAL_CAPITAL = 1.0
ENGINE_VERSION = "virtual_fund_v3"


@dataclass
class Holding:
    qty: float = 0.0
    vwap: float = 0.0
    raw_vwap: float = 0.0


@dataclass
class _StockStats:
    """单票：每次减仓/卖出记录 (组合权重变化点, leg收益率)。"""

    sell_legs: list[dict[str, float]] = field(default_factory=list)
    total_realized: float = 0.0


class VirtualFund:
    """
    单账本虚拟基金：股数、VWAP、NAV 同源，避免 PnL 与 NAV 双轨漂移。
    初始资金 INITIAL_CAPITAL（=1.0 时净值即累计倍数）。
    """

    def __init__(self) -> None:
        self.cash: float = INITIAL_CAPITAL
        self.holdings: dict[str, Holding] = {}
        self.stats: dict[str, _StockStats] = defaultdict(_StockStats)
        self.last_marks: dict[str, float] = {}
        self.last_weights: dict[str, float] = {}
        self.stock_names: dict[str, str] = {}
        self.trade_counts: dict[str, int] = defaultdict(int)
        self.last_actions: dict[str, str] = {}
        self.total_realized: float = 0.0
        self.holding_opened_at: dict[str, str] = {}
        self.last_holding_days: dict[str, int] = {}

    def record_holding_lifecycle(
        self,
        code: str,
        trade_time: str,
        from_weight: float,
        to_weight: float,
    ) -> None:
        """记录本轮开仓/清仓，用于持仓时长。"""
        if to_weight <= 1e-9:
            opened = self.holding_opened_at.pop(code, None)
            if opened:
                self.last_holding_days[code] = holding_days_between(opened, trade_time)
            return
        if from_weight <= 1e-9 and to_weight > 1e-9:
            self.holding_opened_at[code] = trade_time
        elif code not in self.holding_opened_at and to_weight > 1e-9:
            self.holding_opened_at[code] = trade_time

    def holding_duration_fields(self, code: str, as_of_time: str) -> dict[str, Any]:
        opened = self.holding_opened_at.get(code)
        if opened:
            return {
                "is_holding": True,
                "holding_days": holding_days_between(opened, as_of_time),
                "holding_opened_at": opened,
            }
        last_days = self.last_holding_days.get(code)
        return {
            "is_holding": False,
            "holding_days": last_days,
            "holding_opened_at": None,
        }

    def nav(self, prices: dict[str, float]) -> float:
        position_value = 0.0
        for code, holding in self.holdings.items():
            if holding.qty <= 0:
                continue
            price = prices.get(code) or self.last_marks.get(code)
            if price and price > 0:
                position_value += holding.qty * price
        total = self.cash + position_value
        return total if total > 0 else INITIAL_CAPITAL

    def _holding(self, code: str) -> Holding:
        if code not in self.holdings:
            self.holdings[code] = Holding()
        return self.holdings[code]

    def _bootstrap_if_needed(
        self,
        code: str,
        from_weight: float,
        price: float,
        nav_pre: float,
        raw_price: float | None = None,
    ) -> None:
        """日志首笔即有仓位时，按 from_weight 与当日 NAV 补建底仓。"""
        holding = self._holding(code)
        if holding.qty > 1e-12 or from_weight <= 1e-9 or nav_pre <= 0:
            return
        qty = (from_weight / 100.0) * nav_pre / price
        if qty <= 1e-12:
            return
        holding.vwap = price
        holding.raw_vwap = raw_price if raw_price and raw_price > 0 else 0.0
        holding.qty = qty
        self.cash -= qty * price

    def rebalance_to_qty(
        self,
        code: str,
        target_qty: float,
        price: float,
        nav_pre: float,
        weight_sold_pct: float = 0.0,
        raw_price: float | None = None,
    ) -> tuple[float | None, float | None, float]:
        """调整至目标股数；卖出时记录 leg 与卖出权重。"""
        holding = self._holding(code)
        st = self.stats[code]
        delta = target_qty - holding.qty

        if abs(delta) <= 1e-12:
            return None, None, 0.0

        if delta > 0:
            if holding.qty <= 1e-12:
                st.sell_legs = []
            buy_qty = delta
            if holding.qty <= 1e-12:
                holding.vwap = price
                holding.raw_vwap = raw_price if raw_price and raw_price > 0 else 0.0
            else:
                holding.vwap = (holding.qty * holding.vwap + buy_qty * price) / (holding.qty + buy_qty)
                if raw_price and raw_price > 0 and holding.raw_vwap > 0:
                    holding.raw_vwap = (
                        holding.qty * holding.raw_vwap + buy_qty * raw_price
                    ) / (holding.qty + buy_qty)
                elif raw_price and raw_price > 0:
                    holding.raw_vwap = raw_price
            holding.qty += buy_qty
            self.cash -= buy_qty * price
            return None, None, 0.0

        sell_qty = min(-delta, holding.qty)
        if sell_qty <= 1e-12 or holding.vwap <= 0:
            return None, None, 0.0

        vwap_before = holding.vwap
        realized = sell_qty * (price - vwap_before)
        leg_return_pct = round((price / vwap_before - 1.0) * 100, 2)
        self.cash += sell_qty * price
        holding.qty -= sell_qty

        if weight_sold_pct > 1e-9:
            st.sell_legs.append(
                {"weight_sold": weight_sold_pct, "leg_return_pct": leg_return_pct}
            )

        st.total_realized += realized
        self.total_realized += realized

        if holding.qty <= 1e-12:
            holding.qty = 0.0
            holding.vwap = 0.0
            holding.raw_vwap = 0.0

        contrib_pct = round(realized / nav_pre * 100, 4) if nav_pre > 0 else None
        return leg_return_pct, contrib_pct, realized

    def apply_trade(
        self,
        code: str,
        stock_name: str,
        from_weight: float,
        to_weight: float,
        price: float,
        nav_pre: float,
        raw_price: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        self.stock_names[code] = stock_name
        self._bootstrap_if_needed(code, from_weight, price, nav_pre, raw_price=raw_price)
        weight_sold = max(from_weight - to_weight, 0.0)
        target_qty = (to_weight / 100.0) * nav_pre / price if to_weight > 0 else 0.0
        leg, contrib, _ = self.rebalance_to_qty(
            code, target_qty, price, nav_pre, weight_sold_pct=weight_sold, raw_price=raw_price
        )
        return leg, contrib, leg

    def position_snapshot(self, code: str) -> tuple[float, float]:
        h = self.holdings.get(code)
        if not h or h.qty <= 1e-12:
            return 0.0, 0.0
        return h.qty, h.vwap

    def raw_cost(self, code: str) -> float | None:
        h = self.holdings.get(code)
        if not h or h.qty <= 1e-12 or h.raw_vwap <= 0:
            return None
        return h.raw_vwap

    def unrealized(self, code: str, mark: float) -> float:
        qty, vwap = self.position_snapshot(code)
        if qty <= 0 or vwap <= 0 or mark <= 0:
            return 0.0
        return (mark - vwap) * qty

    def stock_cum_return_pct(self, code: str, mark: float | None = None) -> float:
        """累计收益 = Σ(卖出权重×leg收益)/Σ卖出权重；仍持仓时并入剩余仓位浮动。"""
        st = self.stats[code]
        legs = st.sell_legs
        if not legs:
            return 0.0

        total_w = sum(leg["weight_sold"] for leg in legs)
        if total_w <= 0:
            return 0.0

        weighted = sum(leg["weight_sold"] * leg["leg_return_pct"] for leg in legs) / total_w

        qty, vwap = self.position_snapshot(code)
        if qty > 1e-12 and mark and mark > 0 and vwap > 0:
            unreal_pct = (mark / vwap - 1.0) * 100
            last_weight = self.last_weights.get(code, 0.0)
            if last_weight > 0:
                return round(
                    (weighted * total_w + unreal_pct * last_weight) / (total_w + last_weight),
                    2,
                )
        return round(weighted, 2)

    def grouped_row(
        self,
        code: str,
        trade_count: int,
        last_trade_time: str | None,
        mark: float | None = None,
    ) -> dict[str, Any]:
        st = self.stats[code]
        rc = len(st.sell_legs)
        wins = sum(1 for leg in st.sell_legs if leg["leg_return_pct"] >= 0)
        cum_return_pct = self.stock_cum_return_pct(code, mark)
        duration = self.holding_duration_fields(code, last_trade_time or "")
        return {
            "ts_code": code,
            "stock_name": self.stock_names.get(code, code),
            "events": trade_count,
            "realized_count": rc,
            "wins": wins,
            "losses": rc - wins,
            "win_rate": round(wins / rc * 100, 2) if rc else 0.0,
            "cum_return_pct": cum_return_pct,
            "avg_return_pct": cum_return_pct,
            "total_realized": round(st.total_realized, 6),
            "total_buy_cost": round(sum(leg["weight_sold"] for leg in st.sell_legs), 6),
            "last_trade_time": last_trade_time,
            **duration,
        }


def _enrich_equity_stages(nav_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    为每个调仓/盯市节点补充阶段分析字段，便于观察复利：
    - nav_before：本阶段起始净值
    - period_benchmark_return_pct：基准同区间涨跌
    - period_excess_return_pct：阶段超额
    - growth_attribution_pct：该阶段对总几何收益(log)的贡献占比（各阶段之和≈100%）
    """
    if not nav_records:
        return nav_records

    nav_end = float(nav_records[-1].get("nav") or INITIAL_CAPITAL)
    log_total = math.log(nav_end / INITIAL_CAPITAL) if nav_end > 0 else 0.0
    prev_nav = INITIAL_CAPITAL
    prev_bench_mult: float | None = None

    for record in nav_records:
        nav_after = float(record.get("nav") or prev_nav)
        record["nav_before"] = round(prev_nav, 6)

        bench_cum = record.get("benchmark_return_pct")
        period_bench: float | None = None
        if bench_cum is not None:
            bench_mult = 1.0 + float(bench_cum) / 100.0
            if prev_bench_mult is not None and prev_bench_mult > 0:
                period_bench = round((bench_mult / prev_bench_mult - 1.0) * 100, 2)
            prev_bench_mult = bench_mult
        record["period_benchmark_return_pct"] = period_bench

        period_ret = record.get("period_return_pct")
        if period_ret is not None and period_bench is not None:
            record["period_excess_return_pct"] = round(float(period_ret) - period_bench, 2)
        else:
            record["period_excess_return_pct"] = None

        if abs(log_total) > 1e-12 and nav_after > 0 and prev_nav > 0:
            record["growth_attribution_pct"] = round(math.log(nav_after / prev_nav) / log_total * 100, 2)
        else:
            record["growth_attribution_pct"] = 0.0

        prev_nav = nav_after

    return nav_records


def _build_event_prices(
    fund: VirtualFund,
    batch_hfq: dict[str, float],
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for code, holding in fund.holdings.items():
        if holding.qty <= 0:
            continue
        if code in batch_hfq:
            prices[code] = batch_hfq[code]
        elif code in fund.last_marks:
            prices[code] = fund.last_marks[code]
    for code, hfq in batch_hfq.items():
        prices[code] = hfq
    return prices


def compute_pseudo_nav(trades: list[TradeInput]) -> dict[str, Any]:
    if not trades:
        return _empty_result()

    ts_codes = {to_xueqiu_code(t.ts_code) for t in trades}
    trade_dates = {fmt_trade_date(t.trade_time) for t in trades}
    adj_map = load_adj_map(ts_codes, trade_dates)
    bench_closes = load_benchmark_closes(trade_dates)

    events: dict[str, list[TradeInput]] = defaultdict(list)
    for trade in trades:
        events[trade.trade_time].append(trade)

    fund = VirtualFund()
    nav_records: list[dict[str, Any]] = []
    trade_results: list[dict[str, Any]] = []
    stock_last_trade_time: dict[str, str] = {}

    bench_base: float | None = None
    prev_nav = INITIAL_CAPITAL

    for trade_time in sorted(events.keys()):
        batch = sorted(events[trade_time], key=lambda t: t.id)
        batch_hfq: dict[str, float] = {}

        for trade in batch:
            code = to_xueqiu_code(trade.ts_code)
            hfq = resolve_price_hfq(trade, adj_map)
            if hfq and hfq > 0:
                batch_hfq[code] = hfq
            fund.stock_names[code] = trade.stock_name
            fund.trade_counts[code] += 1
            fund.last_actions[code] = trade.action

        event_prices = _build_event_prices(fund, batch_hfq)
        nav_pre = fund.nav(event_prices)

        trade_date = trade_time[:10]
        date_key = fmt_trade_date(trade_time)
        if bench_base is None and date_key in bench_closes:
            bench_base = bench_closes[date_key]

        for trade in batch:
            code = to_xueqiu_code(trade.ts_code)
            hfq = resolve_price_hfq(trade, adj_map)
            stock_last_trade_time[code] = trade_time
            if not hfq or hfq <= 0:
                trade_results.append(_trade_row(trade, code, hfq, None, None, None, nav_pre))
                continue

            nav_at_trade = fund.nav(event_prices)
            raw_price = float(trade.price) if trade.price and trade.price > 0 else None
            leg_return_pct, account_contrib_pct, _ = fund.apply_trade(
                code=code,
                stock_name=trade.stock_name,
                from_weight=float(trade.from_weight),
                to_weight=float(trade.to_weight),
                price=hfq,
                nav_pre=nav_at_trade,
                raw_price=raw_price,
            )
            fund.record_holding_lifecycle(
                code,
                trade.trade_time,
                float(trade.from_weight),
                float(trade.to_weight),
            )
            fund.last_weights[code] = float(trade.to_weight)

            trade_results.append(
                _trade_row(trade, code, hfq, leg_return_pct, leg_return_pct, account_contrib_pct, nav_at_trade)
            )

        for code, hfq in batch_hfq.items():
            fund.last_marks[code] = hfq

        event_prices = _build_event_prices(fund, batch_hfq)
        nav_post = fund.nav(event_prices)
        if nav_post <= 0:
            nav_post = nav_pre

        period_return_pct = round((nav_post / prev_nav - 1.0) * 100, 2) if prev_nav > 0 else 0.0
        cum_return_pct = round((nav_post / INITIAL_CAPITAL - 1.0) * 100, 2)
        bench_return_pct = _bench_return_pct(bench_closes, bench_base, date_key)

        nav_records.append(
            {
                "trade_date": trade_date,
                "trade_time": trade_time,
                "nav": round(nav_post, 6),
                "cash": round(fund.cash, 6),
                "cum_return_pct": cum_return_pct,
                "period_return_pct": period_return_pct,
                "realized_return_pct": cum_return_pct,
                "unrealized_return_pct": 0.0,
                "benchmark_return_pct": bench_return_pct,
                "excess_return_pct": round(cum_return_pct - bench_return_pct, 2)
                if bench_return_pct is not None
                else None,
                "holding_count": sum(1 for w in fund.last_weights.values() if w > 0),
                "event_count": len(batch),
            }
        )
        prev_nav = nav_post

    last_rebalance_nav = nav_records[-1]["nav"] if nav_records else INITIAL_CAPITAL
    last_trade_date = fmt_trade_date(trades[-1].trade_time)
    holding_codes = {code for code, weight in fund.last_weights.items() if weight > 0}
    latest_quotes = load_latest_hfq_marks(holding_codes)
    latest_adj = load_latest_adj_factors(holding_codes)

    latest_mark_prices: dict[str, float] = {}
    latest_quote_dates: dict[str, str] = {}
    for code in holding_codes:
        if code in latest_quotes:
            quote_date, price = latest_quotes[code]
            latest_mark_prices[code] = price
            latest_quote_dates[code] = quote_date
        elif code in fund.last_marks:
            latest_mark_prices[code] = fund.last_marks[code]

    final_nav = fund.nav(latest_mark_prices) if latest_mark_prices else last_rebalance_nav
    if final_nav <= 0:
        final_nav = last_rebalance_nav

    latest_global_date = max(latest_quote_dates.values()) if latest_quote_dates else last_trade_date
    if latest_quotes and latest_global_date > last_trade_date and abs(final_nav - last_rebalance_nav) > 1e-9:
        cum_return_pct = round((final_nav / INITIAL_CAPITAL - 1.0) * 100, 2)
        period_return_pct = (
            round((final_nav / last_rebalance_nav - 1.0) * 100, 2) if last_rebalance_nav > 0 else 0.0
        )
        bench_return_pct = _bench_return_pct(bench_closes, bench_base, latest_global_date)
        nav_records.append(
            {
                "trade_date": f"{latest_global_date[:4]}-{latest_global_date[4:6]}-{latest_global_date[6:8]}",
                "trade_time": f"{latest_global_date[:4]}-{latest_global_date[4:6]}-{latest_global_date[6:8]} 15:00:00",
                "nav": round(final_nav, 6),
                "cash": round(fund.cash, 6),
                "cum_return_pct": cum_return_pct,
                "period_return_pct": period_return_pct,
                "realized_return_pct": cum_return_pct,
                "unrealized_return_pct": 0.0,
                "benchmark_return_pct": bench_return_pct,
                "excess_return_pct": round(cum_return_pct - bench_return_pct, 2)
                if bench_return_pct is not None
                else None,
                "holding_count": len(holding_codes),
                "event_count": 0,
                "is_latest_mark": True,
            }
        )

    positions: list[dict[str, Any]] = []
    for code, weight in fund.last_weights.items():
        if weight <= 0:
            continue
        mark = latest_mark_prices.get(code) or fund.last_marks.get(code)
        quote_date = latest_quote_dates.get(code, last_trade_date)
        _, vwap = fund.position_snapshot(code)
        raw_vwap = fund.raw_cost(code)
        return_pct = None
        if mark and vwap > 0:
            return_pct = round((mark / vwap - 1.0) * 100, 2)

        mark_raw: float | None = None
        if mark and mark > 0:
            mark_raw = _raw_mark_from_hfq(mark, vwap, raw_vwap, latest_adj.get(code))

        as_of = _as_of_time(quote_date, trades[-1].trade_time)
        duration = fund.holding_duration_fields(code, as_of)
        positions.append(
            {
                "ts_code": code,
                "stock_name": fund.stock_names.get(code, code),
                "last_action": fund.last_actions.get(code, ""),
                "current_weight": round(weight, 2),
                "avg_cost": round(raw_vwap, 4) if raw_vwap else None,
                "mark_price": mark_raw,
                "avg_cost_hfq": round(vwap, 4) if vwap > 0 else None,
                "mark_price_hfq": round(mark, 4) if mark else None,
                "return_pct": return_pct,
                "trade_count": fund.trade_counts.get(code, 0),
                "last_trade_time": stock_last_trade_time.get(code),
                "latest_quote_date": quote_date,
                **duration,
            }
        )
    positions.sort(key=lambda x: (-x["current_weight"], x["ts_code"]))

    realized_trades = [t for t in trade_results if t.get("leg_return_pct") is not None]
    wins = sum(1 for t in realized_trades if (t["leg_return_pct"] or 0) >= 0)
    leg_returns = [float(t["leg_return_pct"]) for t in realized_trades if t.get("leg_return_pct") is not None]
    sold_weights = [
        max(float(t["from_weight"]) - float(t["to_weight"]), 0.0)
        for t in realized_trades
        if t.get("leg_return_pct") is not None
    ]
    weight_sum = sum(sold_weights)
    weighted_avg_leg = (
        round(sum(r * w for r, w in zip(leg_returns, sold_weights)) / weight_sum, 2)
        if weight_sum > 0
        else 0.0
    )
    return_pcts = leg_returns
    contrib_pcts = [float(t["account_contrib_pct"]) for t in trade_results if t.get("account_contrib_pct") is not None]

    grouped_stats: list[dict[str, Any]] = []
    all_traded_codes = sorted(set(fund.stock_names) | set(fund.trade_counts))
    global_as_of = _as_of_time(latest_global_date, trades[-1].trade_time)
    for code in all_traded_codes:
        mark = latest_mark_prices.get(code) if code in holding_codes else None
        row = fund.grouped_row(
            code,
            fund.trade_counts.get(code, 0),
            global_as_of if code in holding_codes else stock_last_trade_time.get(code),
            mark=mark,
        )
        if row["realized_count"] > 0 or code in holding_codes:
            grouped_stats.append(row)
    grouped_stats.sort(key=lambda x: (-x["cum_return_pct"], -x["realized_count"]))

    realized_sum = round(fund.total_realized / INITIAL_CAPITAL * 100, 2)
    unrealized_sum = round(
        sum(fund.unrealized(code, latest_mark_prices.get(code, 0.0)) for code in holding_codes)
        / INITIAL_CAPITAL
        * 100,
        2,
    )
    cum_return_pct = round(realized_sum + unrealized_sum, 2)
    nav_return_pct = round((final_nav / INITIAL_CAPITAL - 1.0) * 100, 2)
    last_curve = nav_records[-1] if nav_records else {}
    nav_records = _enrich_equity_stages(nav_records)

    return {
        "overview": {
            "trade_count": len(trades),
            "stock_count": len(ts_codes),
            "realized_events": len(realized_trades),
            "win_rate": round(wins / len(realized_trades) * 100, 2) if realized_trades else 0.0,
            "avg_trade_return_pct": weighted_avg_leg,
            "cum_return_pct": cum_return_pct,
            "realized_return_pct": realized_sum,
            "unrealized_return_pct": unrealized_sum,
            "benchmark_return_pct": last_curve.get("benchmark_return_pct"),
            "excess_return_pct": last_curve.get("excess_return_pct"),
            "latest_trade_time": trades[-1].trade_time,
            "holding_count": len(positions),
            "rebalance_event_count": len(events),
            "buy_count": sum(1 for t in trades if t.to_weight > t.from_weight),
            "sell_count": sum(1 for t in trades if t.to_weight < t.from_weight),
            "max_trade_return_pct": round(max(return_pcts), 2) if return_pcts else None,
            "min_trade_return_pct": round(min(return_pcts), 2) if return_pcts else None,
            "best_stock_name": grouped_stats[0]["stock_name"] if grouped_stats else None,
            "best_stock_return_pct": grouped_stats[0]["cum_return_pct"] if grouped_stats else None,
            "worst_stock_name": grouped_stats[-1]["stock_name"] if grouped_stats else None,
            "worst_stock_return_pct": grouped_stats[-1]["cum_return_pct"] if grouped_stats else None,
            "total_realized_contrib_pct": round(sum(contrib_pcts), 4),
            "final_nav": round(final_nav, 6),
            "nav_return_pct": nav_return_pct,
            "engine_version": ENGINE_VERSION,
        },
        "positions": positions,
        "recent_trades": list(reversed(trade_results)),
        "grouped_stats": grouped_stats,
        "equity_curve": nav_records,
        "daily_nav": nav_records,
    }


def _trade_row(
    trade: TradeInput,
    code: str,
    hfq: float | None,
    return_pct: float | None,
    leg_return_pct: float | None,
    account_contrib_pct: float | None,
    nav_pre: float,
) -> dict[str, Any]:
    return {
        "id": trade.id,
        "trade_time": trade.trade_time,
        "stock_name": trade.stock_name,
        "ts_code": code,
        "action": trade.action,
        "from_weight": trade.from_weight,
        "to_weight": trade.to_weight,
        "weight_delta": trade.weight_delta,
        "price": trade.price,
        "price_hfq": hfq,
        "return_pct": return_pct,
        "leg_return_pct": leg_return_pct,
        "account_contrib_pct": account_contrib_pct,
        "nav_pre": round(nav_pre, 6),
    }


def _empty_result() -> dict[str, Any]:
    empty_overview = {
        "trade_count": 0,
        "stock_count": 0,
        "realized_events": 0,
        "win_rate": 0.0,
        "avg_trade_return_pct": 0.0,
        "cum_return_pct": 0.0,
        "realized_return_pct": 0.0,
        "unrealized_return_pct": 0.0,
        "latest_trade_time": None,
        "holding_count": 0,
        "rebalance_event_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "max_trade_return_pct": None,
        "min_trade_return_pct": None,
        "best_stock_name": None,
        "best_stock_return_pct": None,
        "worst_stock_name": None,
        "worst_stock_return_pct": None,
        "total_realized_contrib_pct": 0.0,
        "final_nav": 1.0,
        "engine_version": ENGINE_VERSION,
    }
    return {
        "overview": empty_overview,
        "positions": [],
        "recent_trades": [],
        "grouped_stats": [],
        "equity_curve": [],
        "daily_nav": [],
    }


def _collect_latest_weights(rows: Any) -> dict[tuple[int, str], float]:
    latest_weight: dict[tuple[int, str], float] = {}
    for row in rows:
        account_id = int(row.account_id)
        code = to_xueqiu_code(str(row.ts_code))
        latest_weight[(account_id, code)] = float(row.to_weight)
    return latest_weight


def fetch_active_holding_codes() -> set[str]:
    """各账户最新 to_weight > 0 的标的（用于可选的行情同步）。"""
    with get_conn() as conn:
        rows = conn.execute(
            select(
                rebalance_trades_table.c.account_id,
                rebalance_trades_table.c.ts_code,
                rebalance_trades_table.c.to_weight,
            ).order_by(
                rebalance_trades_table.c.trade_time.asc(),
                rebalance_trades_table.c.id.asc(),
            )
        ).fetchall()

    latest_weight = _collect_latest_weights(rows)
    return {code for (_, code), weight in latest_weight.items() if weight > 0}


def fetch_holding_codes_for_account(account_id: int) -> set[str]:
    """单账户当前持仓标的（最新 to_weight > 0）。"""
    with get_conn() as conn:
        rows = conn.execute(
            select(
                rebalance_trades_table.c.account_id,
                rebalance_trades_table.c.ts_code,
                rebalance_trades_table.c.to_weight,
            )
            .where(rebalance_trades_table.c.account_id == account_id)
            .order_by(
                rebalance_trades_table.c.trade_time.asc(),
                rebalance_trades_table.c.id.asc(),
            )
        ).fetchall()

    latest_weight = _collect_latest_weights(rows)
    return {code for (aid, code), weight in latest_weight.items() if aid == account_id and weight > 0}
