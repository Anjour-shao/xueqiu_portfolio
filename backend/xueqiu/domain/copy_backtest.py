"""
抄作业回测引擎：单一账户合并全部 ZH 组合信号，供 API / CLI 共用。

价格口径：
- 买卖成交、现金、市值、仓位权重：未复权价 rebalance_trades.price
- 累计收益率：持仓按后复权价盯市（cash + Σ qty×hfq）
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
STAR_UNLOCK_PROFIT = 500_000.0
LOT_SIZE = 100
MIN_NEW_POSITION_PCT = 1.0


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    max_stock_pct: float = 0.20
    star_unlock_profit: float = 500_000.0
    lot_size: int = 100
    min_new_position_pct: float = 1.0
    allow_star_market: bool = False


def _cfg() -> BacktestConfig:
    return _RUN_CFG or BacktestConfig()


def resolve_trade_price_raw(trade: TradeInput, adj_map: dict[tuple[str, str], float]) -> float | None:
    """调仓成交价：优先日志未复权 price。"""
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


def _floor_lots(qty: float) -> float:
    if qty <= 1e-12:
        return 0.0
    lot = _cfg().lot_size
    return float(int(qty // lot) * lot)


def _weight_pct(qty: float, price: float, nav_pre: float) -> float:
    if nav_pre <= 0 or qty <= 1e-12:
        return 0.0
    return qty * price / nav_pre * 100.0


@dataclass
class Holding:
    qty: float = 0.0
    vwap: float = 0.0


@dataclass
class CopyFund:
    initial_cash: float
    cash: float = 0.0
    holdings: dict[str, Holding] = field(default_factory=dict)
    last_raw_marks: dict[str, float] = field(default_factory=dict)
    last_hfq_marks: dict[str, float] = field(default_factory=dict)
    stock_names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

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

    def _holding(self, code: str) -> Holding:
        if code not in self.holdings:
            self.holdings[code] = Holding()
        return self.holdings[code]

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

    def _target_qty(
        self,
        code: str,
        master_from: float,
        master_to: float,
        price: float,
        nav_pre: float,
    ) -> float:
        cap_qty = _cfg().max_stock_pct * nav_pre / price if price > 0 and nav_pre > 0 else 0.0
        master_from = float(master_from)
        master_to = float(master_to)

        if master_to <= 1e-9:
            return 0.0

        if master_to > master_from + 1e-9:
            target_pct = min(master_to, _cfg().max_stock_pct * 100.0)
            target_qty = (target_pct / 100.0) * nav_pre / price
            return min(target_qty, cap_qty)

        if master_to + 1e-9 < master_from and master_from > 1e-9:
            holding = self.holdings.get(code)
            if holding and holding.qty > 1e-12:
                target_qty = holding.qty * (master_to / master_from)
                return min(target_qty, cap_qty)
            return 0.0

        holding = self.holdings.get(code)
        if holding and holding.qty > 1e-12:
            return min(holding.qty, cap_qty)
        return 0.0

    def apply_signal(
        self,
        code: str,
        stock_name: str,
        master_from: float,
        master_to: float,
        price: float,
        nav_pre: float,
    ) -> tuple[str, float, float]:
        self.stock_names[code] = stock_name
        holding = self._holding(code)
        target_qty = self._target_qty(code, master_from, master_to, price, nav_pre)
        delta = target_qty - holding.qty
        action = self._infer_action_label(master_from, master_to)

        if abs(delta) <= 1e-12:
            weight_after = _weight_pct(holding.qty, price, nav_pre)
            return action, 0.0, round(weight_after, 2)

        if delta > 0:
            buy_qty = _floor_lots(delta)
            max_affordable = _floor_lots(self.cash / price if price > 0 else 0.0)
            buy_qty = min(buy_qty, max_affordable)
            weight_after = _weight_pct(holding.qty, price, nav_pre)
            if buy_qty < _cfg().lot_size:
                return f"{action}(低于最小手数)", 0.0, round(weight_after, 2)
            if holding.qty <= 1e-12 and _weight_pct(buy_qty, price, nav_pre) < _cfg().min_new_position_pct:
                return f"{action}(仓位过小)", 0.0, round(weight_after, 2)
            if holding.qty <= 1e-12:
                holding.vwap = price
            else:
                holding.vwap = (holding.qty * holding.vwap + buy_qty * price) / (holding.qty + buy_qty)
            holding.qty += buy_qty
            self.cash -= buy_qty * price
            weight_after = _weight_pct(holding.qty, price, nav_pre)
            return action, buy_qty, round(weight_after, 2)

        is_clear = float(master_to) <= 1e-9
        if is_clear:
            sell_qty = holding.qty
        else:
            sell_qty = min(_floor_lots(-delta), holding.qty)
        if sell_qty <= 1e-12:
            weight_after = _weight_pct(holding.qty, price, nav_pre)
            return action, 0.0, round(weight_after, 2)
        self.cash += sell_qty * price
        holding.qty -= sell_qty
        if holding.qty <= 1e-12:
            holding.qty = 0.0
            holding.vwap = 0.0
        weight_after = _weight_pct(holding.qty, price, nav_pre)
        return action, -sell_qty, round(weight_after, 2)

    def trim_to_cap(self, code: str, price: float, max_pct: float, nav_pre: float) -> float:
        holding = self.holdings.get(code)
        if not holding or holding.qty <= 1e-12 or nav_pre <= 0:
            return 0.0
        stock_value = holding.qty * price
        cap_value = nav_pre * max_pct
        if stock_value <= cap_value + 1e-6:
            return 0.0
        sell_qty = holding.qty * (1.0 - cap_value / stock_value)
        sell_qty = _floor_lots(sell_qty)
        if sell_qty <= 1e-12:
            return 0.0
        remaining = holding.qty - sell_qty
        if 0 < remaining < _cfg().lot_size:
            sell_qty = holding.qty
        if sell_qty <= 1e-12:
            return 0.0
        self.cash += sell_qty * price
        holding.qty -= sell_qty
        if holding.qty <= 1e-12:
            holding.qty = 0.0
            holding.vwap = 0.0
        return -sell_qty


def _is_buy_or_increase(from_weight: float, to_weight: float) -> bool:
    return to_weight > from_weight + 1e-9


def _is_star_market(code: str) -> bool:
    return to_xueqiu_code(code).upper().startswith("SH688")


def _build_raw_prices(fund: CopyFund, batch_raw: dict[str, float]) -> dict[str, float]:
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


def _build_hfq_prices(fund: CopyFund, batch_hfq: dict[str, float]) -> dict[str, float]:
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


def _raw_mark_from_hfq(code: str, hfq: float, fund: CopyFund) -> float:
    raw_ref = fund.last_raw_marks.get(code)
    hfq_ref = fund.last_hfq_marks.get(code)
    if raw_ref and hfq_ref and hfq_ref > 0:
        return hfq * (raw_ref / hfq_ref)
    return hfq


def enforce_stock_cap(
    fund: CopyFund, prices: dict[str, float], max_pct: float, trade_time: str = ""
) -> list[dict]:
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
        qty_delta = fund.trim_to_cap(code, price, max_pct, nav_pre)
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
                    "price": round(price, 4),
                    "price_hfq": round(fund.last_hfq_marks.get(code, 0.0), 4) or None,
                    "qty_delta": round(qty_delta, 4),
                    "our_weight_pct": round(max_pct * 100, 2),
                    "nav_after": round(fund.nav(prices), 2),
                    "note": "单票超上限自动裁剪",
                }
            )
            nav_pre = fund.nav(prices)
    return logs


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

    fund = CopyFund(initial_cash=cfg.initial_capital)
    ts_codes = {t.ts_code for _, _, t in all_trades}
    trade_dates = {fmt_trade_date(t.trade_time) for _, _, t in all_trades}
    adj_map = load_adj_map(ts_codes, trade_dates)

    trade_logs: list[dict] = []
    equity_curve: list[dict] = []
    blocked_688 = 0
    cap_triggers = 0
    skipped_lot = 0
    skipped_small = 0
    last_curve_time = ""

    for acct_code, acct_name, trade in all_trades:
        raw_px = resolve_trade_price_raw(trade, adj_map)
        hfq_px = resolve_price_hfq(trade, adj_map)
        if not raw_px or raw_px <= 0:
            continue

        raw_prices = _build_raw_prices(fund, {trade.ts_code: raw_px})
        nav_before = fund.nav(raw_prices)
        star_unlocked = nav_before - cfg.initial_capital >= cfg.star_unlock_profit - 1e-6

        if (
            _is_buy_or_increase(trade.from_weight, trade.to_weight)
            and _is_star_market(trade.ts_code)
            and not cfg.allow_star_market
            and not star_unlocked
        ):
            blocked_688 += 1
            trade_logs.append(
                {
                    "trade_time": trade.trade_time,
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
                        fund.holdings.get(trade.ts_code, Holding()).qty * raw_px / nav_before * 100, 2
                    )
                    if nav_before > 0
                    else 0.0,
                    "nav_after": round(nav_before, 2),
                    "note": "累计盈利未达50万",
                }
            )
            continue

        nav_pre = fund.nav(raw_prices)
        action, qty_delta, our_weight = fund.apply_signal(
            trade.ts_code,
            trade.stock_name,
            float(trade.from_weight),
            float(trade.to_weight),
            raw_px,
            nav_pre,
        )
        fund.last_raw_marks[trade.ts_code] = raw_px
        if hfq_px and hfq_px > 0:
            fund.last_hfq_marks[trade.ts_code] = hfq_px

        if "低于最小手数" in action:
            skipped_lot += 1
        elif "仓位过小" in action:
            skipped_small += 1

        raw_prices = _build_raw_prices(fund, {trade.ts_code: raw_px})
        cap_logs = enforce_stock_cap(fund, raw_prices, cfg.max_stock_pct, trade.trade_time)
        if cap_logs:
            cap_triggers += len(cap_logs)
            trade_logs.extend(cap_logs)

        raw_prices = _build_raw_prices(fund, {trade.ts_code: raw_px})
        nav_after = fund.nav(raw_prices)
        hfq_prices = _build_hfq_prices(fund, {trade.ts_code: hfq_px} if hfq_px else {})
        nav_after_hfq = fund.nav_hfq(hfq_prices)

        if abs(qty_delta) > 1e-12 or "(" in action:
            trade_logs.append(
                {
                    "trade_time": trade.trade_time,
                    "source_portfolio": acct_code,
                    "source_name": acct_name,
                    "stock_name": trade.stock_name,
                    "ts_code": trade.ts_code,
                    "master_from": trade.from_weight,
                    "master_to": trade.to_weight,
                    "action": action,
                    "price": round(raw_px, 4),
                    "price_hfq": round(hfq_px, 4) if hfq_px else None,
                    "qty_delta": round(qty_delta, 4),
                    "our_weight_pct": our_weight,
                    "nav_after": round(nav_after, 2),
                    "note": "",
                }
            )

        if trade.trade_time != last_curve_time:
            equity_curve.append(
                {
                    "trade_time": trade.trade_time,
                    "total_nav": round(nav_after, 2),
                    "total_nav_hfq": round(nav_after_hfq, 2),
                    "cum_return_pct": round((nav_after_hfq / cfg.initial_capital - 1.0) * 100, 2),
                    "profit": round(nav_after - cfg.initial_capital, 2),
                    "profit_hfq": round(nav_after_hfq - cfg.initial_capital, 2),
                }
            )
            last_curve_time = trade.trade_time

    holding_codes = {code for code, h in fund.holdings.items() if h.qty > 1e-12}
    latest_hfq = load_latest_hfq_marks(holding_codes)
    hfq_mark_prices = {code: price for code, (_, price) in latest_hfq.items()}
    for code, mark in fund.last_hfq_marks.items():
        hfq_mark_prices.setdefault(code, mark)

    # 市值/NAV 用未复权：优先末次调仓日志价，不用 close_hfq 直接当现价
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
        positions.append(
            {
                "ts_code": code,
                "stock_name": fund.stock_names.get(code, code),
                "qty": round(holding.qty, 0),
                "mark_price": round(raw_price, 4),
                "mark_price_hfq": round(hfq_price, 4) if hfq_price else None,
                "value": round(value, 2),
                "weight_pct": round(value / final_nav * 100, 2) if final_nav > 0 else 0.0,
            }
        )
    positions.sort(key=lambda x: -x["value"])

    source_stats: dict[str, int] = {}
    for log in trade_logs:
        if log["action"] not in ("688拦截", "封顶减仓"):
            src = log["source_portfolio"]
            source_stats[src] = source_stats.get(src, 0) + 1

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
        "skipped_lot": skipped_lot,
        "skipped_small": skipped_small,
        "trade_log_count": len(trade_logs),
        "star_unlocked": profit >= cfg.star_unlock_profit,
        "star_unlock_profit": cfg.star_unlock_profit,
        "max_stock_pct": cfg.max_stock_pct * 100,
        "lot_size": cfg.lot_size,
        "min_new_position_pct": cfg.min_new_position_pct,
        "allow_star_market": cfg.allow_star_market,
        "trade_logs": trade_logs,
        "source_stats": source_stats,
        "positions": positions,
        "equity_curve": equity_curve,
    }
