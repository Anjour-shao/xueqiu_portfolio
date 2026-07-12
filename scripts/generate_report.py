"""基于数据库中的分析结果生成投资观点报告。

用法:
    cd backend
    python ../scripts/generate_report.py                          # 默认：用户 7845696728
    python ../scripts/generate_report.py --user-id 123456         # 指定用户
    python ../scripts/generate_report.py --start 2025-01-01       # 时间范围
    python ../scripts/generate_report.py --format json            # 仅输出 JSON
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

from sqlalchemy import text

from xueqiu.storage.db import engine, init_db

DATA_DIR = ROOT / "data"


def _query_extractions(user_id: str | None, start: str | None, end: str | None) -> list[dict]:
    """查询提取结果，按帖子时间排序。批量加载避免逐条查询超时。"""
    conditions = ["e.has_info = 1"]
    params: dict = {}

    if user_id:
        conditions.append("p.user_id = :uid")
        params["uid"] = int(user_id)
    if start:
        conditions.append("p.created_at >= :start")
        params["start"] = start
    if end:
        conditions.append("p.created_at <= :end")
        params["end"] = end + " 23:59:59"

    where = " AND ".join(conditions)

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""SELECT e.id, e.post_id, e.summary, e.macro_views, e.model_used, e.extracted_at,
                           p.created_at, p.text, p.reply_count, p.like_count, p.retweet_count
                    FROM xueqiu_extractions e
                    JOIN xueqiu_posts p ON e.post_id = p.post_id
                    WHERE {where}
                    ORDER BY p.created_at ASC"""
            ),
            params,
        ).fetchall()

    # 收集所有 extraction_id，批量查 stocks/sectors
    eids = [row[0] for row in rows]
    stocks_by_eid: dict[int, list[dict]] = {}
    sectors_by_eid: dict[int, list[dict]] = {}

    if eids:
        with engine.begin() as conn:
            # 批量查 stocks
            stocks_rows = conn.execute(
                text("""SELECT extraction_id, stock_name, stock_code, mention_type, raw_text,
                               sentiment, time_horizon, key_logic, confidence
                        FROM extraction_stocks WHERE extraction_id IN :eids"""),
                {"eids": tuple(eids)},
            ).fetchall()
            for s in stocks_rows:
                eid = s[0]
                stocks_by_eid.setdefault(eid, []).append({
                    "stock_name": s[1], "stock_code": s[2], "mention_type": s[3],
                    "raw_text": s[4], "sentiment": s[5], "time_horizon": s[6],
                    "key_logic": s[7], "confidence": s[8],
                })

            # 批量查 sectors
            sectors_rows = conn.execute(
                text("""SELECT extraction_id, sector_name, sentiment, time_horizon, key_logic
                        FROM extraction_sectors WHERE extraction_id IN :eids"""),
                {"eids": tuple(eids)},
            ).fetchall()
            for sec in sectors_rows:
                eid = sec[0]
                sectors_by_eid.setdefault(eid, []).append({
                    "sector_name": sec[1], "sentiment": sec[2],
                    "time_horizon": sec[3], "key_logic": sec[4],
                })

    extractions = []
    for row in rows:
        eid = row[0]
        macro_raw = row[3] or "[]"
        try:
            macro_views = json.loads(macro_raw)
        except json.JSONDecodeError:
            macro_views = []

        extractions.append({
            "extraction_id": eid,
            "post_id": row[1],
            "created_at": row[6],
            "text": row[7],
            "reply_count": row[8],
            "like_count": row[9],
            "retweet_count": row[10],
            "summary": row[2],
            "macro_views": macro_views,
            "model_used": row[4],
            "stocks": stocks_by_eid.get(eid, []),
            "sectors": sectors_by_eid.get(eid, []),
        })

    return extractions


def _query_unconfirmed_nicknames() -> list[dict]:
    """查询未确认的代称。"""
    with engine.begin() as conn:
        rows = conn.execute(
            text("""SELECT nickname, stock_name, stock_code, confidence
                    FROM nickname_map WHERE confirmed = 0
                    ORDER BY confidence DESC""")
        ).fetchall()
    return [{"nickname": r[0], "stock_name": r[1], "stock_code": r[2], "confidence": r[3]} for r in rows]


def _query_author_name(user_id: str) -> str:
    """查询作者昵称。"""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT user_name FROM xueqiu_posts WHERE user_id = :uid LIMIT 1"),
            {"uid": int(user_id)},
        ).fetchone()
    return row[0] if row else f"用户{user_id}"


def _sentiment_stars(sentiment: str) -> str:
    """将看好程度转为星级。"""
    mapping = {
        "强烈看好": "★★★★★",
        "看好": "★★★★☆",
        "中性": "★★★☆☆",
        "谨慎": "★★☆☆☆",
        "看空": "★☆☆☆☆",
    }
    return mapping.get(sentiment, "★★★☆☆")


def _build_stock_summary(extractions: list[dict]) -> list[dict]:
    """聚合个股维度数据。"""
    # key: (stock_name, stock_code)
    stock_map: dict[tuple, dict] = {}

    for ext in extractions:
        for s in ext["stocks"]:
            key = (s["stock_name"], s.get("stock_code") or "")
            if key not in stock_map:
                stock_map[key] = {
                    "name": s["stock_name"],
                    "code": s.get("stock_code") or "",
                    "count": 0,
                    "sentiments": [],
                    "time_horizons": [],
                    "first_mention": ext["created_at"],
                    "last_mention": ext["created_at"],
                    "posts": [],
                    "logics": [],
                }
            sm = stock_map[key]
            sm["count"] += 1
            if s.get("sentiment"):
                sm["sentiments"].append(s["sentiment"])
            if s.get("time_horizon"):
                sm["time_horizons"].append(s["time_horizon"])
            if s.get("key_logic"):
                sm["logics"].append(s["key_logic"])
            if ext["created_at"] < sm["first_mention"]:
                sm["first_mention"] = ext["created_at"]
            if ext["created_at"] > sm["last_mention"]:
                sm["last_mention"] = ext["created_at"]
            # 保留前 3 条代表性帖子
            if len(sm["posts"]) < 3:
                sm["posts"].append({
                    "post_id": ext["post_id"],
                    "created_at": ext["created_at"],
                    "summary": ext["summary"],
                    "text_preview": ext["text"][:200],
                })

    # 计算主流 sentiment
    result = []
    for (name, code), sm in stock_map.items():
        from collections import Counter

        top_sentiment = Counter(sm["sentiments"]).most_common(1)
        top_horizon = Counter(sm["time_horizons"]).most_common(1)
        sm["top_sentiment"] = top_sentiment[0][0] if top_sentiment else "未明确"
        sm["top_horizon"] = top_horizon[0][0] if top_horizon else "未明确"
        sm["stars"] = _sentiment_stars(sm["top_sentiment"])
        result.append(sm)

    # 按提及次数排序
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def _build_sector_summary(extractions: list[dict]) -> list[dict]:
    """聚合板块维度数据。"""
    sector_map: dict[str, dict] = {}

    for ext in extractions:
        for sec in ext["sectors"]:
            name = sec["sector_name"]
            if name not in sector_map:
                sector_map[name] = {
                    "name": name,
                    "count": 0,
                    "sentiments": [],
                    "logics": [],
                }
            sm = sector_map[name]
            sm["count"] += 1
            if sec.get("sentiment"):
                sm["sentiments"].append(sec["sentiment"])
            if sec.get("key_logic"):
                sm["logics"].append(sec["key_logic"])

    from collections import Counter

    result = []
    for name, sm in sector_map.items():
        top_sent = Counter(sm["sentiments"]).most_common(1)
        sm["top_sentiment"] = top_sent[0][0] if top_sent else "未明确"
        sm["stars"] = _sentiment_stars(sm["top_sentiment"])
        result.append(sm)

    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def _generate_markdown(
    author_name: str,
    user_id: str,
    start: str,
    end: str,
    extractions: list[dict],
    stock_summary: list[dict],
    sector_summary: list[dict],
    unconfirmed: list[dict],
) -> str:
    """生成 Markdown 报告。"""
    lines = []
    lines.append(f"# {author_name} 投资观点提取报告")
    lines.append(f"**用户 ID**: {user_id} | **时间范围**: {start} ~ {end}")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # 概览
    total_stocks = len(stock_summary)
    total_sectors = len(sector_summary)
    all_stock_names = set()
    for sm in stock_summary:
        all_stock_names.add(sm["name"])
    lines.append("## 📊 概览")
    lines.append(f"- 含投资信息帖子: {len(extractions)} 条")
    lines.append(f"- 涉及个股: {total_stocks} 只")
    lines.append(f"- 涉及板块: {total_sectors} 个")
    lines.append("")

    # 个股汇总表
    lines.append("## 📈 个股观点汇总")
    lines.append("")
    lines.append("| 股票 | 代码 | 提及次数 | 看好程度 | 时间维度 | 首次提及 | 最后提及 |")
    lines.append("|------|------|----------|----------|----------|----------|----------|")
    for sm in stock_summary:
        lines.append(
            f"| {sm['name']} | {sm['code']} | {sm['count']} | {sm['stars']} | {sm['top_horizon']} | {sm['first_mention'][:10]} | {sm['last_mention'][:10]} |"
        )
    lines.append("")

    # 个股详情
    if stock_summary:
        lines.append("## 🔍 个股详细分析")
        lines.append("")
        for sm in stock_summary:
            # 查代称
            aliases = _find_aliases(sm["name"])
            alias_str = f" — 代称: {', '.join(aliases)}" if aliases else ""
            lines.append(f"### {sm['name']} ({sm['code']}){alias_str}")
            lines.append(f"- **提及次数**: {sm['count']}")
            lines.append(f"- **整体看好度**: {sm['stars']} ({sm['top_sentiment']})")
            lines.append(f"- **时间维度**: {sm['top_horizon']}")
            if sm["logics"]:
                unique_logics = list(dict.fromkeys(sm["logics"]))
                lines.append(f"- **核心逻辑**:")
                for logic in unique_logics[:5]:
                    lines.append(f"  - {logic}")
            lines.append("")
            if sm["posts"]:
                lines.append("**关键帖子**:")
                for p in sm["posts"]:
                    preview = p["text_preview"].replace("\n", " ")[:150]
                    lines.append(f"- [{p['created_at'][:10]}] {p['summary']} (帖子 {p['post_id']})")
                    lines.append(f"  > {preview}...")
                lines.append("")

    # 板块汇总
    if sector_summary:
        lines.append("## 🏭 板块观点汇总")
        lines.append("")
        lines.append("| 板块 | 提及次数 | 整体态度 |")
        lines.append("|------|----------|----------|")
        for sm in sector_summary:
            lines.append(f"| {sm['name']} | {sm['count']} | {sm['stars']} ({sm['top_sentiment']}) |")
        lines.append("")

        # 板块详情
        for sm in sector_summary:
            lines.append(f"### {sm['name']}")
            unique_logics = list(dict.fromkeys(sm['logics']))
            for logic in unique_logics[:3]:
                lines.append(f"- {logic}")
            lines.append("")

    # 待确认代称
    if unconfirmed:
        lines.append("## ⚠ 待确认代称")
        lines.append("")
        lines.append("| 代称 | 推测股票 | 代码 | 置信度 |")
        lines.append("|------|----------|------|--------|")
        for u in unconfirmed:
            lines.append(f"| {u['nickname']} | {u['stock_name']} | {u.get('stock_code', '')} | {u['confidence']:.0%} |")
        lines.append("")
        lines.append("> 请确认后在数据库中标记为 confirmed，然后重新运行分析。")

    # 宏观观点
    lines.append("## 🌐 宏观策略观点")
    lines.append("")
    macro_set = set()
    for ext in extractions:
        for m in ext.get("macro_views", []) or []:
            if m and len(m) > 5:
                macro_set.add(m)
    if macro_set:
        for m in sorted(macro_set):
            lines.append(f"- {m}")
    else:
        lines.append("（未提取到明确的宏观策略观点）")

    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 generate_report.py 自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def _find_aliases(stock_name: str) -> list[str]:
    """查找某股票的所有代称。"""
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT nickname FROM nickname_map WHERE stock_name = :name AND confirmed = 1"),
            {"name": stock_name},
        ).fetchall()
    return [r[0] for r in rows]


def _generate_json(extractions: list[dict], stock_summary: list[dict], sector_summary: list[dict]) -> str:
    """生成 JSON 格式数据。"""
    output = {
        "generated_at": datetime.now().isoformat(),
        "statistics": {
            "total_extractions": len(extractions),
            "total_stocks": len(stock_summary),
            "total_sectors": len(sector_summary),
        },
        "stocks": stock_summary,
        "sectors": sector_summary,
        "extractions": extractions,
    }
    return json.dumps(output, ensure_ascii=False, indent=2, default=str)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="生成投资观点报告")
    parser.add_argument("--user-id", default="7845696728", help="用户 ID")
    parser.add_argument("--start", default="2025-01-01", help="起始日期")
    parser.add_argument("--end", default="2026-01-31", help="截止日期")
    parser.add_argument("--format", choices=["md", "json", "all"], default="all", help="输出格式")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认 data/）")
    args = parser.parse_args()

    user_id = args.user_id.strip()
    start = args.start.strip()
    end = args.end.strip()

    print(f"=== 生成投资观点报告 ===")
    print(f"用户: {user_id} | 时间: {start} ~ {end}")

    init_db()

    # 查询数据
    author_name = _query_author_name(user_id)
    print(f"作者: {author_name}")

    extractions = _query_extractions(user_id, start, end)
    print(f"含投资信息帖子: {len(extractions)} 条")

    if not extractions:
        print("无分析数据。请先运行 fetch_user_period.py 再运行 analyze_posts.py。")
        return

    stock_summary = _build_stock_summary(extractions)
    sector_summary = _build_sector_summary(extractions)
    unconfirmed = _query_unconfirmed_nicknames()

    output_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"user_{user_id}_report"

    # Markdown
    if args.format in ("md", "all"):
        md_path = output_dir / f"{prefix}.md"
        md_content = _generate_markdown(
            author_name, user_id, start, end,
            extractions, stock_summary, sector_summary, unconfirmed,
        )
        md_path.write_text(md_content, encoding="utf-8")
        print(f"Markdown 报告: {md_path}")

    # JSON
    if args.format in ("json", "all"):
        json_path = output_dir / f"{prefix}.json"
        json_content = _generate_json(extractions, stock_summary, sector_summary)
        json_path.write_text(json_content, encoding="utf-8")
        print(f"JSON 数据: {json_path}")

    # 待确认清单
    if unconfirmed:
        todo_path = output_dir / f"user_{user_id}_todo.txt"
        lines = ["# 待确认代称清单", f"# 生成时间: {datetime.now()}", ""]
        lines.append("以下代称 AI 无法确认，请人工判断后更新 nickname_map 表：")
        lines.append("")
        for u in unconfirmed:
            lines.append(f"  代称: {u['nickname']}")
            lines.append(f"  推测: {u['stock_name']} ({u.get('stock_code', '')})")
            lines.append(f"  置信度: {u['confidence']:.0%}")
            lines.append("")
        lines.append("# 确认后执行 SQL：")
        lines.append("# UPDATE nickname_map SET confirmed=1, stock_name='正式名称', stock_code='代码' WHERE nickname='代称';")
        todo_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"待确认清单: {todo_path}")

    print()
    print("=== 报告生成完成 ===")


if __name__ == "__main__":
    main()
