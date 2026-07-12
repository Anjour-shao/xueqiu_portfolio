"""爬取雪球个股讨论区帖子，AI 过滤低质量情绪内容并整合提炼。

用法:
    cd backend
    python ../scripts/fetch_stock_discussion.py                           # 默认 SZ001270，50条
    python ../scripts/fetch_stock_discussion.py --symbol SH600519          # 指定股票
    python ../scripts/fetch_stock_discussion.py --count 100                # 指定数量
    python ../scripts/fetch_stock_discussion.py --output ./report.txt      # 指定输出路径
    python ../scripts/fetch_stock_discussion.py --no-ai                    # 跳过 AI 过滤，直接输出原文
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

from openai import OpenAI

from xueqiu.config import (
    COOKIE_FILE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_PRO_MODEL,
    DEEPSEEK_FLASH_MODEL,
)
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.auth import load_cookie
from xueqiu.integrations.xueqiu.posts import fetch_stock_posts, parse_post, extract_post_list

DEFAULT_SYMBOL = "SZ001270"
DEFAULT_COUNT = 50
OUTPUT_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# AI Prompt
# ---------------------------------------------------------------------------

FILTER_AND_SUMMARIZE_PROMPT = """你是一位专业的A股投资研究分析师。以下是从雪球个股讨论区爬取的最新帖子列表。

## 你的任务
1. **逐条评估**每条帖子的信息价值，分为三类：
   - "高价值"：包含个股/行业分析、基本面/技术面判断、消息解读、投资逻辑等
   - "中等价值"：简单表态但有参考意义（如「坚定持有」「已加仓」等）、简短但有信息量的提问
   - "低价值"：纯情绪输出（发泄/炫耀）、无意义灌水、广告、纯表情、与股票无关的闲聊

2. **过滤**掉所有"低价值"帖子。

3. **整合提炼**高价值和中等价值帖子的核心内容，按主题归类：
   - 看多观点及理由
   - 看空观点及理由
   - 消息面/公告解读
   - 技术面分析
   - 基本面讨论
   - 其他有信息量的讨论

4. **统计**看多/看空/中性情绪占比。

## 输出格式（严格 JSON）
{
  "stock_symbol": "SZ001270",
  "fetch_time": "原始抓取时间",
  "total_fetched": 50,
  "after_filter": 35,
  "filtered_out": 15,
  "filtered_samples": [
    {"index": 1, "reason": "纯情绪发泄", "preview": "又被套了..."}
  ],
  "sentiment_stats": {
    "bullish": 15,
    "bearish": 8,
    "neutral": 12
  },
  "categories": [
    {
      "category": "看多观点及理由",
      "summary": "用一段话概括该类别核心观点",
      "key_posts": [
        {"user": "用户名", "time": "2025-01-01", "text": "帖子原文（保留关键信息）"}
      ]
    }
  ],
  "overall_summary": "用 2-3 段话整体概括当前雪球用户对该股的讨论氛围、主要分歧和共识"
}

## 帖子列表
{posts_text}"""


def _call_llm(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_text: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 8192,
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
        print(f"  AI 调用失败: {exc}")
        return None


def _parse_json_response(raw: str | None) -> dict | None:
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
            return json.loads(raw[start : end + 1])
        except (ValueError, json.JSONDecodeError):
            print(f"  JSON 解析失败，原始回复前 300 字:\n{raw[:300]}")
            return None


def _format_posts_for_prompt(posts) -> str:
    """将帖子列表格式化为适合 AI 处理的文本。"""
    lines = []
    for i, p in enumerate(posts, 1):
        like_info = f" | ♻{p.retweet_count} 💬{p.reply_count} 👍{p.like_count}"
        lines.append(
            f"[{i}] @{p.user_name} | {p.created_at}{like_info}\n"
            f"    {p.text[:400]}"
        )
    return "\n\n".join(lines)


def _safe_print(msg: str) -> None:
    """安全打印，避免 Windows GBK 编码问题。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def _format_output_txt(result: dict, output_path: Path) -> None:
    """将 AI 分析结果格式化为可读的 txt 文件。"""
    lines = []
    lines.append("=" * 70)
    lines.append("  雪球个股讨论区分析报告")
    lines.append("=" * 70)
    lines.append(f"  股票代码: {result.get('stock_symbol', 'N/A')}")
    lines.append(f"  抓取时间: {result.get('fetch_time', 'N/A')}")
    lines.append(f"  原始抓取: {result.get('total_fetched', 0)} 条")
    lines.append(f"  过滤后:   {result.get('after_filter', 0)} 条（剔除 {result.get('filtered_out', 0)} 条低质量内容）")
    lines.append("=" * 70)

    # 情绪统计
    stats = result.get("sentiment_stats", {})
    if stats:
        lines.append("")
        lines.append("[情绪分布]")
        lines.append(f"   看多: {stats.get('bullish', 0)} 条  |  看空: {stats.get('bearish', 0)} 条  |  中性: {stats.get('neutral', 0)} 条")

    # 整体概括
    overall = result.get("overall_summary", "")
    if overall:
        lines.append("")
        lines.append("-" * 70)
        lines.append("[整体概括]")
        lines.append("-" * 70)
        lines.append(overall)

    # 按分类展开
    categories = result.get("categories", [])
    for cat in categories:
        lines.append("")
        lines.append("-" * 70)
        cat_name = cat.get("category", "其他")
        lines.append(f"[{cat_name}]")
        lines.append("-" * 70)
        if cat.get("summary"):
            lines.append(f"  {cat['summary']}")
        lines.append("")
        for j, post in enumerate(cat.get("key_posts", []), 1):
            user = post.get("user", "未知")
            t = post.get("time", "")
            text = post.get("text", "")
            lines.append(f"  [{j}] @{user} ({t})")
            for text_line in text.split("\n"):
                lines.append(f"      {text_line}")
            lines.append("")

    # 被过滤的低质量帖子举例
    samples = result.get("filtered_samples", [])
    if samples:
        lines.append("-" * 70)
        lines.append(f"[被过滤的低质量内容举例]（共 {result.get('filtered_out', 0)} 条）")
        lines.append("-" * 70)
        for s in samples[:10]:
            lines.append(f"  [{s.get('index', '?')}] {s.get('reason', '')}: {s.get('preview', '')}")

    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    output_path.write_text("\n".join(lines), encoding="utf-8")
    _safe_print(f"\n报告已保存至: {output_path}")


def _format_output_simple(posts, output_path: Path, symbol: str) -> None:
    """不使用 AI 时，直接输出帖子原文。"""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  雪球个股讨论区帖子原文 - {symbol}")
    lines.append(f"  抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  共 {len(posts)} 条")
    lines.append("=" * 70)
    lines.append("")

    for i, p in enumerate(posts, 1):
        lines.append(f"[{i}] @{p.user_name} | {p.created_at}")
        lines.append(f"    ♻{p.retweet_count} | 💬{p.reply_count} | 👍{p.like_count}")
        lines.append(f"    {p.text}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    _safe_print(f"\n帖子原文已保存至: {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="爬取雪球个股讨论区帖子并 AI 提炼")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="股票代码（如 SZ001270）")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="获取帖子数量")
    parser.add_argument("--output", default=None, help="输出文件路径（默认 data/{symbol}_discussion.txt）")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 过滤，直接输出原文")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔(秒)")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()
    target_count = args.count
    output_path = Path(args.output) if args.output else (OUTPUT_DIR / f"{symbol}_discussion.txt")

    print(f"=== 雪球个股讨论区爬取 ===")
    print(f"股票代码: {symbol}")
    print(f"目标数量: {target_count} 条")
    print(f"AI 过滤: {'否' if args.no_ai else '是'}")
    print(f"输出路径: {output_path}")
    print()

    # 加载 Cookie
    try:
        cookie = load_cookie()
        print("Cookie 加载成功")
    except RuntimeError as exc:
        print(f"Cookie 加载失败: {exc}")
        return

    # 创建客户端
    client = XueQiuApiClient(cookie=cookie)

    # 计算需要的页数（每页 20 条）
    import random

    pages_needed = (target_count + 19) // 20
    print(f"需要拉取 {pages_needed} 页（每页 20 条）...")

    # 拉取帖子
    all_posts = []
    for page in range(1, pages_needed + 1):
        try:
            from xueqiu.integrations.xueqiu.posts import fetch_stock_posts_page

            posts, has_more = fetch_stock_posts_page(
                client, symbol, page=page, size=20, sort="time"
            )
            all_posts.extend(posts)
            print(f"  第 {page} 页: {len(posts)} 条" + (" (已到底)" if not has_more else ""))

            if not has_more:
                break

            if page < pages_needed:
                time.sleep(random.uniform(args.delay * 0.8, args.delay * 1.2))
        except XueQiuApiError as exc:
            print(f"  第 {page} 页 API 错误: {exc}")
            break

    # 截取需要的数量
    all_posts = all_posts[:target_count]
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n实际获取: {len(all_posts)} 条")

    if not all_posts:
        print("未获取到任何帖子，请检查股票代码或 Cookie 是否有效。")
        return

    if args.no_ai:
        # 直接输出原文
        _format_output_simple(all_posts, output_path, symbol)
        return

    # ---- AI 过滤与提炼 ----
    if not DEEPSEEK_API_KEY:
        print("\n未配置 DEEPSEEK_API_KEY，将直接输出原文。")
        _format_output_simple(all_posts, output_path, symbol)
        return

    print(f"\n正在使用 DeepSeek 模型进行过滤与整合提炼...")
    print(f"  模型: {DEEPSEEK_PRO_MODEL}")

    ai_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    posts_text = _format_posts_for_prompt(all_posts)

    raw_response = _call_llm(
        ai_client,
        DEEPSEEK_PRO_MODEL,
        FILTER_AND_SUMMARIZE_PROMPT,
        posts_text,
        temperature=0.3,
        max_tokens=8192,
    )

    result = _parse_json_response(raw_response)
    if result:
        result["stock_symbol"] = symbol
        result["fetch_time"] = fetch_time
        result["total_fetched"] = len(all_posts)
        if "after_filter" not in result:
            result["after_filter"] = len(all_posts)
            result["filtered_out"] = 0
        _format_output_txt(result, output_path)
    else:
        print("\nAI 分析失败，回退输出原文。")
        _format_output_simple(all_posts, output_path, symbol)


if __name__ == "__main__":
    main()
