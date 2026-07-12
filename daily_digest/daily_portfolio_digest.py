"""每日组合 Digest：硬编码关注组合 + 个人持仓，钉钉推送。

设计（中长线 / 每晚 8 点一次）：
- 每晚跑一次，对比 state 里上次已推送的调仓时间；有更新才做 AI + 推送调仓段
- 同一天组合调仓多次：当晚合并为一条 digest，按时间顺序列出各批次
- 推送默认 HTML 渲染为图片（见 digest/ 目录）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 硬编码配置
# ---------------------------------------------------------------------------
WATCH_PORTFOLIOS: list[str] = [
    "ZH3337164",
    "ZH3472193",
    "ZH3558598",
    "ZH3300885",
    "ZH1236871",
    "ZH3393223",
    "ZH3483962",
    "ZH3104761",
    "ZH3437281",
    "ZH3476690",
    "ZH3530915",
    "ZH3546223",
    "ZH3484875",
]

PORTFOLIO_NAMES: dict[str, str] = {
    "ZH3337164": "三年10倍",
    "ZH3472193": "利润断层",
    "ZH3558598": "2026垃圾站",
    "ZH3300885": "AI概念",
    "ZH1236871": "赌出个自由",
    "ZH3393223": "血战到底",
    "ZH3483962": "投资界老萨满",
    "ZH3104761": "景气组合",
    "ZH3437281": "实仓跟踪",
    "ZH3476690": "科技",
    "ZH3530915": "复利中线",
    "ZH3546223": "争取五倍",
    "ZH3484875": "钽坦",
}

# 用户每日发言提炼
DIGEST_USER_ID = "7845696728"

# 评论：拉最新 N 条（约 3 页 × 20），不做「近 3 天」过滤
COMMENT_TARGET_COUNT = 50
COMMENT_PAGE_SIZE = 20
COMMENT_MAX_PAGES = 3

# 回溯调仓历史页数（防同日多批漏报）
REBALANCE_HISTORY_PAGES = 5
# 每晚每组合最多处理几批调仓（同日多批合并时截断）
MAX_BATCHES_PER_PORTFOLIO = 3
# 单次运行最多调用 DeepSeek 次数（防首次未 init-state 爆量）
MAX_AI_CALLS_PER_RUN = 8

# 测试：模拟「今晚 8 点」的时间点；生产环境保持 None
TEST_SIMULATE_NOW: str | None = None

STATE_VERSION = 3

# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent / "backend"
STATE_FILE = Path(os.getenv("DIGEST_STATE_FILE", str(ROOT / "daily_digest_state.json")))

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
load_dotenv(ROOT.parent / ".env")
if not os.getenv("ACCOUNT_DASHBOARD_DATABASE_URL", "").strip():
    os.environ["ACCOUNT_DASHBOARD_DATABASE_URL"] = "sqlite:///:memory:"

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


DIGEST_LOOKBACK_DAYS = _env_int("DIGEST_LOOKBACK_DAYS", 2)

import requests
from openai import OpenAI

from xueqiu.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DINGTALK_KEYWORD,
    DINGTALK_WEBHOOK,
)
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import (
    REBALANCE_HISTORY_URL,
    _fetch_portfolio_name,
    _parse_rebalance_batch,
    fetch_portfolio_rebalance,
    validate_portfolio_id,
)
from xueqiu.integrations.xueqiu.auth import COOKIE_REFRESH_HINT, is_cookie_invalid_text, load_cookie
from xueqiu.integrations.xueqiu.posts import fetch_stock_posts, fetch_user_timeline_page


def _now() -> datetime:
    if TEST_SIMULATE_NOW:
        return datetime.strptime(TEST_SIMULATE_NOW.strip(), "%Y-%m-%d %H:%M:%S")
    return datetime.now()


def _parse_rebalance_dt(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


@dataclass
class RebalanceBatchDigest:
    rebalance_time: str
    records: list[dict[str, Any]]
    ai_summaries: dict[str, str] = field(default_factory=dict)


@dataclass
class PortfolioUpdate:
    portfolio_id: str
    portfolio_name: str
    batches: list[RebalanceBatchDigest]


@dataclass
class UserPostsDigest:
    """用户当日发言提炼结果。"""
    post_count: int
    summary: str  # AI 提炼的核心观点文本
    raw_text: str = ""  # 原始帖子文本（供调试）


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_digest_at": None,
        "portfolios": {},
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _empty_state()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"读取状态文件失败，将使用空状态: {exc}")
        return _empty_state()

    if not isinstance(raw, dict):
        return _empty_state()

    # 兼容旧版本 state
    ver = raw.get("version")
    if ver != STATE_VERSION:
        migrated = _empty_state()
        migrated["last_digest_at"] = raw.get("last_digest_at")
        if isinstance(raw.get("portfolios"), dict):
            migrated["portfolios"] = dict(raw["portfolios"])
        else:
            for key, val in raw.items():
                if str(key).startswith("ZH") and isinstance(val, str):
                    migrated["portfolios"][str(key)] = {"last_notified_rebalance_time": val}
                elif str(key).startswith("ZH") and isinstance(val, dict):
                    migrated["portfolios"][str(key)] = dict(val)
        print(f"已将 state 从 v{ver} 迁移到 v{STATE_VERSION}。")
        return migrated

    state = _empty_state()
    state["last_digest_at"] = raw.get("last_digest_at")
    state["portfolios"] = dict(raw.get("portfolios") or {})
    return state


def save_state(state: dict[str, Any]) -> None:
    state["version"] = STATE_VERSION
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _portfolio_last_notified(state: dict[str, Any], portfolio_id: str) -> str:
    entry = state.get("portfolios", {}).get(portfolio_id) or {}
    return str(entry.get("last_notified_rebalance_time") or "")


def _set_portfolio_last_notified(
    state: dict[str, Any], portfolio_id: str, rebalance_time: str
) -> None:
    state.setdefault("portfolios", {})
    prev = state["portfolios"].get(portfolio_id) or {}
    prev["last_notified_rebalance_time"] = rebalance_time
    state["portfolios"][portfolio_id] = prev


def _deepseek_client() -> OpenAI | None:
    if not DEEPSEEK_API_KEY:
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def clean_xueqiu_comments(raw_comments_list: list[str]) -> list[str]:
    cleaned: list[str] = []
    for text in raw_comments_list:
        text = re.sub(r"\$.*?\$", "", text)
        text = re.sub(r"@\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 10:
            cleaned.append(text)
    return cleaned


def fetch_stock_comments(client: XueQiuApiClient, symbol: str) -> str:
    print(f"      抓取舆情: {symbol}（目标 ≥{COMMENT_TARGET_COUNT} 条）…")
    posts = None
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            posts = fetch_stock_posts(
                client,
                symbol,
                max_pages=COMMENT_MAX_PAGES,
                page_size=COMMENT_PAGE_SIZE,
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = attempt * 2
                print(f"      抓取失败，{wait}s 后重试 ({attempt}/3): {exc}")
                time.sleep(wait)

    if last_exc is not None or posts is None:
        print(f"      股票舆情抓取失败: {last_exc}")
        if isinstance(last_exc, XueQiuApiError) and "HTML" in str(last_exc):
            print(
                "      提示: 讨论区走 api.xueqiu.com；"
                "若仍返回 HTML 多为 WAF/限流，请稍后重试或更新 Cookie。"
            )
        return ""

    comments = [post.text for post in posts if post.text]
    unique_comments = list(dict.fromkeys(comments))
    cleaned_comments = clean_xueqiu_comments(unique_comments)[:COMMENT_TARGET_COUNT]
    lines = [f"{idx}. {text}" for idx, text in enumerate(cleaned_comments, 1)]
    print(f"      {symbol} 获取 {len(cleaned_comments)} 条讨论（原始帖 {len(posts)}）。")
    return "\n".join(lines)


def call_deepseek_summary(stock_name: str, comments: str, *, verbose: bool = False) -> str:
    if not comments or len(comments) < 20:
        return "近期无足够讨论数据或热度较低。"

    client = _deepseek_client()
    if client is None:
        return "未配置 DEEPSEEK_API_KEY，跳过 AI 分析。"

    if verbose:
        key_hint = f"{DEEPSEEK_API_KEY[:8]}…" if len(DEEPSEEK_API_KEY) > 8 else "(空)"
        print(f"      >>> 调用 DeepSeek API（base={DEEPSEEK_BASE_URL}, key={key_hint}）")

    prompt = f"""
你是一个专业的A股量化投研助手。请根据以下雪球用户的近期原生评论，分析当前市场对【{stock_name}】的共识与分歧。

输出要求：
1. 直接从【看多逻辑】、【看空隐患】、【关键事件/基本面追踪】三个小标题开始，不要任何开场白、自我称呼或复述任务。
2. 提取评论中的核心产业逻辑、订单传闻或财报预期；过滤纯情绪宣泄。
3. 每个维度 2-4 条要点，总字数 260-380 字，不要用省略号截断句式。

评论原始数据：
{comments}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是客观严谨的金融分析师。禁止开场白，直接输出三个维度要点。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content
        summary = (content or "").strip()
        if verbose:
            preview = summary.replace("\n", " ")[:120]
            print(f"      <<< DeepSeek 返回 {len(summary)} 字: {preview}…")
        return summary
    except Exception as exc:
        print(f"      DeepSeek 调用失败: {exc}")
        return f"AI 分析生成异常: {exc}"


def fetch_rebalances_since(
    client: XueQiuApiClient,
    portfolio_id: str,
    since_time: str,
    *,
    as_of: datetime | None = None,
    today_only: bool = False,
    lookback_days: int = 2,
) -> list[dict[str, Any]]:
    """拉取 (since_time, as_of] 区间内、晚于上次推送的手动调仓。

    默认不按「仅当天」过滤，而是靠 since_time（last_notified）防历史重复；
    lookback_days 限制最远回溯日历天数，避免 state 异常时一次性补推过多旧批次。
    """
    pid = validate_portfolio_id(portfolio_id)
    as_of_dt = as_of or _now()

    if not since_time:
        return []

    since_dt = _parse_rebalance_dt(since_time)
    portfolio_name = _fetch_portfolio_name(client, pid)

    found: list[dict[str, Any]] = []
    page_size = 20

    for page in range(1, REBALANCE_HISTORY_PAGES + 1):
        data = client.get_json(
            REBALANCE_HISTORY_URL,
            params={"cube_symbol": pid, "page": page, "count": page_size},
        )
        batches = data.get("list") if isinstance(data, dict) else None
        if not isinstance(batches, list) or not batches:
            break

        stop_paging = False
        for batch in batches:
            if not isinstance(batch, dict):
                continue
            crawled = _parse_rebalance_batch(pid, portfolio_name, batch)
            if crawled is None:
                continue
            rt = crawled["rebalance_time"]
            rt_dt = _parse_rebalance_dt(rt)
            if rt_dt <= since_dt:
                stop_paging = True
                continue
            if rt_dt > as_of_dt:
                continue
            if today_only and rt_dt.date() != as_of_dt.date():
                continue
            if lookback_days > 0:
                earliest = as_of_dt.date() - timedelta(days=lookback_days - 1)
                if rt_dt.date() < earliest:
                    continue
            found.append(crawled)

        if stop_paging or len(batches) < page_size:
            break
        if page < REBALANCE_HISTORY_PAGES:
            time.sleep(random.uniform(0.5, 1.0))

    found.sort(key=lambda x: x["rebalance_time"])
    return found


def _truncate_ai_text(text: str, limit: int = 1400) -> str:
    from digest.render import _truncate

    return _truncate(text, limit)


def _portfolio_update_markdown(update: PortfolioUpdate) -> str:
    batch_count = len(update.batches)
    title = f"### 📌 {update.portfolio_name}"
    if batch_count > 1:
        title += f"（{batch_count} 批）"
    lines = [
        title,
        "",
        f"> 组合 `{update.portfolio_id}`",
        "",
    ]

    for batch in update.batches:
        lines.append(f"#### 🕐 {batch.rebalance_time}")
        lines.append("")
        for item in batch.records:
            action = str(item.get("action", ""))
            icon = "【买】" if action == "买入" else "【卖】" if action == "卖出" else "·"
            name = item.get("name", "")
            code = item.get("code", "")
            lines.append(
                f"- {icon} **{action}** {name} `{code}`  \n"
                f"  {item.get('price', '-')} · {item.get('weight_change', '-')}"
            )
            if action == "买入" and code in batch.ai_summaries:
                summary = _truncate_ai_text(batch.ai_summaries[code])
                summary_lines = summary.split("\n")
                quoted = "\n> ".join(summary_lines)
                lines.append(f"\n> **AI 舆情**  \n> {quoted}")
        lines.append("")

    return "\n".join(lines)


def _apply_dingtalk_keyword(text: str) -> str:
    """钉钉自定义关键词只匹配 markdown.text 正文，不匹配 title 字段。"""
    if not DINGTALK_KEYWORD:
        return text
    if DINGTALK_KEYWORD in text:
        return text
    return f"【{DINGTALK_KEYWORD}】\n\n{text}"


def send_dingtalk_markdown(title: str, md_text: str) -> bool:
    if not DINGTALK_WEBHOOK:
        print("未配置 DINGTALK_WEBHOOK，跳过推送。")
        return False

    md_text = _apply_dingtalk_keyword(md_text)
    if DINGTALK_KEYWORD:
        print(f"      钉钉消息已注入关键词: {DINGTALK_KEYWORD}")

    # 钉钉 markdown 正文上限约 20000 字节，过长会被拒收
    max_chars = 18000
    if len(md_text) > max_chars:
        print(f"      钉钉正文过长 ({len(md_text)} 字)，已截断至 {max_chars} 字。")
        md_text = md_text[:max_chars] + "\n\n…(已截断)"

    data = {
        "msgtype": "markdown",
        "markdown": {"title": title[:64], "text": md_text},
    }
    try:
        resp = requests.post(
            DINGTALK_WEBHOOK,
            headers={"Content-Type": "application/json"},
            data=json.dumps(data, ensure_ascii=False),
            timeout=30,
        )
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:300]}

        errcode = body.get("errcode", -1)
        if resp.status_code == 200 and errcode == 0:
            print("钉钉消息推送成功（errcode=0）。")
            return True

        if errcode == 310000:
            print(
                "钉钉关键词不匹配：请在钉钉群机器人「安全设置」查看自定义关键词，"
                "写入 backend/.env 的 DINGTALK_KEYWORD（须与设置完全一致）。"
            )
            plain = re.sub(r"[#>*\[\]()]", "", md_text)
            plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
            if DINGTALK_KEYWORD and DINGTALK_KEYWORD not in plain:
                plain = f"{DINGTALK_KEYWORD}\n\n{plain}"
            text_data = {"msgtype": "text", "text": {"content": plain[:4000]}}
            resp2 = requests.post(
                DINGTALK_WEBHOOK,
                headers={"Content-Type": "application/json"},
                data=json.dumps(text_data, ensure_ascii=False),
                timeout=30,
            )
            try:
                body2 = resp2.json()
            except ValueError:
                body2 = {"raw": resp2.text[:300]}
            if resp2.status_code == 200 and body2.get("errcode") == 0:
                print("钉钉 text 消息推送成功（markdown 因关键词失败后回退）。")
                return True
            print(f"钉钉 text 仍失败: {body2}")

        print(f"钉钉返回异常: HTTP {resp.status_code}, body={body}")
        return False
    except Exception as exc:
        print(f"钉钉推送失败: {exc}")
        return False


def _digest_push_title(updates: list[PortfolioUpdate]) -> str:
    if not updates:
        return "每日组合"
    names = "、".join(u.portfolio_name for u in updates[:2])
    if len(updates) > 2:
        names += f" 等{len(updates)}个"
    return f"组合调仓 · {names}"


def _build_watch_summary(updates: list[PortfolioUpdate]) -> dict[str, Any] | None:
    pids = [p.strip().upper() for p in WATCH_PORTFOLIOS if p.strip()]
    if not pids:
        return None
    updated_ids = {u.portfolio_id for u in updates}
    items = [
        {
            "id": pid,
            "name": PORTFOLIO_NAMES.get(pid, pid),
            "has_update": pid in updated_ids,
        }
        for pid in pids
    ]
    return {
        "count": len(items),
        "new_count": len(updates),
        "portfolios": items,
    }


def fetch_today_user_posts(
    client: XueQiuApiClient,
    user_id: str,
    *,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """逐页抓取用户 timeline，按 created_at 过滤当天帖子。

    雪球 timeline 第一页可能包含置顶帖（日期很旧），不能遇到旧帖就停。
    改为：按页遍历，收集当天帖子；当某页完全没有当天帖子时停止（置顶帖不影响）。
    """
    today = _now().strftime("%Y-%m-%d")
    today_posts: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        try:
            posts, has_more = fetch_user_timeline_page(
                client, user_id, page=page, count=20
            )
        except Exception as exc:
            print(f"      用户帖子第 {page} 页获取失败: {exc}")
            break

        if not posts:
            print(f"      用户帖子第 {page} 页无数据，停止翻页。")
            break

        page_has_today = False
        oldest_date = ""
        for p in posts:
            post_date = (p.created_at or "")[:10]
            if post_date == today:
                page_has_today = True
                today_posts.append({
                    "id": p.id,
                    "text": p.text,
                    "created_at": p.created_at,
                    "like_count": p.like_count,
                    "reply_count": p.reply_count,
                    "retweet_count": p.retweet_count,
                })
            elif post_date and post_date < today:
                if not oldest_date or post_date < oldest_date:
                    oldest_date = post_date

        if page_has_today:
            print(f"      第 {page} 页命中当天帖子（本页累计 {len(today_posts)} 条当天），继续…")
        else:
            # 整页没有当天帖子，说明已翻过今天的内容范围
            if oldest_date:
                print(f"      第 {page} 页无当天帖子（最早 {oldest_date}），停止翻页。")
            else:
                print(f"      第 {page} 页无当天帖子，停止翻页。")
            break

        if not has_more:
            break
        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.0))

    print(f"      用户 {user_id} 今日共 {len(today_posts)} 条帖子。")
    return today_posts


def call_deepseek_summarize_user_posts(posts: list[dict[str, Any]], *, verbose: bool = False) -> str:
    """用 DeepSeek 提炼用户当日发言的核心投资观点。"""
    if not posts:
        return ""

    client = _deepseek_client()
    if client is None:
        return "未配置 DEEPSEEK_API_KEY，跳过用户发言提炼。"

    # 拼接当日帖子文本
    items = []
    for idx, p in enumerate(posts, 1):
        text = (p.get("text") or "").replace("\n", " ")
        if len(text) > 400:
            text = text[:400] + "…"
        items.append(f"[{idx}] {text}")
    posts_text = "\n\n".join(items)

    if verbose:
        key_hint = f"{DEEPSEEK_API_KEY[:8]}…" if len(DEEPSEEK_API_KEY) > 8 else "(空)"
        print(f"      >>> 调用 DeepSeek 提炼用户发言（base={DEEPSEEK_BASE_URL}, key={key_hint}）")

    prompt = f"""
你是一个专业的投资信息提炼助手。以下是某位雪球用户今日的全部发言，请提炼核心内容。

输出要求：
1. 直接从【核心观点】、【关注个股/板块】、【宏观判断】三个小标题开始，不要任何开场白。
2. 提取每一条中涉及的投资判断、产业逻辑、仓位变动暗示；过滤纯情绪发泄或日常寒暄。
3. 每个维度 2-5 条要点，总字数 300-500 字。
4. 观点之间用空行分隔，每条一句话即可。

今日发言原始数据：
{posts_text}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业的投资信息提炼助手。禁止开场白，直接输出三个维度要点。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content
        summary = (content or "").strip()
        if verbose:
            preview = summary.replace("\n", " ")[:120]
            print(f"      <<< DeepSeek 返回 {len(summary)} 字: {preview}…")
        return summary
    except Exception as exc:
        print(f"      用户发言提炼失败: {exc}")
        return f"AI 提炼异常: {exc}"


def _probe_xueqiu_cookie() -> dict[str, Any]:
    """启动时探测 Cookie 是否仍可拉调仓 API。"""
    try:
        cookie = load_cookie()
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    probe_pid = (WATCH_PORTFOLIOS[0] if WATCH_PORTFOLIOS else "ZH3337164").strip().upper()
    try:
        client = XueQiuApiClient()
        fetch_portfolio_rebalance(probe_pid, client=client)
        return {"ok": True, "message": ""}
    except Exception as exc:
        if is_cookie_invalid_text(str(exc)):
            return {"ok": False, "message": str(exc)}
        print(f"Cookie 探测出现非登录类错误（忽略）: {exc}")
        return {"ok": True, "message": ""}


def _cookie_alert_payload(message: str) -> dict[str, str]:
    return {
        "title": "雪球 Cookie 已失效",
        "message": message.strip(),
        "hint": COOKIE_REFRESH_HINT,
    }


def send_dingtalk_digest(
    *,
    run_time: str,
    updates: list[PortfolioUpdate] | None = None,
    force_markdown: bool = False,
    cookie_alert: dict[str, str] | None = None,
    user_posts: UserPostsDigest | None = None,
) -> None:
    """推送简报：默认 HTML 渲染为图片；失败时回退 Markdown。"""
    from digest import render as digest_render

    updates = updates or []
    watch_summary = _build_watch_summary(updates)
    title = _digest_push_title(updates)
    if cookie_alert and not updates:
        title = "Cookie 过期 · 请更新"
    if user_posts and user_posts.post_count > 0:
        title += " · 用户观点"
    simulate_note = f"模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    push_mode = digest_render.DIGEST_PUSH_MODE or "image"

    if not force_markdown and push_mode in ("image", "both"):
        try:
            ok, local_path = digest_render.push_digest_image(
                run_time=run_time,
                simulate_note=simulate_note,
                updates=updates,
                watch_summary=watch_summary,
                cookie_alert=cookie_alert,
                user_posts=user_posts,
                title=title,
            )
            if ok and push_mode == "image":
                return
            if ok and push_mode == "both":
                print("      图片已推送，继续发送 Markdown 摘要…")
            elif local_path:
                print(f"      图床未配置或上传失败，已生成本地预览: {local_path}")
                print("      建议在 .env 配置 OSS 或 IMG_BB_API_KEY 后重试（见 daily_digest/README.md）")
        except Exception as exc:
            print(f"      图片简报失败，回退 Markdown: {exc}")

    mode = f" · 模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    md_parts = [
        f"## 📊 每日组合简报{mode}",
        "",
        f"> {run_time}",
        "",
    ]
    if cookie_alert:
        md_parts.extend(
            [
                "### ⚠️ Cookie 已失效",
                "",
                cookie_alert.get("message", ""),
                "",
                cookie_alert.get("hint", COOKIE_REFRESH_HINT),
                "",
                "---",
                "",
            ]
        )
    if updates:
        md_parts.append("### 🔔 组合调仓")
        md_parts.append("")
        for upd in updates:
            md_parts.append(_portfolio_update_markdown(upd).rstrip())
            md_parts.append("")
    elif watch_summary:
        md_parts.append("### 🔔 组合调仓")
        md_parts.append("")
        if watch_summary["new_count"] == 0:
            md_parts.append(
                f"今晚无新调仓（已巡检 {watch_summary['count']} 个关注组合）。"
            )
        md_parts.append("")
    # 用户发言提炼
    if user_posts and user_posts.summary:
        md_parts.append("---")
        md_parts.append("")
        md_parts.append("### 💬 用户观点提炼")
        md_parts.append("")
        md_parts.append(f"> 今日 {user_posts.post_count} 条发言 · AI 核心提炼")
        md_parts.append("")
        md_parts.append(user_posts.summary)
        md_parts.append("")
    if not updates and not (watch_summary and watch_summary["new_count"] == 0) and not user_posts:
        md_parts.append("今晚无新调仓。")

    send_dingtalk_markdown(title, "\n".join(md_parts).rstrip() + "\n")


def init_portfolio_state_only() -> None:
    """仅把各组合「当前最新调仓时间」写入 state，不推送、不调 AI（首次上 GHA 前建议跑一次）。"""
    state = load_state()
    client = XueQiuApiClient()
    for pid in [p.strip().upper() for p in WATCH_PORTFOLIOS if p.strip()]:
        try:
            crawled = fetch_portfolio_rebalance(pid, client=client)
            rt = crawled["rebalance_time"]
            _set_portfolio_last_notified(state, pid, rt)
            label = PORTFOLIO_NAMES.get(pid, pid)
            print(f"[{pid}] {label} -> {rt}")
        except Exception as exc:
            print(f"[{pid}] 失败: {exc}")
        time.sleep(random.uniform(0.8, 1.5))
    save_state(state)
    print("state 已同步至最新调仓，后续每晚只推送新变化。")


def check_portfolio_for_nightly_digest(
    client: XueQiuApiClient,
    portfolio_id: str,
    state: dict[str, Any],
    *,
    ai_budget: list[int],
) -> PortfolioUpdate | None:
    pid = portfolio_id.strip().upper()
    last_notified = _portfolio_last_notified(state, pid)
    as_of = _now()

    print(f"\n[{pid}] 晚间巡检（自 {last_notified or '从未推送'} 至 {as_of.strftime('%Y-%m-%d %H:%M')}）…")

    if not last_notified:
        try:
            latest = fetch_portfolio_rebalance(pid, client=client)
            rt = str(latest.get("rebalance_time") or "")
            if rt:
                _set_portfolio_last_notified(state, pid, rt)
                print(f"[{pid}] 首次巡检：已同步基准 {rt}，不推送历史调仓。")
        except Exception as exc:
            print(f"[{pid}] 首次同步基准失败: {exc}")
        return None

    try:
        new_batches_raw = fetch_rebalances_since(
            client, pid, last_notified, as_of=as_of, lookback_days=DIGEST_LOOKBACK_DAYS
        )
    except Exception as exc:
        print(f"[{pid}] 获取调仓历史失败: {exc}")
        if is_cookie_invalid_text(str(exc)):
            raise
        return None

    if not new_batches_raw:
        print(f"[{pid}] 该时段无新调仓。")
        return None

    if len(new_batches_raw) > MAX_BATCHES_PER_PORTFOLIO:
        trimmed = new_batches_raw[-MAX_BATCHES_PER_PORTFOLIO:]
        print(
            f"[{pid}] 调仓批次数 {len(new_batches_raw)} 过多，"
            f"仅处理最近 {MAX_BATCHES_PER_PORTFOLIO} 批"
        )
        new_batches_raw = trimmed

    portfolio_name = PORTFOLIO_NAMES.get(pid) or new_batches_raw[-1].get("portfolio_name") or pid
    times = [b["rebalance_time"] for b in new_batches_raw]
    print(
        f"[{pid}] 待推送 {len(new_batches_raw)} 批调仓: "
        f"{times[0]}" + (f" … {times[-1]}" if len(times) > 1 else "")
    )

    buy_codes_seen: set[str] = set()
    digest_batches: list[RebalanceBatchDigest] = []

    for crawled in new_batches_raw:
        records = crawled.get("records") or []
        ai_summaries: dict[str, str] = {}
        for record in records:
            if record.get("action") != "买入":
                continue
            code = record.get("code", "")
            if not code or code in buy_codes_seen:
                continue
            buy_codes_seen.add(code)
            stock_name = record.get("name", code)
            if ai_budget[0] <= 0:
                ai_summaries[code] = "（本 run 已达 DeepSeek 调用上限，已跳过）"
                continue
            print(f"      分析买入: {stock_name} ({code})")
            comments = fetch_stock_comments(client, code)
            ai_budget[0] -= 1
            ai_summaries[code] = call_deepseek_summary(
                str(stock_name), comments, verbose=True
            )

        digest_batches.append(
            RebalanceBatchDigest(
                rebalance_time=crawled["rebalance_time"],
                records=records,
                ai_summaries=ai_summaries,
            )
        )

    _set_portfolio_last_notified(state, pid, new_batches_raw[-1]["rebalance_time"])

    return PortfolioUpdate(
        portfolio_id=pid,
        portfolio_name=portfolio_name,
        batches=digest_batches,
    )


def main(*, skip_portfolios: bool = False, force_markdown: bool = False) -> None:
    run_time = _now().strftime("%Y-%m-%d %H:%M")
    sim_note = f" [模拟时间，TEST_SIMULATE_NOW={TEST_SIMULATE_NOW}]" if TEST_SIMULATE_NOW else ""
    print(f"=== 每日组合 Digest ({run_time}){sim_note} ===")
    print(f"调仓回溯天数 DIGEST_LOOKBACK_DAYS={DIGEST_LOOKBACK_DAYS}")

    base_url = (DEEPSEEK_BASE_URL or "https://api.deepseek.com").strip()
    print(f"DeepSeek: {base_url}")

    if not WATCH_PORTFOLIOS:
        print("请在脚本顶部配置 WATCH_PORTFOLIOS。")
        return

    state = load_state()
    updates: list[PortfolioUpdate] = []
    ai_budget = [MAX_AI_CALLS_PER_RUN]
    cookie_alert: dict[str, str] | None = None
    user_posts: UserPostsDigest | None = None

    cookie_probe = _probe_xueqiu_cookie()
    if not cookie_probe.get("ok"):
        cookie_alert = _cookie_alert_payload(str(cookie_probe.get("message") or "Cookie 无效"))
        print(f"Cookie 探测失败: {cookie_probe.get('message')}")

    try:
        if not skip_portfolios and WATCH_PORTFOLIOS and not cookie_alert:
            client = XueQiuApiClient()
            portfolios = [p.strip().upper() for p in WATCH_PORTFOLIOS if p.strip()]
            total = len(portfolios)
            for index, pid in enumerate(portfolios, 1):
                try:
                    upd = check_portfolio_for_nightly_digest(
                        client, pid, state, ai_budget=ai_budget
                    )
                except Exception as exc:
                    if is_cookie_invalid_text(str(exc)):
                        cookie_alert = _cookie_alert_payload(str(exc))
                        print(f"巡检中断（Cookie 失效）: {exc}")
                        break
                    raise
                if upd is not None:
                    updates.append(upd)
                if index < total:
                    time.sleep(random.uniform(1.0, 2.0))
            if ai_budget[0] < MAX_AI_CALLS_PER_RUN:
                used = MAX_AI_CALLS_PER_RUN - ai_budget[0]
                print(f"\n本 run 已调用 DeepSeek {used} 次（上限 {MAX_AI_CALLS_PER_RUN}）。")

            # 用户每日发言提炼（复用同一个 client）
            if DIGEST_USER_ID:
                print(f"\n抓取用户 {DIGEST_USER_ID} 今日发言…")
                today_posts = fetch_today_user_posts(client, DIGEST_USER_ID)
                print(f"  用户 {DIGEST_USER_ID} 今日 {len(today_posts)} 条帖子。")
                if today_posts:
                    summary = call_deepseek_summarize_user_posts(today_posts, verbose=True)
                    user_posts = UserPostsDigest(
                        post_count=len(today_posts),
                        summary=summary,
                    )
                    print(f"  用户发言 AI 提炼完成。")
            else:
                print("未配置 DIGEST_USER_ID，跳过用户发言提炼。")

        elif skip_portfolios:
            print("已跳过组合调仓巡检（--skip-portfolios）。")
        elif cookie_alert:
            print("Cookie 无效，已跳过组合调仓巡检。")

        state["last_digest_at"] = run_time

        should_send = bool(updates) or bool(cookie_alert) or (user_posts and user_posts.post_count > 0)
        if should_send:
            send_dingtalk_digest(
                run_time=run_time,
                updates=updates,
                force_markdown=force_markdown,
                cookie_alert=cookie_alert,
                user_posts=user_posts,
            )
        else:
            print("关注组合无新调仓，今日无用户发言。")
    finally:
        try:
            save_state(state)
        except OSError as exc:
            print(f"保存 state 失败: {exc}")

    print("=== 执行完毕 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日组合 Digest")
    parser.add_argument(
        "--init-state",
        action="store_true",
        help="仅同步各组合最新调仓时间到 state，不推送",
    )
    parser.add_argument(
        "--push-markdown",
        action="store_true",
        help="强制 Markdown 文本推送（默认 HTML 渲染为图片）",
    )
    args = parser.parse_args()
    if args.init_state:
        init_portfolio_state_only()
    else:
        main(skip_portfolios=False, force_markdown=args.push_markdown)
