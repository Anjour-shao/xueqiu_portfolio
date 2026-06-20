"""分析 accounts 表里各组合历史上「重仓股」的胜率。

重仓定义：某次减仓/卖出时 from_weight >= 阈值（默认 15/20/25%）。
收益率：该笔 leg 的后复权卖价 vs 持仓 VWAP（与虚拟净值引擎一致）。

用法:
    cd backend
    python ../scripts/analyze_heavy_position_winrate.py
    python ../scripts/analyze_heavy_position_winrate.py --threshold 20
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select

from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.domain.nav_engine import (
    TradeInput,
    VirtualFund,
    fmt_trade_date,
    load_adj_map,
    load_latest_hfq_marks,
    resolve_price_hfq,
)
from xueqiu.storage.db import accounts_table, get_conn, rebalance_trades_table


@dataclass
class HeavyLeg:
    account_code: str
    account_name: str
    ts_code: str
    stock_name: str
    trade_time: str
    from_weight: float
    to_weight: float
    leg_return_pct: float
    is_win: bool


@dataclass
class AccountSummary:
    account_code: str
    account_name: str
    total_trades: int = 0
    heavy_legs: list[HeavyLeg] = field(default_factory=list)
    peak_weights: list[float] = field(default_factory=list)


def _resolve_hfq_price(trade: TradeInput, adj_map: dict) -> float | None:
    return resolve_price_hfq(trade, adj_map)


def analyze_account(
    account_code: str,
    account_name: str,
    rows: list,
    threshold: float,
) -> AccountSummary:
    trades: list[TradeInput] = []
    for r in rows:
        trades.append(
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
            )
        )

    summary = AccountSummary(account_code=account_code, account_name=account_name, total_trades=len(trades))
    if not trades:
        return summary

    ts_codes = {t.ts_code for t in trades}
    trade_dates = {fmt_trade_date(t.trade_time) for t in trades}
    adj_map = load_adj_map(ts_codes, trade_dates)
    fund = VirtualFund()

    for trade in trades:
        hfq = _resolve_hfq_price(trade, adj_map)
        if not hfq or hfq <= 0:
            continue
        raw = float(trade.price) if trade.price and trade.price > 0 else None
        prices = {trade.ts_code: hfq}
        nav_pre = fund.nav(prices)

        peak_before = max(trade.from_weight, fund.last_weights.get(trade.ts_code, 0.0))
        if trade.to_weight > peak_before:
            peak_before = trade.to_weight
        if peak_before >= threshold:
            summary.peak_weights.append(peak_before)

        leg, _, _ = fund.apply_trade(
            trade.ts_code,
            trade.stock_name,
            float(trade.from_weight),
            float(trade.to_weight),
            hfq,
            nav_pre,
            raw_price=raw,
        )

        weight_after = trade.to_weight
        fund.last_weights[trade.ts_code] = weight_after
        fund.last_marks[trade.ts_code] = hfq

        if trade.to_weight + 1e-9 < trade.from_weight and trade.from_weight >= threshold:
            if leg is not None:
                summary.heavy_legs.append(
                    HeavyLeg(
                        account_code=account_code,
                        account_name=account_name,
                        ts_code=trade.ts_code,
                        stock_name=trade.stock_name,
                        trade_time=trade.trade_time,
                        from_weight=trade.from_weight,
                        to_weight=trade.to_weight,
                        leg_return_pct=leg,
                        is_win=leg >= 0,
                    )
                )

    return summary


def _stats(legs: list[HeavyLeg]) -> dict:
    if not legs:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_return": None, "median_return": None}
    wins = sum(1 for x in legs if x.is_win)
    rets = sorted(x.leg_return_pct for x in legs)
    mid = rets[len(rets) // 2]
    return {
        "count": len(legs),
        "wins": wins,
        "losses": len(legs) - wins,
        "win_rate": round(wins / len(legs) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
        "median_return": round(mid, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="分析 accounts 重仓股胜率")
    parser.add_argument("--threshold", type=float, default=20.0, help="重仓阈值 from_weight %% (默认 20)")
    parser.add_argument("--also", type=str, default="15,25", help="额外输出的阈值，逗号分隔")
    args = parser.parse_args()
    thresholds = sorted({args.threshold, *(float(x.strip()) for x in args.also.split(",") if x.strip())})

    with get_conn() as conn:
        accounts = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_code, accounts_table.c.account_name).order_by(
                accounts_table.c.account_code
            )
        ).fetchall()

        all_rows_by_acct: dict[int, list] = {}
        for acct in accounts:
            rows = conn.execute(
                select(rebalance_trades_table)
                .where(rebalance_trades_table.c.account_id == acct.id)
                .order_by(rebalance_trades_table.c.trade_time.asc(), rebalance_trades_table.c.id.asc())
            ).fetchall()
            all_rows_by_acct[acct.id] = rows

    print(f"共 {len(accounts)} 个账户\n")
    print("口径：卖出/减仓时 from_weight >= 阈值 的 leg，收益率 = 卖价HFQ / 成本HFQ - 1\n")

    for thr in thresholds:
        print("=" * 72)
        print(f"重仓阈值 from_weight >= {thr:.0f}%")
        print("=" * 72)

        all_legs: list[HeavyLeg] = []
        per_account: list[tuple[str, str, dict]] = []

        for acct in accounts:
            rows = all_rows_by_acct.get(acct.id, [])
            sm = analyze_account(acct.account_code, acct.account_name, rows, thr)
            st = _stats(sm.heavy_legs)
            all_legs.extend(sm.heavy_legs)
            if st["count"] > 0:
                per_account.append((acct.account_code, acct.account_name, st))

        per_account.sort(key=lambda x: (-x[2]["count"], x[0]))

        total = _stats(all_legs)
        print(f"\n【全账户合计】 legs={total['count']}  胜={total['wins']} 负={total['losses']}", end="")
        if total["win_rate"] is not None:
            print(f"  胜率={total['win_rate']}%  均收益={total['avg_return']}%  中位={total['median_return']}%")
        else:
            print("  (无样本)")

        print(f"\n{'组合':<12} {'名称':<16} {'legs':>5} {'胜率':>7} {'均收益':>8} {'中位':>8}")
        print("-" * 72)
        for code, name, st in per_account:
            name_show = (name[:14] + "…") if len(name) > 15 else name
            print(
                f"{code:<12} {name_show:<16} {st['count']:>5} {st['win_rate']:>6.1f}% "
                f"{st['avg_return']:>7.2f}% {st['median_return']:>7.2f}%"
            )

        # 按股票聚合（跨账户）
        by_stock: dict[str, list[HeavyLeg]] = defaultdict(list)
        for leg in all_legs:
            by_stock[leg.ts_code].append(leg)
        stock_rows = []
        for code, legs in by_stock.items():
            st = _stats(legs)
            stock_rows.append((code, legs[0].stock_name, st))
        stock_rows.sort(key=lambda x: -x[2]["count"])

        print(f"\n【跨账户高频重仓 leg Top 12】")
        print(f"{'代码':<12} {'名称':<12} {'legs':>5} {'胜率':>7} {'均收益':>8}")
        print("-" * 52)
        for code, name, st in stock_rows[:12]:
            name_show = (name[:10] + "…") if len(name) > 11 else name
            print(f"{code:<12} {name_show:<12} {st['count']:>5} {st['win_rate']:>6.1f}% {st['avg_return']:>7.2f}%")

        # 最差/最好 leg 样例
        if all_legs:
            worst = sorted(all_legs, key=lambda x: x.leg_return_pct)[:5]
            best = sorted(all_legs, key=lambda x: x.leg_return_pct, reverse=True)[:5]
            print("\n  最差 5 笔:")
            for x in worst:
                print(f"    {x.account_code} {x.ts_code} {x.trade_time[:10]} {x.from_weight:.0f}%→{x.to_weight:.0f}%  {x.leg_return_pct:+.1f}%")
            print("  最好 5 笔:")
            for x in best:
                print(f"    {x.account_code} {x.ts_code} {x.trade_time[:10]} {x.from_weight:.0f}%→{x.to_weight:.0f}%  {x.leg_return_pct:+.1f}%")

        print()


if __name__ == "__main__":
    main()
