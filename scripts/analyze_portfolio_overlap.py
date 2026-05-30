"""组合重叠与持仓周期分析（只读，不写回策略参数）。

用法:
    cd backend
    python ../scripts/analyze_portfolio_overlap.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.domain.copy_backtest import _update_mirror, load_portfolio_trades
from xueqiu.domain.copy_strategies import _group_batches, analyze_consensus_stats
from xueqiu.storage.db import init_db

OUTPUT_DIR = Path(__file__).resolve().parent / "backtest_output"


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _days_between(a: str, b: str) -> int:
    return max(0, (_parse_date(b) - _parse_date(a)).days)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def analyze_holding_periods(
    all_trades: list,
) -> dict[str, float | int | list[float]]:
    """每 (组合, 股票) 从首次加仓到清仓或数据末日的持仓天数。"""
    open_at: dict[tuple[str, str], str] = {}
    periods: list[float] = []
    last_trade_time = all_trades[-1][2].trade_time if all_trades else ""

    for acct, _, trade in all_trades:
        key = (acct, trade.ts_code)
        tt = trade.trade_time
        from_w = float(trade.from_weight)
        to_w = float(trade.to_weight)
        if to_w > from_w + 1e-9 and key not in open_at:
            open_at[key] = tt
        elif to_w + 1e-9 < from_w and key in open_at:
            periods.append(_days_between(open_at[key], tt))
            del open_at[key]

    for key, start in open_at.items():
        periods.append(_days_between(start, last_trade_time))

    if not periods:
        return {"count": 0, "avg_days": 0.0, "median_days": 0.0, "p25_days": 0.0, "p75_days": 0.0}

    periods_sorted = sorted(periods)
    n = len(periods_sorted)
    mid = n // 2
    median = periods_sorted[mid] if n % 2 else (periods_sorted[mid - 1] + periods_sorted[mid]) / 2
    p25 = periods_sorted[int(n * 0.25)]
    p75 = periods_sorted[min(n - 1, int(n * 0.75))]
    return {
        "count": n,
        "avg_days": round(sum(periods) / n, 1),
        "median_days": round(median, 1),
        "p25_days": round(p25, 1),
        "p75_days": round(p75, 1),
    }


def analyze_overlap_matrix(
    all_trades: list,
) -> tuple[dict[str, dict[str, float]], list[dict]]:
    """按调仓批次后 mirror 快照，累计组合两两 Jaccard。"""
    mirror: dict[tuple[str, str], float] = {}
    pair_sum: dict[tuple[str, str], float] = defaultdict(float)
    pair_cnt: dict[tuple[str, str], int] = defaultdict(int)
    portfolios: set[str] = set()

    for _tt, batch in _group_batches(all_trades):
        _update_mirror(mirror, batch)
        holdings_by_p: dict[str, set[str]] = defaultdict(set)
        for (p, c), w in mirror.items():
            if w > 1e-9:
                holdings_by_p[p].add(c)
                portfolios.add(p)

        plist = sorted(holdings_by_p.keys())
        for i, p1 in enumerate(plist):
            for p2 in plist[i + 1 :]:
                j = _jaccard(holdings_by_p[p1], holdings_by_p[p2])
                key = (p1, p2) if p1 < p2 else (p2, p1)
                pair_sum[key] += j
                pair_cnt[key] += 1

    matrix: dict[str, dict[str, float]] = defaultdict(dict)
    top_pairs: list[dict] = []
    for (p1, p2), total in pair_sum.items():
        avg = round(total / pair_cnt[(p1, p2)], 4)
        matrix[p1][p2] = avg
        matrix[p2][p1] = avg
        top_pairs.append({"portfolio_a": p1, "portfolio_b": p2, "avg_jaccard": avg, "snapshots": pair_cnt[(p1, p2)]})

    top_pairs.sort(key=lambda x: -x["avg_jaccard"])
    return dict(matrix), top_pairs[:15]


def analyze_rebalance_frequency(all_trades: list) -> dict[str, dict[str, float]]:
    by_portfolio: dict[str, int] = defaultdict(int)
    first_date: dict[str, str] = {}
    last_date: dict[str, str] = {}

    for acct, _, trade in all_trades:
        by_portfolio[acct] += 1
        tt = trade.trade_time[:10]
        first_date.setdefault(acct, tt)
        last_date[acct] = tt

    out: dict[str, dict[str, float]] = {}
    for acct, cnt in sorted(by_portfolio.items()):
        span_years = max(_days_between(first_date[acct], last_date[acct]) / 365.25, 0.25)
        out[acct] = {
            "trade_count": cnt,
            "years_span": round(span_years, 2),
            "trades_per_year": round(cnt / span_years, 1),
        }
    return out


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# 组合重叠与持仓分析报告",
        "",
        f"生成时间: {report['generated_at']}",
        f"调仓批次: {report['consensus']['total_batches']}",
        "",
        "## 持仓周期（每组合-每股票）",
        "",
        f"- 样本数: {report['holding_periods']['count']}",
        f"- 平均: {report['holding_periods']['avg_days']} 天",
        f"- 中位数: {report['holding_periods']['median_days']} 天",
        f"- P25–P75: {report['holding_periods']['p25_days']} – {report['holding_periods']['p75_days']} 天",
        "",
        "## 共识频率",
        "",
        f"- 同日≥2组合买同一股: {report['consensus']['same_day_2plus_buy_batches']} 批",
        f"- ≥2组合同时持有快照: {report['consensus']['pair_or_more_snapshots']} 次",
        f"- 持有人数分布: {report['consensus']['holder_snapshots']}",
        "",
        "## 组合重叠 Top（平均 Jaccard）",
        "",
        "| 组合A | 组合B | Jaccard | 快照数 |",
        "|-------|-------|---------|--------|",
    ]
    for row in report["top_overlap_pairs"]:
        lines.append(
            f"| {row['portfolio_a']} | {row['portfolio_b']} | {row['avg_jaccard']:.3f} | {row['snapshots']} |"
        )
    lines.extend(["", "## 组合调仓频率", "", "| 组合 | 调仓笔数 | 跨度(年) | 年均笔数 |", "|------|----------|----------|----------|"])
    for acct, stats in report["rebalance_frequency"].items():
        lines.append(
            f"| {acct} | {stats['trade_count']} | {stats['years_span']} | {stats['trades_per_year']} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    init_db()
    account_names, all_trades = load_portfolio_trades()
    if not all_trades:
        print("无调仓数据")
        return

    consensus = analyze_consensus_stats()
    holding = analyze_holding_periods(all_trades)
    _matrix, top_pairs = analyze_overlap_matrix(all_trades)
    rebalance = analyze_rebalance_frequency(all_trades)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "portfolio_count": len(account_names),
        "trade_count": len(all_trades),
        "consensus": consensus,
        "holding_periods": holding,
        "top_overlap_pairs": top_pairs,
        "rebalance_frequency": rebalance,
        "overlap_matrix": _matrix,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "portfolio_analysis.json"
    md_path = OUTPUT_DIR / "portfolio_analysis.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, md_path)

    print(f"组合数: {len(account_names)}")
    print(f"持仓周期中位数: {holding['median_days']} 天 (n={holding['count']})")
    print(f"同日双组合买入: {consensus['same_day_2plus_buy_batches']} 批")
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
