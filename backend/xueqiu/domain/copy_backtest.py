"""
抄作业回测引擎：分组合计账 + 严格跟单减仓 + 共识加仓。

价格口径：
- 买卖成交、现金、市值：未复权价（含 1% 滑点）
- 累计收益率：持仓按后复权价盯市
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_RUN_CFG: "BacktestConfig | None" = None

from sqlalchemy import select

from xueqiu.api.services import _fetch_trades, _resolve_account, list_xueqiu_portfolio_codes
from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.storage.db import accounts_table, get_conn
from xueqiu.domain.nav_engine import (
    TradeInput,
    fmt_trade_date,
    load_adj_map,
    load_latest_hfq_marks,
    resolve_price_hfq,
)

INITIAL_CAPITAL = 100_000.0
MAX_STOCK_PCT = 0.20
MIN_NEW_POSITION_PCT = 1.0
MAX_POSITIONS = 5
STAR_UNLOCK_PROFIT = 500_000.0
MAIN_LOT_SIZE = 100
STAR_LOT_SIZE = 200
SLIPPAGE_BUY = 1.01
SLIPPAGE_SELL = 0.99


TARGET_INVESTED_PCT = 0.98
REBALANCE_PORTFOLIO = "_配平"


@dataclass
class BacktestConfig:
    initial_capital: float = INITIAL_CAPITAL
    max_stock_pct: float = MAX_STOCK_PCT
    min_new_position_pct: float = MIN_NEW_POSITION_PCT
    max_positions: int = MAX_POSITIONS
    target_invested_pct: float = TARGET_INVESTED_PCT


def _cfg() -> BacktestConfig:
    return _RUN_CFG or BacktestConfig()


def resolve_trade_price_raw(trade: TradeInput, adj_map: dict[tuple[str, str], float]) -> float | None:
    if trade.price is not None and trade.price > 0:
        return float(trade.price)
    hfq = resolve_price_hfq(trade, adj_map)
    if not hfq or hfq <= 0:
        return None
    code = to_xueqiu_code(trade.ts_code)
    trade_date = fmt_trade_date(trade.trade_time)
    adj = adj_map.get((code, trade_date), 1.0)
    if adj <= 0:
        adj = 1.0
    return float(hfq) / adj


def _slippage_price(raw_px: float, is_buy: bool) -> float:
    return raw_px * SLIPPAGE_BUY if is_buy else raw_px * SLIPPAGE_SELL


def _is_star_market(code: str) -> bool:
    return to_xueqiu_code(code).upper().startswith("SH688")


def _lot_size(code: str) -> int:
    return STAR_LOT_SIZE if _is_star_market(code) else MAIN_LOT_SIZE


def _floor_lots(code: str, qty: float) -> float:
    if qty <= 1e-12:
        return 0.0
    lot = _lot_size(code)
    return float(int(qty // lot) * lot)


def _weight_pct(qty: float, price: float, nav_pre: float) -> float:
    if nav_pre <= 0 or qty <= 1e-12:
        return 0.0
    return qty * price / nav_pre * 100.0


def _is_buy_or_increase(from_weight: float, to_weight: float) -> bool:
    return to_weight > from_weight + 1e-9


@dataclass
class Slice:
    qty: float = 0.0
    vwap: float = 0.0


@dataclass
class Holding:
    qty: float = 0.0
    vwap: float = 0.0


@dataclass
class _StockStats:
    sell_legs: list[dict[str, float]] = field(default_factory=list)


@dataclass
class SignalResult:
    action: str
    qty_delta: float = 0.0
    our_weight_pct: float = 0.0
    trigger: str = ""
    leg_return_pct: float | None = None
    slice_qty_before: float = 0.0
    slice_qty_after: float = 0.0
    physical_qty: float = 0.0
    skipped: bool = False
    note: str = ""


class SliceLedger:
    """分组合计账：物理一股一仓，买卖仅动对应 slice。"""

    def __init__(self, initial_cash: float) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.slices: dict[tuple[str, str], Slice] = {}
        self.holdings: dict[str, Holding] = {}
        self.stats: dict[str, _StockStats] = defaultdict(_StockStats)
        self.stock_names: dict[str, str] = {}
        self.last_raw_marks: dict[str, float] = {}
        self.last_hfq_marks: dict[str, float] = {}
        self.trade_counts: dict[str, int] = defaultdict(int)
        self.stock_last_trade_time: dict[str, str] = {}
        self.holding_opened_at: dict[str, str] = {}

    def _slice(self, portfolio: str, code: str) -> Slice:
        key = (portfolio, code)
        if key not in self.slices:
            self.slices[key] = Slice()
        return self.slices[key]

    def physical_qty(self, code: str) -> float:
        return sum(s.qty for (p, c), s in self.slices.items() if c == code and s.qty > 1e-12)

    def position_count(self) -> int:
        return sum(1 for c, h in self.holdings.items() if h.qty > 1e-12)

    def _sync_physical(self, code: str) -> None:
        total_qty = 0.0
        total_cost = 0.0
        for (p, c), sl in self.slices.items():
            if c != code or sl.qty <= 1e-12:
                continue
            total_qty += sl.qty
            total_cost += sl.qty * sl.vwap
        if total_qty <= 1e-12:
            self.holdings.pop(code, None)
            return
        h = self.holdings.setdefault(code, Holding())
        h.qty = total_qty
        h.vwap = total_cost / total_qty if total_qty > 0 else 0.0

    def nav(self, prices: dict[str, float]) -> float:
        position_value = 0.0
        for code, holding in self.holdings.items():
            if holding.qty <= 1e-12:
                continue
            price = prices.get(code) or self.last_raw_marks.get(code)
            if price and price > 0:
                position_value += holding.qty * price
        total = self.cash + position_value
        return total if total > 0 else self.initial_cash

    def nav_hfq(self, hfq_prices: dict[str, float]) -> float:
        position_value = 0.0
        for code, holding in self.holdings.items():
            if holding.qty <= 1e-12:
                continue
            hfq = hfq_prices.get(code) or self.last_hfq_marks.get(code)
            if hfq and hfq > 0:
                raw = self.last_raw_marks.get(code)
                last_hfq = self.last_hfq_marks.get(code)
                if raw and last_hfq and last_hfq > 0:
                    position_value += holding.qty * hfq * (raw / last_hfq)
                else:
                    position_value += holding.qty * hfq
        total = self.cash + position_value
        return total if total > 0 else self.initial_cash

    def _infer_action_label(self, from_weight: float, to_weight: float) -> str:
        if from_weight <= 1e-9 and to_weight > 1e-9:
            return "买入"
        if from_weight > 1e-9 and to_weight <= 1e-9:
            return "卖出"
        if to_weight > from_weight + 1e-9:
            return "加仓"
        if to_weight + 1e-9 < from_weight:
            return "减仓"
        return "持平"

    def _cap_qty(self, code: str, price: float, nav_pre: float) -> float:
        if price <= 0 or nav_pre <= 0:
            return 0.0
        return _cfg().max_stock_pct * nav_pre / price

    def _record_sell_leg(self, code: str, sell_qty: float, sell_price: float, vwap: float, nav_pre: float) -> float | None:
        if sell_qty <= 1e-12 or vwap <= 0:
            return None
        leg_return_pct = round((sell_price / vwap - 1.0) * 100, 2)
        weight_sold = sell_qty * sell_price / nav_pre * 100.0 if nav_pre > 0 else 0.0
        if weight_sold > 1e-9:
            self.stats[code].sell_legs.append({"weight_sold": weight_sold, "leg_return_pct": leg_return_pct})
        return leg_return_pct

    def _sell_from_slice(
        self,
        portfolio: str,
        code: str,
        sell_qty: float,
        sell_price: float,
        nav_pre: float,
    ) -> float:
        if sell_qty <= 1e-12:
            return 0.0
        sl = self._slice(portfolio, code)
        if sl.qty <= 1e-12:
            return 0.0
        sell_qty = min(sell_qty, sl.qty)
        leg = self._record_sell_leg(code, sell_qty, sell_price, sl.vwap, nav_pre)
        self.cash += sell_qty * sell_price
        sl.qty -= sell_qty
        if sl.qty <= 1e-12:
            sl.qty = 0.0
            sl.vwap = 0.0
        self._sync_physical(code)
        return sell_qty

    def _buy_to_slice(
        self,
        portfolio: str,
        code: str,
        buy_qty: float,
        buy_price: float,
    ) -> float:
        if buy_qty <= 1e-12:
            return 0.0
        lot = _lot_size(code)
        if buy_qty < lot:
            return 0.0
        max_affordable = _floor_lots(code, self.cash / buy_price if buy_price > 0 else 0.0)
        buy_qty = min(buy_qty, max_affordable)
        buy_qty = _floor_lots(code, buy_qty)
        if buy_qty < lot:
            return 0.0
        sl = self._slice(portfolio, code)
        if sl.qty <= 1e-12:
            sl.vwap = buy_price
        else:
            sl.vwap = (sl.qty * sl.vwap + buy_qty * buy_price) / (sl.qty + buy_qty)
        sl.qty += buy_qty
        self.cash -= buy_qty * buy_price
        if code not in self.holding_opened_at:
            self.holding_opened_at[code] = ""
        self._sync_physical(code)
        return buy_qty

    def liquidate_all_slices(
        self,
        code: str,
        sell_price: float,
        nav_pre: float,
    ) -> float:
        """换仓：清空该股全部 slice。"""
        sold_total = 0.0
        for (p, c), sl in list(self.slices.items()):
            if c != code or sl.qty <= 1e-12:
                continue
            sold_total += self._sell_from_slice(p, code, sl.qty, sell_price, nav_pre)
        return sold_total

    def apply_reduce_signal(
        self,
        portfolio_code: str,
        code: str,
        stock_name: str,
        master_from: float,
        master_to: float,
        raw_px: float,
        nav_pre: float,
    ) -> SignalResult | None:
        self.stock_names[code] = stock_name
        master_from = float(master_from)
        master_to = float(master_to)
        if master_to + 1e-9 >= master_from:
            return None
        action = self._infer_action_label(master_from, master_to)
        sl = self._slice(portfolio_code, code)
        slice_before = sl.qty
        phys_before = self.physical_qty(code)
        sell_price = _slippage_price(raw_px, False)

        if sl.qty <= 1e-12:
            return SignalResult(
                action=f"{action}(无slice)",
                our_weight_pct=round(_weight_pct(phys_before, raw_px, nav_pre), 2),
                trigger="跟单减仓",
                slice_qty_before=slice_before,
                slice_qty_after=slice_before,
                physical_qty=phys_before,
                skipped=True,
                note="该组合无计账份额",
            )
        vwap_before = sl.vwap
        if master_to <= 1e-9:
            target_slice_qty = 0.0
        else:
            target_slice_qty = sl.qty * (master_to / master_from)
        sell_qty = sl.qty - target_slice_qty
        if master_to <= 1e-9:
            sell_qty = sl.qty
        else:
            sell_qty = _floor_lots(code, sell_qty)
            remaining = sl.qty - sell_qty
            if 0 < remaining < _lot_size(code):
                sell_qty = sl.qty
        sold = self._sell_from_slice(portfolio_code, code, sell_qty, sell_price, nav_pre)
        phys_after = self.physical_qty(code)
        leg = round((sell_price / vwap_before - 1.0) * 100, 2) if sold > 0 and vwap_before > 0 else None
        return SignalResult(
            action=action if sold > 0 else f"{action}(低于最小手数)",
            qty_delta=-sold,
            our_weight_pct=round(_weight_pct(phys_after, raw_px, nav_pre), 2),
            trigger="跟单减仓" if master_to > 1e-9 else "跟单清仓",
            leg_return_pct=leg,
            slice_qty_before=slice_before,
            slice_qty_after=self._slice(portfolio_code, code).qty,
            physical_qty=phys_after,
            skipped=sold <= 0,
        )

    def apply_increase_existing_slice(
        self,
        portfolio_code: str,
        code: str,
        stock_name: str,
        master_from: float,
        master_to: float,
        raw_px: float,
        nav_pre: float,
    ) -> SignalResult | None:
        """阶段A：仅对已有 slice 的组合加仓（相对缩放）。"""
        self.stock_names[code] = stock_name
        master_from = float(master_from)
        master_to = float(master_to)
        if master_to <= master_from + 1e-9:
            return None
        sl = self._slice(portfolio_code, code)
        if sl.qty <= 1e-12:
            return None

        action = self._infer_action_label(master_from, master_to)
        slice_before = sl.qty
        phys_qty = self.physical_qty(code)
        buy_price = _slippage_price(raw_px, True)
        cap_qty = self._cap_qty(code, buy_price, nav_pre)

        if master_from > 1e-9:
            target_slice_qty = sl.qty * (master_to / master_from)
        else:
            target_slice_qty = sl.qty
        max_slice = max(0.0, cap_qty - (phys_qty - sl.qty))
        target_slice_qty = min(target_slice_qty, max_slice)
        buy_qty = _floor_lots(code, target_slice_qty - sl.qty)
        if buy_qty < _lot_size(code):
            return SignalResult(
                action=f"{action}(低于最小手数)",
                our_weight_pct=round(_weight_pct(phys_qty, raw_px, nav_pre), 2),
                trigger="组合加仓",
                slice_qty_before=slice_before,
                slice_qty_after=slice_before,
                physical_qty=phys_qty,
                skipped=True,
            )

        bought = self._buy_to_slice(portfolio_code, code, buy_qty, buy_price)
        phys_after = self.physical_qty(code)
        return SignalResult(
            action=action if bought > 0 else f"{action}(低于最小手数)",
            qty_delta=bought,
            our_weight_pct=round(_weight_pct(phys_after, raw_px, nav_pre), 2),
            trigger="组合加仓",
            slice_qty_before=slice_before,
            slice_qty_after=self._slice(portfolio_code, code).qty,
            physical_qty=phys_after,
            skipped=bought <= 0,
        )

    def trim_to_cap(self, code: str, price: float, nav_pre: float) -> float:
        holding = self.holdings.get(code)
        if not holding or holding.qty <= 1e-12 or nav_pre <= 0:
            return 0.0
        cap_qty = self._cap_qty(code, price, nav_pre)
        if holding.qty <= cap_qty + 1e-6:
            return 0.0
        sell_total = holding.qty - _floor_lots(code, cap_qty)
        if sell_total <= 1e-12:
            return 0.0
        remaining = holding.qty - sell_total
        if 0 < remaining < _lot_size(code):
            sell_total = holding.qty
        sold = 0.0
        sell_price = _slippage_price(price, False)
        for (p, c), sl in list(self.slices.items()):
            if c != code or sl.qty <= 1e-12:
                continue
            portion = sell_total * (sl.qty / holding.qty)
            portion = min(portion, sl.qty)
            portion = _floor_lots(code, portion)
            if portion <= 0 and sell_total >= holding.qty - 1e-6:
                portion = sl.qty
            sold += self._sell_from_slice(p, code, portion, sell_price, nav_pre)
        return -sold

    def stock_cum_return_pct(self, code: str, mark_hfq: float | None) -> float:
        st = self.stats[code]
        legs = st.sell_legs
        if not legs:
            holding = self.holdings.get(code)
            if holding and holding.qty > 1e-12 and mark_hfq and holding.vwap > 0:
                hfq = self.last_hfq_marks.get(code)
                raw = self.last_raw_marks.get(code)
                if hfq and raw and raw > 0:
                    mark = mark_hfq
                    cost_hfq = holding.vwap * (hfq / raw) if raw > 0 else holding.vwap
                    return round((mark / cost_hfq - 1.0) * 100, 2) if cost_hfq > 0 else 0.0
            return 0.0
        total_w = sum(leg["weight_sold"] for leg in legs)
        if total_w <= 0:
            return 0.0
        weighted = sum(leg["weight_sold"] * leg["leg_return_pct"] for leg in legs) / total_w
        holding = self.holdings.get(code)
        if holding and holding.qty > 1e-12 and mark_hfq:
            hfq = self.last_hfq_marks.get(code)
            raw = self.last_raw_marks.get(code)
            if hfq and raw and raw > 0 and holding.vwap > 0:
                cost_hfq = holding.vwap * (hfq / raw)
                unreal = (mark_hfq / cost_hfq - 1.0) * 100 if cost_hfq > 0 else 0.0
                last_weight = holding.qty * raw / self.nav(self.last_raw_marks) * 100 if self.nav(self.last_raw_marks) > 0 else 0.0
                if last_weight > 0:
                    return round((weighted * total_w + unreal * last_weight) / (total_w + last_weight), 2)
        return round(weighted, 2)

    def grouped_row(self, code: str, mark_hfq: float | None) -> dict[str, Any]:
        st = self.stats[code]
        rc = len(st.sell_legs)
        wins = sum(1 for leg in st.sell_legs if leg["leg_return_pct"] >= 0)
        holding = self.holdings.get(code)
        is_holding = holding is not None and holding.qty > 1e-12
        cum = self.stock_cum_return_pct(code, mark_hfq)
        return {
            "ts_code": code,
            "stock_name": self.stock_names.get(code, code),
            "events": self.trade_counts.get(code, 0),
            "realized_count": rc,
            "wins": wins,
            "losses": rc - wins,
            "win_rate": round(wins / rc * 100, 2) if rc else 0.0,
            "cum_return_pct": cum,
            "avg_return_pct": cum,
            "last_trade_time": self.stock_last_trade_time.get(code),
            "is_holding": is_holding,
            "holding_days": None,
            "holding_opened_at": self.holding_opened_at.get(code),
        }


def _build_raw_prices(fund: SliceLedger, batch_raw: dict[str, float]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for code, holding in fund.holdings.items():
        if holding.qty <= 1e-12:
            continue
        if code in batch_raw:
            prices[code] = batch_raw[code]
        elif code in fund.last_raw_marks:
            prices[code] = fund.last_raw_marks[code]
    for code, raw in batch_raw.items():
        prices[code] = raw
    return prices


def _build_hfq_prices(fund: SliceLedger, batch_hfq: dict[str, float]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for code, holding in fund.holdings.items():
        if holding.qty <= 1e-12:
            continue
        if code in batch_hfq:
            prices[code] = batch_hfq[code]
        elif code in fund.last_hfq_marks:
            prices[code] = fund.last_hfq_marks[code]
    for code, hfq in batch_hfq.items():
        prices[code] = hfq
    return prices


def enforce_stock_cap(fund: SliceLedger, prices: dict[str, float], max_pct: float, trade_time: str = "") -> list[dict]:
    nav_pre = fund.nav(prices)
    if nav_pre <= 0:
        return []
    logs: list[dict] = []
    for code, holding in list(fund.holdings.items()):
        if holding.qty <= 1e-12:
            continue
        price = prices.get(code) or fund.last_raw_marks.get(code)
        if not price or price <= 0:
            continue
        qty_delta = fund.trim_to_cap(code, price, nav_pre)
        if qty_delta < -1e-12:
            logs.append(
                {
                    "trade_time": trade_time,
                    "source_portfolio": "系统",
                    "source_name": "20%封顶",
                    "stock_name": fund.stock_names.get(code, code),
                    "ts_code": code,
                    "master_from": None,
                    "master_to": f"≤{max_pct * 100:.0f}%",
                    "action": "封顶减仓",
                    "price": round(_slippage_price(price, False), 4),
                    "price_hfq": round(fund.last_hfq_marks.get(code, 0.0), 4) or None,
                    "qty_delta": round(qty_delta, 4),
                    "our_weight_pct": round(max_pct * 100, 2),
                    "nav_after": round(fund.nav(prices), 2),
                    "note": "单票超上限自动裁剪",
                    "trigger": "封顶减仓",
                    "leg_return_pct": None,
                    "slice_qty_before": None,
                    "slice_qty_after": None,
                    "physical_qty": fund.physical_qty(code),
                }
            )
            nav_pre = fund.nav(prices)
    return logs


def _update_mirror(
    mirror: dict[tuple[str, str], float],
    batch: list[tuple[str, str, TradeInput]],
) -> None:
    for acct_code, _, trade in batch:
        if trade.to_weight <= 1e-9:
            mirror.pop((acct_code, trade.ts_code), None)
        else:
            mirror[(acct_code, trade.ts_code)] = float(trade.to_weight)


def _compute_follow_scores(
    mirror: dict[tuple[str, str], float],
    batch_increase_codes: set[str],
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for (_p, code), weight in mirror.items():
        if weight > 1e-9:
            scores[code] += 1.0
    for code in batch_increase_codes:
        scores[code] += 1.0
    return dict(scores)


def _active_pool(scores: dict[str, float], k: int) -> list[str]:
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [code for code, _ in ranked[:k]]


def _primary_portfolio_for(
    code: str,
    batch: list[tuple[str, str, TradeInput]],
    mirror: dict[tuple[str, str], float],
) -> str:
    for acct_code, _, trade in batch:
        if trade.ts_code == code and trade.to_weight > trade.from_weight + 1e-9:
            return acct_code
    best_p = REBALANCE_PORTFOLIO
    best_w = -1.0
    for (p, c), w in mirror.items():
        if c == code and w > best_w:
            best_p, best_w = p, w
    return best_p


def _held_codes(fund: SliceLedger) -> list[str]:
    return [c for c, h in fund.holdings.items() if h.qty > 1e-12]


def _rotate_for_pool(
    fund: SliceLedger,
    pool: list[str],
    scores: dict[str, float],
    prices: dict[str, float],
    trade_time: str,
    star_unlocked: bool,
) -> list[dict]:
    """方案A：为新进入活跃池的股票腾出名额，卖掉 follow_score 最低的持仓。"""
    cfg = _cfg()
    logs: list[dict] = []
    pool_set = set(pool)
    held = _held_codes(fund)

    for code in pool:
        if code in held:
            continue
        while len(_held_codes(fund)) >= cfg.max_positions:
            held_now = _held_codes(fund)
            if not held_now:
                break
            # 优先踢出不在活跃池的；否则踢 score 最低
            outside = [c for c in held_now if c not in pool_set]
            victim = min(outside or held_now, key=lambda c: (scores.get(c, 0.0), c))
            raw_px = prices.get(victim) or fund.last_raw_marks.get(victim)
            if not raw_px or raw_px <= 0:
                held.remove(victim) if victim in held else None
                break
            nav_pre = fund.nav(prices)
            sold = fund.liquidate_all_slices(victim, _slippage_price(raw_px, False), nav_pre)
            if sold <= 1e-12:
                break
            logs.append(
                {
                    "trade_time": trade_time,
                    "source_portfolio": "系统",
                    "source_name": "换仓",
                    "stock_name": fund.stock_names.get(victim, victim),
                    "ts_code": victim,
                    "master_from": None,
                    "master_to": 0,
                    "action": "换仓卖出",
                    "price": round(_slippage_price(raw_px, False), 4),
                    "price_hfq": round(fund.last_hfq_marks.get(victim, 0.0), 4) or None,
                    "qty_delta": round(-sold, 4),
                    "our_weight_pct": 0.0,
                    "nav_after": round(fund.nav(prices), 2),
                    "note": f"为活跃池腾出名额 score={scores.get(victim, 0):.1f}",
                    "trigger": "换仓",
                    "leg_return_pct": None,
                    "slice_qty_before": None,
                    "slice_qty_after": 0.0,
                    "physical_qty": 0.0,
                }
            )
        if code in _held_codes(fund):
            continue
        if _is_star_market(code) and not star_unlocked:
            continue
        break

    return logs


def _rebalance_deploy(
    fund: SliceLedger,
    pool: list[str],
    scores: dict[str, float],
    prices: dict[str, float],
    batch: list[tuple[str, str, TradeInput]],
    mirror: dict[tuple[str, str], float],
    trade_time: str,
    star_unlocked: bool,
) -> list[dict]:
    """阶段B：将资金配平到目标仓位。"""
    cfg = _cfg()
    logs: list[dict] = []
    if not pool:
        return logs

    nav = fund.nav(prices)
    if nav <= 0:
        return logs

    target_invested = cfg.target_invested_pct * nav
    per_target_value = min(cfg.max_stock_pct * nav, target_invested / len(pool))

    for code in sorted(pool, key=lambda c: (-scores.get(c, 0.0), c)):
        if _is_star_market(code) and not star_unlocked:
            continue
        raw_px = prices.get(code) or fund.last_raw_marks.get(code)
        if not raw_px or raw_px <= 0:
            continue
        buy_price = _slippage_price(raw_px, True)
        phys_qty = fund.physical_qty(code)
        current_value = phys_qty * raw_px
        gap_value = per_target_value - current_value
        if gap_value <= buy_price * _lot_size(code) * 0.5:
            continue

        buy_qty = _floor_lots(code, gap_value / buy_price)
        cap_qty = fund._cap_qty(code, buy_price, fund.nav(prices))
        max_add = max(0.0, cap_qty - phys_qty)
        buy_qty = min(buy_qty, _floor_lots(code, max_add))
        if buy_qty < _lot_size(code):
            continue

        if phys_qty <= 1e-12 and _weight_pct(buy_qty, buy_price, nav) < cfg.min_new_position_pct:
            continue

        portfolio = _primary_portfolio_for(code, batch, mirror)
        if portfolio == REBALANCE_PORTFOLIO:
            portfolio = next(
                (acct for acct, _, t in batch if t.ts_code == code),
                REBALANCE_PORTFOLIO,
            )

        slice_before = fund._slice(portfolio, code).qty
        bought = fund._buy_to_slice(portfolio, code, buy_qty, buy_price)
        if bought <= 0:
            continue

        if code not in fund.holding_opened_at or not fund.holding_opened_at.get(code):
            fund.holding_opened_at[code] = trade_time

        phys_after = fund.physical_qty(code)
        trigger = "配平首仓" if slice_before <= 1e-12 else "配平加仓"
        logs.append(
            {
                "trade_time": trade_time,
                "source_portfolio": portfolio if portfolio != REBALANCE_PORTFOLIO else "系统",
                "source_name": "资金配平",
                "stock_name": fund.stock_names.get(code, code),
                "ts_code": code,
                "master_from": None,
                "master_to": None,
                "action": "配平买入",
                "price": round(buy_price, 4),
                "price_hfq": round(fund.last_hfq_marks.get(code, 0.0), 4) or None,
                "qty_delta": round(bought, 4),
                "our_weight_pct": round(_weight_pct(phys_after, raw_px, nav), 2),
                "nav_after": round(fund.nav(prices), 2),
                "note": f"目标≈{per_target_value / nav * 100:.1f}%/只",
                "trigger": trigger,
                "leg_return_pct": None,
                "slice_qty_before": slice_before,
                "slice_qty_after": fund._slice(portfolio, code).qty,
                "physical_qty": phys_after,
            }
        )
        nav = fund.nav(prices)

    return logs


def _result_to_log(
    trade_time: str,
    acct_code: str,
    acct_name: str,
    trade: TradeInput,
    result: SignalResult,
    raw_px: float,
    hfq_px: float | None,
    nav_after: float,
) -> dict:
    exec_price = _slippage_price(raw_px, result.qty_delta > 0) if abs(result.qty_delta) > 1e-12 else raw_px
    return {
        "trade_time": trade_time,
        "source_portfolio": acct_code,
        "source_name": acct_name,
        "stock_name": trade.stock_name,
        "ts_code": trade.ts_code,
        "master_from": trade.from_weight,
        "master_to": trade.to_weight,
        "action": result.action,
        "price": round(exec_price, 4),
        "price_hfq": round(hfq_px, 4) if hfq_px else None,
        "qty_delta": round(result.qty_delta, 4),
        "our_weight_pct": result.our_weight_pct,
        "nav_after": round(nav_after, 2),
        "note": result.note,
        "trigger": result.trigger,
        "leg_return_pct": result.leg_return_pct,
        "slice_qty_before": result.slice_qty_before,
        "slice_qty_after": result.slice_qty_after,
        "physical_qty": result.physical_qty,
    }


def load_portfolio_trades() -> tuple[dict[str, str], list[tuple[str, str, TradeInput]]]:
    codes = list_xueqiu_portfolio_codes()
    if not codes:
        return {}, []

    account_names: dict[str, str] = {}
    with get_conn() as conn:
        for row in conn.execute(
            select(accounts_table.c.account_code, accounts_table.c.account_name).where(
                accounts_table.c.account_code.in_(sorted(codes))
            )
        ).fetchall():
            account_names[str(row.account_code).upper()] = str(row.account_name)

    all_trades: list[tuple[str, str, TradeInput]] = []
    for code in codes:
        account_id, name = _resolve_account(code)
        acct_name = account_names.get(code.upper(), name)
        for r in _fetch_trades(account_id):
            all_trades.append(
                (
                    code,
                    acct_name,
                    TradeInput(
                        id=r.id,
                        trade_time=r.trade_time,
                        stock_name=r.stock_name,
                        ts_code=to_xueqiu_code(r.ts_code),
                        action=r.action,
                        from_weight=float(r.from_weight),
                        to_weight=float(r.to_weight),
                        weight_delta=float(r.weight_delta),
                        price=r.price,
                        price_hfq=r.price_hfq,
                    ),
                )
            )
    all_trades.sort(key=lambda x: (x[2].trade_time, x[2].id))
    return account_names, all_trades


def run_backtest(config: BacktestConfig | None = None) -> dict:
    global _RUN_CFG
    _RUN_CFG = config or BacktestConfig()
    cfg = _cfg()

    account_names, all_trades = load_portfolio_trades()
    if not all_trades:
        raise ValueError("数据库中没有 ZH 组合或没有调仓记录。")

    fund = SliceLedger(initial_cash=cfg.initial_capital)
    ts_codes = {t.ts_code for _, _, t in all_trades}
    trade_dates = {fmt_trade_date(t.trade_time) for _, _, t in all_trades}
    adj_map = load_adj_map(ts_codes, trade_dates)

    trade_logs: list[dict] = []
    equity_curve: list[dict] = []
    blocked_688 = 0
    cap_triggers = 0
    rotate_triggers = 0
    rebalance_triggers = 0
    skipped_lot = 0
    skipped_small = 0

    mirror: dict[tuple[str, str], float] = {}

    # 按调仓时间点分批
    batches: list[tuple[str, list[tuple[str, str, TradeInput]]]] = []
    i = 0
    while i < len(all_trades):
        t0 = all_trades[i][2].trade_time
        batch: list[tuple[str, str, TradeInput]] = []
        while i < len(all_trades) and all_trades[i][2].trade_time == t0:
            batch.append(all_trades[i])
            i += 1
        batches.append((t0, batch))

    for trade_time, batch in batches:
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

        raw_prices = _build_raw_prices(fund, batch_prices)
        nav_before = fund.nav(raw_prices)
        star_unlocked = nav_before - cfg.initial_capital >= STAR_UNLOCK_PROFIT - 1e-6

        # 阶段A1：先处理所有减仓/清仓
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
                fund.trade_counts[trade.ts_code] += 1
                fund.stock_last_trade_time[trade.ts_code] = trade_time
                if result:
                    if "低于最小手数" in result.action:
                        skipped_lot += 1
                    nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                    trade_logs.append(_result_to_log(trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after))

        # 更新镜像持仓（组合最新 weight）
        _update_mirror(mirror, batch)

        batch_increase_codes = {
            t.ts_code
            for _, _, t in batch
            if t.to_weight > t.from_weight + 1e-9
        }

        # 阶段A2：已有 slice 的组合加仓
        for acct_code, acct_name, trade in batch:
            raw_px = batch_prices.get(trade.ts_code)
            hfq_px = batch_hfq.get(trade.ts_code)
            if not raw_px:
                continue
            if not (trade.to_weight > trade.from_weight + 1e-9):
                continue
            if (
                _is_buy_or_increase(trade.from_weight, trade.to_weight)
                and _is_star_market(trade.ts_code)
                and not star_unlocked
            ):
                blocked_688 += 1
                trade_logs.append(
                    {
                        "trade_time": trade_time,
                        "source_portfolio": acct_code,
                        "source_name": acct_name,
                        "stock_name": trade.stock_name,
                        "ts_code": trade.ts_code,
                        "master_from": trade.from_weight,
                        "master_to": trade.to_weight,
                        "action": "688拦截",
                        "price": round(raw_px, 4),
                        "price_hfq": round(hfq_px, 4) if hfq_px else None,
                        "qty_delta": 0.0,
                        "our_weight_pct": round(
                            fund.physical_qty(trade.ts_code) * raw_px / nav_before * 100, 2
                        )
                        if nav_before > 0
                        else 0.0,
                        "nav_after": round(nav_before, 2),
                        "note": "累计盈利未达50万",
                        "trigger": "688拦截",
                        "leg_return_pct": None,
                        "slice_qty_before": None,
                        "slice_qty_after": None,
                        "physical_qty": fund.physical_qty(trade.ts_code),
                    }
                )
                continue

            nav_pre = fund.nav(_build_raw_prices(fund, batch_prices))
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
            fund.stock_last_trade_time[trade.ts_code] = trade_time
            if result:
                if "低于最小手数" in result.action:
                    skipped_lot += 1
                if result.qty_delta > 0 and not fund.holding_opened_at.get(trade.ts_code):
                    fund.holding_opened_at[trade.ts_code] = trade_time
                nav_after = fund.nav(_build_raw_prices(fund, batch_prices))
                trade_logs.append(_result_to_log(trade_time, acct_code, acct_name, trade, result, raw_px, hfq_px, nav_after))

        for code, raw_px in batch_prices.items():
            fund.last_raw_marks[code] = raw_px
        for code, hfq in batch_hfq.items():
            fund.last_hfq_marks[code] = hfq

        # 阶段B：活跃池 + 换仓 + 资金配平
        scores = _compute_follow_scores(mirror, batch_increase_codes)
        pool = _active_pool(scores, cfg.max_positions)
        raw_prices = _build_raw_prices(fund, batch_prices)
        rotate_logs = _rotate_for_pool(fund, pool, scores, raw_prices, trade_time, star_unlocked)
        if rotate_logs:
            rotate_triggers += len(rotate_logs)
            trade_logs.extend(rotate_logs)
        raw_prices = _build_raw_prices(fund, batch_prices)
        rebalance_logs = _rebalance_deploy(
            fund, pool, scores, raw_prices, batch, mirror, trade_time, star_unlocked
        )
        if rebalance_logs:
            rebalance_triggers += len(rebalance_logs)
            trade_logs.extend(rebalance_logs)

        raw_prices = _build_raw_prices(fund, batch_prices)
        cap_logs = enforce_stock_cap(fund, raw_prices, cfg.max_stock_pct, trade_time)
        if cap_logs:
            cap_triggers += len(cap_logs)
            trade_logs.extend(cap_logs)

        raw_prices = _build_raw_prices(fund, batch_prices)
        nav_after = fund.nav(raw_prices)
        hfq_prices = _build_hfq_prices(fund, batch_hfq)
        nav_after_hfq = fund.nav_hfq(hfq_prices)

        equity_curve.append(
            {
                "trade_time": trade_time,
                "total_nav": round(nav_after, 2),
                "total_nav_hfq": round(nav_after_hfq, 2),
                "cum_return_pct": round((nav_after_hfq / cfg.initial_capital - 1.0) * 100, 2),
                "profit": round(nav_after - cfg.initial_capital, 2),
                "profit_hfq": round(nav_after_hfq - cfg.initial_capital, 2),
            }
        )

    holding_codes = {code for code, h in fund.holdings.items() if h.qty > 1e-12}
    latest_hfq = load_latest_hfq_marks(holding_codes)
    hfq_mark_prices = {code: price for code, (_, price) in latest_hfq.items()}
    for code, mark in fund.last_hfq_marks.items():
        hfq_mark_prices.setdefault(code, mark)

    raw_mark_prices: dict[str, float] = {}
    for code in holding_codes:
        raw_mark_prices[code] = fund.last_raw_marks.get(code, 0.0)

    final_nav = fund.nav(raw_mark_prices)
    final_nav_hfq = fund.nav_hfq(hfq_mark_prices)
    profit = final_nav - cfg.initial_capital
    profit_hfq = final_nav_hfq - cfg.initial_capital
    cash_pct = round(fund.cash / final_nav * 100, 2) if final_nav > 0 else 0.0

    positions: list[dict] = []
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
        if log["action"] not in ("688拦截", "封顶减仓"):
            src = log["source_portfolio"]
            source_stats[src] = source_stats.get(src, 0) + 1

    realized_trades = [leg for st in fund.stats.values() for leg in st.sell_legs]
    wins = sum(1 for leg in realized_trades if leg["leg_return_pct"] >= 0)
    overview_win_rate = round(wins / len(realized_trades) * 100, 2) if realized_trades else 0.0

    return {
        "initial_capital": cfg.initial_capital,
        "final_nav": round(final_nav, 2),
        "final_nav_hfq": round(final_nav_hfq, 2),
        "profit": round(profit, 2),
        "profit_hfq": round(profit_hfq, 2),
        "return_pct": round((final_nav_hfq / cfg.initial_capital - 1.0) * 100, 2),
        "return_pct_raw": round((final_nav / cfg.initial_capital - 1.0) * 100, 2),
        "cash": round(fund.cash, 2),
        "cash_pct": cash_pct,
        "portfolio_count": len(account_names),
        "start_time": all_trades[0][2].trade_time,
        "end_time": all_trades[-1][2].trade_time,
        "blocked_688": blocked_688,
        "cap_triggers": cap_triggers,
        "rotate_triggers": rotate_triggers,
        "rebalance_triggers": rebalance_triggers,
        "skipped_lot": skipped_lot,
        "skipped_small": skipped_small,
        "trade_log_count": len(trade_logs),
        "star_unlocked": profit >= STAR_UNLOCK_PROFIT,
        "max_stock_pct": cfg.max_stock_pct * 100,
        "min_new_position_pct": cfg.min_new_position_pct,
        "max_positions": cfg.max_positions,
        "overview_win_rate": overview_win_rate,
        "trade_logs": trade_logs,
        "source_stats": source_stats,
        "positions": positions,
        "equity_curve": equity_curve,
        "grouped_stats": grouped_stats,
    }
