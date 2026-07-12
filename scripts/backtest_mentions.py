"""基于 AI 提取结果，回测博主提及后股价表现。

用法:
    cd backend
    python ../scripts/backtest_mentions.py
    python ../scripts/backtest_mentions.py --user-id 7845696728
    python ../scripts/backtest_mentions.py --min-mentions 5   # 只统计提及≥N次的股票
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

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import create_engine, text
import numpy as np

from xueqiu.storage.db import engine as xueqiu_engine

OUTPUT_DIR = ROOT / "data"
AS_DB_URL = "mysql+pymysql://root:shaojunjie0808@127.0.0.1:3306/ashare_system?charset=utf8mb4"

# 复权因子在 daily_prices 中，这里不复权，直接用 close 做简单收益率
# 因为 adj_factor 存的是后复权因子，需要额外处理

FORWARD_DAYS = [5, 10, 20, 60]  # 交易日


def _code_to_ts_code(code: str) -> str | None:
    """6 位代码 → ts_code 格式。"""
    code = str(code).strip()
    if not code or len(code) < 6 or not code[:6].isdigit():
        return None
    c = code[:6]
    if c.startswith(("60", "68")):
        return f"{c}.SH"
    elif c.startswith(("00", "30", "301", "300")):
        return f"{c}.SZ"
    elif c.startswith(("8", "4")):
        return f"{c}.BJ"
    return None


def _load_trade_calendar(eng) -> set[str]:
    """加载交易日集合。"""
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT cal_date FROM trade_cal WHERE is_open = 1 AND exchange = 'SSE' ORDER BY cal_date")
        ).fetchall()
    return {r[0] for r in rows}


def _next_trade_date(date_str: str, cal: set[str], offset: int = 0) -> str | None:
    """找到 date_str 之后第 offset 个交易日。offset=0 表示 date_str 本身或之后最近交易日。"""
    d = date_str[:10].replace("-", "")
    if not d.isdigit():
        return None
    # 找 >= d 的第一个交易日
    sorted_dates = sorted(cal)
    for i, td in enumerate(sorted_dates):
        if td >= d:
            idx = i + offset
            if idx < len(sorted_dates):
                return sorted_dates[idx]
            return None
    return None


def _get_prices(eng, ts_codes: list[str], dates: list[str]) -> dict[tuple[str, str], float]:
    """批量查询 (ts_code, trade_date) → close 价格。"""
    if not ts_codes or not dates:
        return {}
    result = {}
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """SELECT ts_code, trade_date, close FROM daily_prices
                   WHERE ts_code IN :codes AND trade_date IN :dates"""
            ),
            {"codes": tuple(ts_codes), "dates": tuple(dates)},
        ).fetchall()
    for r in rows:
        result[(r[0], r[1])] = float(r[2])
    return result


def _get_extractions(user_id: str | None = None) -> list[dict]:
    """获取所有含个股的提取结果。"""
    cond = "AND p.user_id = :uid" if user_id else ""
    params = {"uid": int(user_id)} if user_id else {}

    with xueqiu_engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""SELECT es.stock_name, es.stock_code, es.sentiment, es.time_horizon, es.confidence,
                           p.post_id, p.created_at
                    FROM extraction_stocks es
                    JOIN xueqiu_extractions e ON es.extraction_id = e.id
                    JOIN xueqiu_posts p ON e.post_id = p.post_id
                    WHERE e.has_info = 1 {cond}
                    ORDER BY p.created_at ASC"""
            ),
            params,
        ).fetchall()

    return [
        {
            "stock_name": r[0],
            "stock_code": r[1],
            "sentiment": r[2] or "未明确",
            "time_horizon": r[3] or "未明确",
            "confidence": float(r[4] or 0.5),
            "post_id": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="回测博主提及后股价表现")
    parser.add_argument("--user-id", default="7845696728", help="用户 ID")
    parser.add_argument("--min-mentions", type=int, default=3, help="最少提及次数（低于此的不统计）")
    args = parser.parse_args()

    print("=== 股价表现回测 ===")
    print(f"用户: {args.user_id}")
    print(f"最少提及: {args.min_mentions} 次")
    print()

    # 连接 ashare 数据库
    as_eng = create_engine(AS_DB_URL, future=True)
    cal = _load_trade_calendar(as_eng)
    print(f"交易日历: {len(cal)} 天")

    # 加载提取结果
    mentions = _get_extractions(args.user_id)
    print(f"个股提及记录: {len(mentions)} 条")

    # 过滤：必须有有效代码 + 代码能映射到 ts_code
    valid = []
    skipped_code = 0
    skipped_no_ts = 0
    for m in mentions:
        code = str(m.get("stock_code") or "").strip()
        if not code or code == "None":
            skipped_code += 1
            continue
        ts = _code_to_ts_code(code)
        if not ts:
            skipped_no_ts += 1
            continue
        m["ts_code"] = ts
        valid.append(m)

    print(f"有效提及: {len(valid)} (跳过: 无代码{skipped_code}, 无法映射{skipped_no_ts})")

    # 按 (stock_name, ts_code) 分组
    stock_mentions: dict[tuple, list[dict]] = defaultdict(list)
    for m in valid:
        key = (m["stock_name"], m["ts_code"])
        stock_mentions[key].append(m)

    # -------- 逐条计算前向收益 --------
    # 收集所有需要查询的 (ts_code, trade_date) 组合
    all_queries: set[tuple[str, str]] = set()
    mention_prices: list[dict] = []  # [{name, code, ts_code, date, sentiment, horizon, T0_price, ...}]

    for (name, ts_code), mlist in stock_mentions.items():
        for m in mlist:
            t0 = _next_trade_date(m["created_at"], cal, 0)
            if not t0:
                continue
            all_queries.add((ts_code, t0))
            for offset in FORWARD_DAYS:
                tn = _next_trade_date(m["created_at"], cal, offset)
                if tn:
                    all_queries.add((ts_code, tn))
            mention_prices.append({
                "name": name,
                "code": m["stock_code"],
                "ts_code": ts_code,
                "sentiment": m["sentiment"],
                "horizon": m["time_horizon"],
                "post_date": m["created_at"][:10],
                "t0_date": t0,
            })

    print(f"查询价格点: {len(all_queries)} 个")

    # 批量查价格
    ts_codes_list = list({q[0] for q in all_queries})
    dates_list = list({q[1] for q in all_queries})
    price_map = _get_prices(as_eng, ts_codes_list, dates_list)

    # 计算每条提及的收益
    results: list[dict] = []
    for mp in mention_prices:
        ts = mp["ts_code"]
        t0 = mp["t0_date"]
        p0 = price_map.get((ts, t0))
        if p0 is None or p0 <= 0:
            continue

        returns = {}
        for offset in FORWARD_DAYS:
            tn = _next_trade_date(mp["post_date"], cal, offset)
            if tn:
                pn = price_map.get((ts, tn))
                if pn is not None and pn > 0:
                    returns[f"r{offset}d"] = round((pn - p0) / p0 * 100, 2)

        if returns:
            mp["returns"] = returns
            results.append(mp)

    print(f"有效结果: {len(results)} 条（有 T0 价格的提及）")

    # -------- 聚合统计 --------
    # 1. 按个股
    stock_stats = {}
    for r in results:
        key = (r["name"], r["ts_code"])
        if key not in stock_stats:
            stock_stats[key] = {"returns_5d": [], "returns_10d": [], "returns_20d": [], "returns_60d": [], "sentiments": [], "count": 0}
        ss = stock_stats[key]
        ss["count"] += 1
        ss["sentiments"].append(r["sentiment"])
        for k, v in r["returns"].items():
            ss[f"returns_{k[1:]}"].append(v)  # "r5d" → "returns_5d"

    # 2. 按看好程度
    sentiment_stats: dict[str, dict] = defaultdict(lambda: {"returns_5d": [], "returns_10d": [], "returns_20d": [], "returns_60d": [], "count": 0})
    for r in results:
        sent = r["sentiment"]
        sentiment_stats[sent]["count"] += 1
        for k, v in r["returns"].items():
            sentiment_stats[sent][f"returns_{k[1:]}"].append(v)

    # -------- 生成报告 --------
    lines = []
    lines.append("# 博主提及后股价表现回测")
    lines.append(f"**用户 ID**: {args.user_id} | **分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**有效提及**: {len(results)} 条 | **个股数**: {len(stock_stats)}")
    lines.append("")
    lines.append("> 收益 = (T+N日收盘价 - T0日收盘价) / T0日收盘价 × 100%")
    lines.append("> T0 = 帖子发布后最近交易日")
    lines.append("")

    # -- 个股表 --
    lines.append("## 📈 个股表现汇总")
    lines.append("")
    header = "| 股票 | 代码 | 提及 | 平均5日 | 5日胜率 | 平均10日 | 10日胜率 | 平均20日 | 20日胜率 | 平均60日 | 60日胜率 | 主要态度 |"
    sep = "|------|------|------|---------|---------|----------|----------|----------|----------|----------|----------|----------|"
    lines.append(header)
    lines.append(sep)

    from collections import Counter

    stock_rows = []
    for (name, ts_code), ss in stock_stats.items():
        if ss["count"] < args.min_mentions:
            continue
        code_short = ts_code.split(".")[0]
        row_data = {"name": name, "code": code_short, "count": ss["count"]}
        for period in ["5d", "10d", "20d", "60d"]:
            rets = ss[f"returns_{period}"]
            if rets:
                row_data[f"avg_{period}"] = np.mean(rets)
                row_data[f"win_{period}"] = sum(1 for r in rets if r > 0) / len(rets) * 100
            else:
                row_data[f"avg_{period}"] = None
                row_data[f"win_{period}"] = None
        row_data["top_sentiment"] = Counter(ss["sentiments"]).most_common(1)[0][0] if ss["sentiments"] else "-"
        stock_rows.append(row_data)

    # 按提及次数排序
    stock_rows.sort(key=lambda x: x["count"], reverse=True)

    for sr in stock_rows:
        def _fmt(v):
            if v is None:
                return "N/A"
            return f"{v:+.1f}%"

        def _win(v):
            if v is None:
                return "N/A"
            return f"{v:.0f}%"

        lines.append(
            f"| {sr['name']} | {sr['code']} | {sr['count']} | "
            f"{_fmt(sr['avg_5d'])} | {_win(sr['win_5d'])} | "
            f"{_fmt(sr['avg_10d'])} | {_win(sr['win_10d'])} | "
            f"{_fmt(sr['avg_20d'])} | {_win(sr['win_20d'])} | "
            f"{_fmt(sr['avg_60d'])} | {_win(sr['win_60d'])} | "
            f"{sr['top_sentiment']} |"
        )

    lines.append("")

    # -- 看好程度维度 --
    lines.append("## 🎯 按看好程度统计")
    lines.append("")
    lines.append("| 看好程度 | 提及次数 | 平均5日 | 5日胜率 | 平均10日 | 10日胜率 | 平均20日 | 20日胜率 | 平均60日 | 60日胜率 |")
    lines.append("|----------|----------|---------|---------|----------|----------|----------|----------|----------|----------|")

    sent_order = ["强烈看好", "看好", "中性", "谨慎", "看空", "未明确"]
    for sent in sent_order:
        ss = sentiment_stats.get(sent)
        if not ss:
            continue
        row = f"| {sent} | {ss['count']} |"
        for period in ["5d", "10d", "20d", "60d"]:
            rets = ss[f"returns_{period}"]
            if rets:
                avg = np.mean(rets)
                win = sum(1 for r in rets if r > 0) / len(rets) * 100
                row += f" {avg:+.1f}% | {win:.0f}% |"
            else:
                row += " N/A | N/A |"
        lines.append(row)

    lines.append("")

    # -- 解读 --
    lines.append("## 💡 关键发现")
    lines.append("")

    # 找表现最好/最差的
    scored_stocks = []
    for sr in stock_rows:
        avg20 = sr.get("avg_20d")
        if avg20 is not None and sr["count"] >= 5:
            scored_stocks.append((avg20, sr))
    scored_stocks.sort(key=lambda x: x[0], reverse=True)

    if scored_stocks:
        lines.append(f"**提及后 20 日表现最佳（提及≥5次）:**")
        for avg, sr in scored_stocks[:5]:
            lines.append(f"- {sr['name']}: 平均 {avg:+.1f}%，胜率 {sr.get('win_20d', 0):.0f}%（提及 {sr['count']} 次）")
        lines.append("")
        lines.append(f"**提及后 20 日表现最差:**")
        for avg, sr in scored_stocks[-5:]:
            lines.append(f"- {sr['name']}: 平均 {avg:+.1f}%，胜率 {sr.get('win_20d', 0):.0f}%（提及 {sr['count']} 次）")

    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 backtest_mentions.py 生成 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    # 写入文件
    prefix = f"user_{args.user_id}_backtest"
    md_path = OUTPUT_DIR / f"{prefix}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown 报告: {md_path}")

    # JSON
    json_path = OUTPUT_DIR / f"{prefix}.json"
    json_output = {
        "generated_at": datetime.now().isoformat(),
        "stock_stats": {
            f"{name}({ts_code})": {
                "count": ss["count"],
                **{f"avg_{p}": round(np.mean(ss[f"returns_{p}"]), 2) if ss[f"returns_{p}"] else None for p in ["5d", "10d", "20d", "60d"]},
                **{f"win_{p}": round(sum(1 for r in ss[f"returns_{p}"] if r > 0) / len(ss[f"returns_{p}"]) * 100, 1) if ss[f"returns_{p}"] else None for p in ["5d", "10d", "20d", "60d"]},
            }
            for (name, ts_code), ss in stock_stats.items()
            if ss["count"] >= args.min_mentions
        },
        "sentiment_stats": {
            sent: {
                "count": ss["count"],
                **{f"avg_{p}": round(np.mean(ss[f"returns_{p}"]), 2) if ss[f"returns_{p}"] else None for p in ["5d", "10d", "20d", "60d"]},
                **{f"win_{p}": round(sum(1 for r in ss[f"returns_{p}"] if r > 0) / len(ss[f"returns_{p}"]) * 100, 1) if ss[f"returns_{p}"] else None for p in ["5d", "10d", "20d", "60d"]},
            }
            for sent, ss in sentiment_stats.items()
        },
    }
    json_path.write_text(json.dumps(json_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON 数据: {json_path}")
    print()
    print("=== 回测完成 ===")


if __name__ == "__main__":
    main()
