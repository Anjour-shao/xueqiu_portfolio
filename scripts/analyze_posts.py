"""AI 分析雪球帖子：提取个股/板块观点，评论智能打分。

用法:
    cd backend
    python ../scripts/analyze_posts.py                        # 分析所有未处理的帖子
    python ../scripts/analyze_posts.py --user-id 7845696728   # 限定用户
    python ../scripts/analyze_posts.py --limit 10             # 仅处理 N 条
    python ../scripts/analyze_posts.py --force                # 强制重新分析已有结果
    python ../scripts/analyze_posts.py --no-comments          # 跳过评论打分
    python ../scripts/analyze_posts.py --screen-batch 15      # 批量筛查条数（提速）
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 修复 Windows 终端中文/特殊字符编码问题
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from openai import OpenAI
from sqlalchemy import text

from xueqiu.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_PRO_MODEL,
    DEEPSEEK_FLASH_MODEL,
)
from xueqiu.storage.db import engine, init_db

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

BATCH_SCREEN_PROMPT = """你是一个投资研究助手。判断以下雪球帖子是否包含投资相关的信息。

投资信息包括但不限于：
- 提到具体个股、板块、行业
- 对市场趋势、资金面的分析
- 对某类资产的看好/看空观点
- 宏观策略判断

非投资信息包括：
- 纯生活碎碎念、简单感叹
- 单纯的粉丝互动/感谢
- 无实质内容的转发

请对每个帖子逐一判断，仅回复一个 JSON 数组：
[{"index": 0, "has_info": true, "reason": "一句话理由"}, {"index": 1, "has_info": false, "reason": "一句话理由"}]"""

EXTRACT_PROMPT = """你是一个专业的A股投资研究分析师。请从以下雪球帖子中提取投资观点。

## 已知代称映射
{nickname_hints}

## 提取要求
1. 识别帖子中提到的所有个股和板块（直接名称、股票代码$...$、或代称）
2. 判断作者对每个标的的看好程度和投资时间维度
3. 提取核心逻辑（为什么看好/看空）
4. 如果有宏观策略观点也一并提取
5. 注意作者可能使用代称指代股票，请根据上方映射表识别，无法确定的标记为"待确认"

## 输出格式（严格 JSON）
{
  "has_info": true,
  "summary": "用一句话概括本帖核心观点",
  "stocks": [
    {
      "name": "股票正式名称",
      "code": "代码如603986",
      "mention_type": "直接提及/代称/暗示",
      "raw_text": "原文中的称呼",
      "sentiment": "强烈看好/看好/中性/谨慎/看空",
      "time_horizon": "短期(1-4周)/中期(1-6月)/长期(6月+)/未明确",
      "key_logic": "看好的核心逻辑摘要",
      "confidence": 0.9
    }
  ],
  "sectors": [
    {
      "name": "板块名称",
      "sentiment": "看好/中性/看空",
      "time_horizon": "短期/中期/长期/未明确",
      "key_logic": "板块逻辑摘要"
    }
  ],
  "macro_views": ["宏观观点1", "宏观观点2"],
  "needs_review": ["无法识别的代称1"]
}

注意：
- 如果帖子没有投资信息，has_info 为 false，其他字段为空
- sentiment 和 time_horizon 从给定选项中选择
- confidence 是 AI 对这笔提取的自信度 0-1
- stocks/sectors/macro_views 可以为空数组"""

COMMENT_SCORE_PROMPT = """你是一个投资研究助手。评估以下雪球评论的信息价值。

有价值的评论：包含股票分析、产业逻辑、财报讨论、技术面判断、行业认知
无价值的评论：纯拍马屁、情绪输出、简单问候、无意义附和

对每条评论打分 0-10，仅回复 JSON 数组：
[{"index": 0, "score": 8, "reason": "分析了香农芯创的HBM业务和业绩预增"}, ...]

评论列表：
{comments_text}"""

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _load_nickname_map() -> dict[str, dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT nickname, stock_name, stock_code, confidence FROM nickname_map WHERE confirmed = 1")
        ).fetchall()
    return {row[0]: {"name": row[1], "code": row[2], "confidence": row[3]} for row in rows}


def _format_nickname_hints(nick_map: dict) -> str:
    if not nick_map:
        return "（暂无已知代称映射）"
    lines = []
    for nick, info in nick_map.items():
        code_str = f"({info['code']})" if info.get("code") else ""
        lines.append(f"- 「{nick}」→ {info['name']}{code_str} (置信度: {info['confidence']:.0%})")
    return "\n".join(lines)


def _call_llm(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_text: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str | None:
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as exc:
        print(f"    LLM 调用失败: {exc}")
        return None


def _parse_json_response(raw: str | None) -> dict | list | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if lines[0].startswith("```") else raw
        if raw.endswith("```"):
            raw = raw[:-3]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            start = raw.index("{") if "{" in raw else raw.index("[")
            end = raw.rindex("}") if "}" in raw else raw.rindex("]")
            return json.loads(raw[start:end + 1])
        except (ValueError, json.JSONDecodeError):
            print(f"    JSON 解析失败，原始回复: {raw[:200]}")
            return None


def _screen_batch(
    client: OpenAI,
    flash_model: str,
    posts: list[dict],
) -> list[dict]:
    """批量筛查：一次 API 调用判断多个帖子是否有投资信息。
    返回 [{"has_info": bool, "reason": str}, ...] 与 posts 一一对应。
    """
    if not posts:
        return []

    # 构建批量文本
    items = []
    for i, p in enumerate(posts):
        # 截断过长帖子到 300 字（筛查不需要全文）
        text = p["text"][:300].replace("\n", " ")
        items.append(f"[{i}] {text}")
    batch_text = "\n\n".join(items)

    raw = _call_llm(client, flash_model, BATCH_SCREEN_PROMPT, batch_text, max_tokens=2048)
    result = _parse_json_response(raw)

    # 构建默认结果
    defaults = [{"has_info": False, "reason": "解析失败"} for _ in posts]

    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "index" in item:
                idx = int(item["index"])
                if 0 <= idx < len(posts):
                    defaults[idx] = {
                        "has_info": bool(item.get("has_info", False)),
                        "reason": str(item.get("reason", "")),
                    }
    return defaults


def _extract_post(
    client: OpenAI,
    pro_model: str,
    post_text: str,
    nick_map: dict,
    comments_context: str = "",
) -> dict | None:
    hints = _format_nickname_hints(nick_map)
    system = EXTRACT_PROMPT.replace("{nickname_hints}", hints)

    user_text = f"## 帖子正文\n{post_text}"
    if comments_context:
        user_text += f"\n\n## 精选评论（来自评论区的高价值讨论）\n{comments_context}"

    raw = _call_llm(client, pro_model, system, user_text, temperature=0.1, max_tokens=4096)
    result = _parse_json_response(raw)
    if isinstance(result, dict):
        return result
    return None


def _score_comments_batch(
    client: OpenAI,
    flash_model: str,
    comments: list[dict],
) -> list[dict]:
    if not comments:
        return []
    items = []
    for i, c in enumerate(comments):
        items.append(f"[{i}] {c['user_name']}: {c['text'][:200]}")
    comments_text = "\n".join(items)

    raw = _call_llm(client, flash_model, COMMENT_SCORE_PROMPT, comments_text, max_tokens=2048)
    result = _parse_json_response(raw)

    if isinstance(result, list):
        scored = []
        for item in result:
            if isinstance(item, dict) and "index" in item and "score" in item:
                idx = int(item["index"])
                if 0 <= idx < len(comments):
                    scored.append({
                        "comment_id": comments[idx]["comment_id"],
                        "score": float(item["score"]),
                        "reason": str(item.get("reason", "")),
                    })
        return scored
    return []


def _save_screen_result(post_id: int, has_info: bool, model: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO xueqiu_extractions (post_id, has_info, model_used, extracted_at)
                   VALUES (:pid, :has_info, :model, NOW())
                   ON DUPLICATE KEY UPDATE has_info=:has_info2, model_used=:model2, extracted_at=NOW()"""
            ),
            {
                "pid": post_id, "has_info": 1 if has_info else 0, "model": model,
                "has_info2": 1 if has_info else 0, "model2": model,
            },
        )


def _save_extraction_result(post_id: int, result: dict, model: str) -> int | None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO xueqiu_extractions (post_id, has_info, summary, macro_views, model_used, extracted_at)
                   VALUES (:pid, 1, :summary, :macro, :model, NOW())
                   ON DUPLICATE KEY UPDATE
                       has_info=1, summary=:summary2, macro_views=:macro2,
                       model_used=:model2, extracted_at=NOW()"""
            ),
            {
                "pid": post_id,
                "summary": result.get("summary", ""),
                "macro": json.dumps(result.get("macro_views", []), ensure_ascii=False),
                "model": model,
                "summary2": result.get("summary", ""),
                "macro2": json.dumps(result.get("macro_views", []), ensure_ascii=False),
                "model2": model,
            },
        )
        row = conn.execute(
            text("SELECT id FROM xueqiu_extractions WHERE post_id = :pid"), {"pid": post_id}
        ).fetchone()
        if not row:
            return None
        extraction_id = row[0]

        conn.execute(text("DELETE FROM extraction_stocks WHERE extraction_id = :eid"), {"eid": extraction_id})
        conn.execute(text("DELETE FROM extraction_sectors WHERE extraction_id = :eid"), {"eid": extraction_id})

        for s in result.get("stocks", []) or []:
            conn.execute(
                text(
                    """INSERT INTO extraction_stocks
                       (extraction_id, stock_name, stock_code, mention_type, raw_text,
                        sentiment, time_horizon, key_logic, confidence)
                       VALUES (:eid, :name, :code, :mtype, :raw, :sent, :th, :logic, :conf)"""
                ),
                {
                    "eid": extraction_id, "name": s.get("name", ""), "code": s.get("code", ""),
                    "mtype": s.get("mention_type", ""), "raw": s.get("raw_text", ""),
                    "sent": s.get("sentiment", ""), "th": s.get("time_horizon", ""),
                    "logic": s.get("key_logic", ""), "conf": float(s.get("confidence", 0.5)),
                },
            )

        for sec in result.get("sectors", []) or []:
            conn.execute(
                text(
                    """INSERT INTO extraction_sectors
                       (extraction_id, sector_name, sentiment, time_horizon, key_logic)
                       VALUES (:eid, :name, :sent, :th, :logic)"""
                ),
                {
                    "eid": extraction_id, "name": sec.get("name", ""),
                    "sent": sec.get("sentiment", ""), "th": sec.get("time_horizon", ""),
                    "logic": sec.get("key_logic", ""),
                },
            )
        return extraction_id


def _save_comment_scores(scores: list[dict]) -> None:
    with engine.begin() as conn:
        for s in scores:
            conn.execute(
                text("UPDATE xueqiu_comments SET info_score = :score WHERE comment_id = :cid"),
                {"score": s["score"], "cid": s["comment_id"]},
            )


def _get_unprocessed_posts(user_id: str | None, limit: int, force: bool) -> list[dict]:
    with engine.begin() as conn:
        if force:
            sql = """SELECT p.post_id, p.text, p.reply_count, p.created_at
                     FROM xueqiu_posts p
                     WHERE (:uid IS NULL OR p.user_id = :uid2)
                     ORDER BY p.created_at DESC"""
        else:
            sql = """SELECT p.post_id, p.text, p.reply_count, p.created_at
                     FROM xueqiu_posts p
                     LEFT JOIN xueqiu_extractions e ON p.post_id = e.post_id
                     WHERE e.post_id IS NULL
                       AND (:uid IS NULL OR p.user_id = :uid2)
                     ORDER BY p.created_at DESC"""
        if limit > 0:
            sql += f" LIMIT {limit}"

        rows = conn.execute(text(sql), {"uid": user_id, "uid2": user_id}).fetchall()

    return [
        {"post_id": row[0], "text": row[1], "reply_count": row[2], "created_at": row[3]}
        for row in rows
    ]


def _get_post_comments(post_id: int, limit: int = 50) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """SELECT comment_id, user_name, text, is_author_reply
                   FROM xueqiu_comments WHERE post_id = :pid
                   ORDER BY is_author_reply DESC, info_score DESC LIMIT :limit"""
            ),
            {"pid": post_id, "limit": limit},
        ).fetchall()
    return [
        {"comment_id": row[0], "user_name": row[1], "text": row[2], "is_author_reply": row[3]}
        for row in rows
    ]


def _get_unscored_comments(post_id: int, limit: int = 100) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """SELECT comment_id, user_name, text, is_author_reply
                   FROM xueqiu_comments WHERE post_id = :pid AND info_score IS NULL
                   LIMIT :limit"""
            ),
            {"pid": post_id, "limit": limit},
        ).fetchall()
    return [
        {"comment_id": row[0], "user_name": row[1], "text": row[2], "is_author_reply": row[3]}
        for row in rows
    ]


def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="AI 分析雪球帖子投资观点")
    parser.add_argument("--user-id", default=None, help="限定用户 ID")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 条（0=全部）")
    parser.add_argument("--force", action="store_true", help="强制重新分析已有结果")
    parser.add_argument("--no-comments", action="store_true", help="跳过评论打分")
    parser.add_argument("--screen-batch", type=int, default=15, help="批量筛查条数")
    parser.add_argument("--delay", type=float, default=0.3, help="批次间延迟(秒)")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("错误: 未配置 DEEPSEEK_API_KEY。请在 backend/.env 中设置。")
        return

    flash_model = DEEPSEEK_FLASH_MODEL
    pro_model = DEEPSEEK_PRO_MODEL
    if not flash_model or not pro_model:
        print("错误: DEEPSEEK_PRO_MODEL 或 DEEPSEEK_FLASH_MODEL 未配置。")
        return

    print(f"=== AI 分析雪球帖子 ===")
    print(f"Flash 模型: {flash_model}")
    print(f"Pro   模型: {pro_model}")
    print(f"批量筛查: {args.screen_batch} 条/批")
    print(f"强制重跑: {'是' if args.force else '否（跳过已分析）'}")
    print(f"评论打分: {'否' if args.no_comments else '是'}")
    print()

    init_db()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    nick_map = _load_nickname_map()
    print(f"代称词典: {len(nick_map)} 条已确认映射")
    print()

    posts = _get_unprocessed_posts(args.user_id, args.limit, args.force)
    total = len(posts)
    print(f"待处理帖子: {total} 条")
    if not posts:
        print("无待处理帖子，退出。")
        return

    total_screened = 0
    total_extracted = 0
    total_skipped = 0
    start_time = time.time()

    batches = list(_chunks(posts, args.screen_batch))
    batch_count = len(batches)

    for bi, batch in enumerate(batches, 1):
        progress_pct = (total_screened / total) * 100 if total > 0 else 0
        first_date = batch[0].get("created_at", "")[:10] if batch else ""
        last_date = batch[-1].get("created_at", "")[:10] if batch else ""

        print(f"\n--- 批次 {bi}/{batch_count} ({len(batch)}条, {first_date} ~ {last_date}, 进度 {progress_pct:.0f}%) ---")

        # ---- Stage 1: 批量初筛 ----
        screen_results = _screen_batch(client, flash_model, batch)
        time.sleep(args.delay)

        has_info_posts = []
        for post, sr in zip(batch, screen_results):
            total_screened += 1
            if sr["has_info"]:
                has_info_posts.append(post)
            else:
                _save_screen_result(post["post_id"], False, flash_model)
                total_skipped += 1

        has_count = len(has_info_posts)
        print(f"  筛查: {has_count}/{len(batch)} 条含投资信息")

        # ---- Stage 2: 逐条深度提取 ----
        for post in has_info_posts:
            pid = post["post_id"]
            text = post["text"]
            created = post.get("created_at", "")

            comments_context = ""
            if not args.no_comments:
                unscored = _get_unscored_comments(pid, limit=20)
                if unscored:
                    scores = _score_comments_batch(client, flash_model, unscored)
                    if scores:
                        _save_comment_scores(scores)
                top_comments = _get_post_comments(pid, limit=10)
                if top_comments:
                    context_lines = []
                    for c in top_comments:
                        tag = "[作者]" if c["is_author_reply"] else ""
                        context_lines.append(f"{tag}{c['user_name']}: {c['text'][:300]}")
                    comments_context = "\n".join(context_lines)

            result = _extract_post(client, pro_model, text, nick_map, comments_context)

            if result and result.get("has_info"):
                _save_extraction_result(pid, result, pro_model)
                total_extracted += 1
                stocks_count = len(result.get("stocks", []) or [])
                sectors_count = len(result.get("sectors", []) or [])
                needs_review = result.get("needs_review", []) or []
                summary = result.get("summary", "")[:60]
                print(f"  [{pid}] {stocks_count}股 {sectors_count}板块 | {summary}")
                if needs_review:
                    print(f"    [待确认] 代称: {', '.join(needs_review)}")
            else:
                _save_screen_result(pid, False, pro_model)
                print(f"  [{pid}] 提取失败（标记为无信息）")

            time.sleep(args.delay)

        # 进度摘要
        elapsed = time.time() - start_time
        rate = total_screened / elapsed if elapsed > 0 else 0
        remaining = (total - total_screened) / rate if rate > 0 else 0
        print(f"  累计: {total_screened}/{total} 筛查, {total_extracted} 提取 | {rate:.1f}条/秒 | 剩余约 {remaining/60:.0f}分钟")

    elapsed = time.time() - start_time
    print()
    print(f"=== 分析完成 ({elapsed/60:.1f}分钟) ===")
    print(f"总帖子: {total}")
    print(f"筛查: {total_screened} 条")
    print(f"跳过(无投资信息): {total_skipped} 条")
    print(f"深度提取: {total_extracted} 条")
    print(f"命中率: {total_extracted}/{total} = {total_extracted/total*100:.1f}%" if total > 0 else "")


if __name__ == "__main__":
    main()
