"""多策略抄作业回测对比：一次跑完全部方案并输出报告。

用法:
    cd backend
    python ../scripts/compare_copy_strategies.py
    python ../scripts/compare_copy_strategies.py --start-date 2023-01-01
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.domain.copy_strategies import (
    STRATEGY_CATALOG,
    STRATEGY_FOCUS,
    analyze_consensus_stats,
    compute_portfolio_benchmarks,
    run_all_strategies,
    run_strategy,
    strategy_to_summary,
)
from xueqiu.storage.db import init_db

OUTPUT_DIR = Path(__file__).resolve().parent / "backtest_output"


def _fmt_pct(v) -> str:
    if v is None:
        return "-"
    return f"{v:+.2f}%"


def _fmt_num(v) -> str:
    if v is None:
        return "-"
    return f"{v}"


def print_strategy_table(results: list[dict], start_date: str | None = None) -> None:
    print()
    title = f"策略对比（自 {start_date} 起）" if start_date else "策略对比（全历史）"
    print("=" * 118)
    print(title)
    print("=" * 118)
    ret_col = "自入场收益" if start_date else "全历史收益"
    header = (
        f"{'策略':<16} {'类型':>4} {ret_col:>12} {'最大回撤':>8} {'夏普*':>6} {'现金%':>6} {'持仓':>4} "
        f"{'无slice':>7} {'换仓':>5} {'配平':>5}"
    )
    print(header)
    print("-" * 118)
    for r in sorted(results, key=lambda x: -(x.get("return_since_entry") or x.get("return_pct") or 0)):
        ret = r.get("return_since_entry") if start_date else r.get("return_pct")
        focus = STRATEGY_FOCUS.get(r.get("strategy_id", ""), "-")
        print(
            f"{r.get('label', r['strategy_id']):<16} "
            f"{focus:>4} "
            f"{_fmt_pct(ret):>12} "
            f"{_fmt_num(r.get('max_drawdown_pct')):>7}% "
            f"{_fmt_num(r.get('sharpe_proxy')):>6} "
            f"{_fmt_num(r.get('cash_pct')):>6} "
            f"{_fmt_num(r.get('position_count')):>4} "
            f"{_fmt_num(r.get('orphan_sell_count')):>7} "
            f"{_fmt_num(r.get('rotate_count')):>5} "
            f"{_fmt_num(r.get('rebalance_count')):>5}"
        )
    print("-" * 118)
    print("* 夏普为调仓期收益序列的近似值，仅供横向对比")
    print()


def print_portfolio_benchmarks(rows: list[dict]) -> None:
    print("=" * 100)
    print("单组合基准（各自独立伪净值，同起点 10 万）")
    print("=" * 100)
    print(f"{'组合':<12} {'名称':<16} {'全历史':>10} {'2020至今':>10} {'2023至今':>10} {'最大回撤':>8}")
    print("-" * 100)
    for row in rows:
        print(
            f"{row['portfolio']:<12} "
            f"{row['name'][:14]:<16} "
            f"{_fmt_pct(row['return_pct']):>10} "
            f"{_fmt_pct(row['return_since_2020']):>10} "
            f"{_fmt_pct(row['return_since_2023']):>10} "
            f"{row.get('max_drawdown_pct', '-'):>7}%"
        )
    print("-" * 100)
    print("注：全历史最高者往往因起步最早；请重点看 2020/2023 分段")
    print()


def write_csv(results: list[dict], benchmarks: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "strategy_compare.csv"
    fields = [
        "strategy_id",
        "label",
        "description",
        "strategy_focus",
        "return_pct",
        "return_since_2020",
        "return_since_2023",
        "max_drawdown_pct",
        "sharpe_proxy",
        "cash_pct",
        "position_count",
        "orphan_sell_count",
        "rotate_count",
        "rebalance_count",
        "trade_log_count",
        "leader_switches",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow({**r, "strategy_focus": STRATEGY_FOCUS.get(r.get("strategy_id", ""), "")})
        w.writerow({})
        w.writerow({"strategy_id": "# benchmarks"})
        for row in benchmarks:
            w.writerow(
                {
                    "strategy_id": row["portfolio"],
                    "label": row["name"],
                    "return_pct": row["return_pct"],
                    "return_since_2020": row["return_since_2020"],
                    "return_since_2023": row["return_since_2023"],
                    "max_drawdown_pct": row["max_drawdown_pct"],
                }
            )
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=None, help="入场日 YYYY-MM-DD，从该日起空仓跟单")
    parser.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金，默认 100 万")
    args = parser.parse_args()

    init_db()
    consensus = analyze_consensus_stats()
    print("── 数据共识频率（解释为何严格共识很难触发）──")
    print(f"  调仓批次: {consensus['total_batches']}")
    print(f"  同日≥2组合买同一股: {consensus['same_day_2plus_buy_batches']} 次（从未同日合买则无法当日共识）")
    print(f"  mirror快照分布: {consensus['holder_snapshots']}")
    print(f"  ≥2组合同时持有快照: {consensus['pair_or_more_snapshots']} 次")
    print(f"  历史最多同时持有组合数: {consensus['max_holders_ever']}")
    print()

    print(f"正在运行全部策略回测（初始资金 {args.capital:,.0f}）...")
    if args.start_date:
        results = []
        for spec in STRATEGY_CATALOG:
            raw = run_strategy(spec.id, initial_capital=args.capital, start_date=args.start_date)
            raw["label"] = spec.label
            raw["description"] = spec.description
            results.append(strategy_to_summary(raw) | {"strategy_id": spec.id.value, "label": spec.label})
    else:
        results = run_all_strategies(initial_capital=args.capital)
    benchmarks = compute_portfolio_benchmarks()
    print_strategy_table(results, start_date=args.start_date)
    print_portfolio_benchmarks(benchmarks)

    best_strategy = max(
        results,
        key=lambda x: (x.get("return_since_entry") if args.start_date else x.get("return_pct")) or -1e18,
    )
    print("── 对比摘要 ──")
    ret = best_strategy.get("return_since_entry") if args.start_date else best_strategy.get("return_pct")
    print(f"最强策略: {best_strategy.get('label')} ({_fmt_pct(ret)})，现金% {best_strategy.get('cash_pct')}")
    for spec in STRATEGY_CATALOG:
        r = next(x for x in results if x["strategy_id"] == spec.id.value)
        focus = STRATEGY_FOCUS.get(spec.id.value, "")
        print(f"  [{focus or '-'}] {spec.label}: {spec.description} · 现金% {r.get('cash_pct')}")
    print()

    csv_path = write_csv(results, benchmarks)
    print(f"详细 CSV: {csv_path}")


if __name__ == "__main__":
    main()
