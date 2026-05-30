"""抄作业回测 CLI：模拟跟单多个雪球组合，输出收益与交易明细。

结果 CSV 写入 scripts/backtest_output/。看板内也有「抄作业回测」对话框，功能相同。

用法:
    cd backend
    python ../scripts/backtest_copy_portfolios.py
    python ../scripts/backtest_copy_portfolios.py --capital 1000000
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.domain.copy_backtest import run_backtest
from xueqiu.storage.db import init_db

OUTPUT_DIR = Path(__file__).resolve().parent / "backtest_output"


def print_report(result: dict) -> None:
    print("=" * 60)
    print("抄作业回测报告（单一账户 · 先到先得 · 完全跟踪组合）")
    print("=" * 60)
    print(f"回测区间:     {result['start_time']} ~ {result['end_time']}")
    print(f"信号来源:     {result['portfolio_count']} 个 ZH 组合（合并为 1 账户）")
    print(f"初始资金:     {result['initial_capital']:,.2f}")
    print(f"最终资产:     {result['final_nav']:,.2f}")
    print(f"累计收益:     {result['profit']:+,.2f}  ({result['return_pct']:+.2f}%)")
    print(f"现金占比:     {result['cash_pct']:.2f}%")
    print(f"单票上限:     {result['max_stock_pct']:.0f}%")
    print(f"688 拦截:     {result['blocked_688']}  |  低于手数: {result['skipped_lot']}  |  仓位过小: {result['skipped_small']}")
    print(f"20% 封顶次数: {result['cap_triggers']}")
    print(f"跟单记录:     {result['trade_log_count']} 条")
    print()
    print("── 信号来源统计 ──")
    for code, cnt in sorted(result["source_stats"].items(), key=lambda x: -x[1]):
        print(f"  {code}: {cnt} 笔")
    print()
    print("── 最终持仓 Top ──")
    for row in result["positions"][:15]:
        print(
            f"  {row['stock_name']} {row['ts_code']} | "
            f"市值 {row['value']:,.2f} ({row['weight_pct']:.1f}%)"
        )
    if not result["positions"]:
        print("  （空仓）")
    print("=" * 60)


def write_equity_csv(result: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "equity_curve.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["trade_time", "total_nav", "total_nav_hfq", "cum_return_pct", "profit", "profit_hfq"],
        )
        writer.writeheader()
        writer.writerows(result["equity_curve"])
    return path


def write_trades_txt(result: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "trades.txt"
    lines = [
        "抄作业回测 — 买卖记录（完全跟踪组合 · 单票封顶20% · 100股整手）",
        f"区间: {result['start_time']} ~ {result['end_time']}",
        f"初始 {result['initial_capital']:,.0f} → 最终 {result['final_nav']:,.2f} ({result['return_pct']:+.2f}%)",
        "",
        f"{'时间':<20} {'来源':<12} {'股票':<10} {'动作':<10} {'主理仓位':<14} {'成交价':<10} {'股数变动':<12} {'我方仓位%':<10} {'账户净值':<12} 备注",
        "-" * 130,
    ]
    for log in result["trade_logs"]:
        mf = log.get("master_from")
        mt = log.get("master_to")
        if mf is None:
            master = str(mt)
        else:
            master = f"{mf}→{mt}"
        lines.append(
            f"{log['trade_time']:<20} "
            f"{log['source_portfolio']:<12} "
            f"{log['stock_name']:<10} "
            f"{log['action']:<10} "
            f"{master:<14} "
            f"{log['price']:<10.2f} "
            f"{log['qty_delta']:>+12.0f} "
            f"{log['our_weight_pct']:<10.2f} "
            f"{log['nav_after']:<12.2f} "
            f"{log.get('note', '')}"
        )
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def main() -> None:
    init_db()
    result = run_backtest()
    print_report(result)
    csv_path = write_equity_csv(result)
    txt_path = write_trades_txt(result)
    print(f"净值曲线: {csv_path}")
    print(f"买卖记录: {txt_path}")


if __name__ == "__main__":
    main()
