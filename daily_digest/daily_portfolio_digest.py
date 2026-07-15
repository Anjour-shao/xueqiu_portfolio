"""每日组合 Digest + 铖昌科技价格监控，钉钉推送。

设计（中长线 / 每晚 8 点一次）：
- 每晚跑一次，对比 state 里上次已推送的调仓时间；有更新才做 AI + 推送调仓段
- 同一天组合调仓多次：当晚合并为一条 digest，按时间顺序列出各批次
- 铖昌科技(001270) 价格监控：<=105 / <=96 触发加仓提醒
- 有事件（调仓/价格触发）→ 渲染图片推送；无事件 → 文字心跳保活
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
    "ZH3337164",  # 三年10倍
    "ZH3393223",  # 血战到底
    "ZH3558598",  # 2026垃圾站
]

PORTFOLIO_NAMES: dict[str, str] = {
    "ZH3337164": "三年10倍",
    "ZH3393223": "血战到底",
    "ZH3558598": "2026垃圾站",
}

# ---- 价格监控（多股票，双向触发） ----
# direction: "below" = 跌到目标价触发（加仓）; "above" = 涨到目标价触发（止盈）
PRICE_ALERT_STOCKS: list[dict] = [
    {
        "code": "SZ001270",
        "name": "铖昌科技",
        "sina_symbol": "sz001270",
        "holdings": "2手 成本~160",
        "targets": [
            (105.0, "加仓第1批 (1手 @105)", "below"),
            (96.0,  "加仓第2批 (2手 @96)",  "below"),
        ],
    },
    {
        "code": "SZ003043",
        "name": "华亚智能",
        "sina_symbol": "sz003043",
        "holdings": "2手 浮盈+25%",
        "targets": [
            (95.0,  "止盈第1档 (卖1手 @95)",  "above"),
            (105.0, "止盈第2档 (卖1手 @105)", "above"),
            (72.0,  "保利止损 (卖1手 @72)",   "below"),
            (65.0,  "清仓线 (全清 @65)",       "below"),
        ],
    },
]

# 评论：拉最新 N 条（约 3 页 x 20）
COMMENT_TARGET_COUNT = 50
COMMENT_PAGE_SIZE = 20
COMMENT_MAX_PAGES = 3

# 回溯调仓历史页数（防同日多批漏报）
REBALANCE_HISTORY_PAGES = 5
# 每晚每组合最多处理几批调仓
MAX_BATCHES_PER_PORTFOLIO = 3
# 单次运行最多调用 DeepSeek 次数
MAX_AI_CALLS_PER_RUN = 8

# 测试：模拟「今晚 8 点」的时间点；生产环境保持 None
TEST_SIMULATE_NOW: str | None = None

STATE_VERSION = 4

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
from xueqiu.integrations.xueqiu.posts import fetch_stock_posts


def _now() -> datetime:
    if TEST_SIMULATE_NOW:
        return datetime.strptime(TEST_SIMULATE_NOW.strip(), "%Y-%m-%d %H:%M:%S")
    return datetime.now()


def _parse_rebalance_dt(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


# ===================================================================
# Data classes
# ===================================================================


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
class PriceAlert:
    """多股票价格触发提醒"""
    triggered: bool  # 是否有新触发
    stocks: list[dict]  # [{"name":..., "code":..., "current_price":..., "targets_hit":[...]}]


@dataclass
class PriceTargetResult:
    target: float
    label: str
    direction: str  # "above" / "below"
    hit: bool
    already_triggered: bool
    triggered_now: bool


# ===================================================================
# State
# ===================================================================


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_digest_at": None,
        "portfolios": {},
        "price_alerts": {"triggered": {}},
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

    # 迁移旧版本 state
    ver = raw.get("version", 0)
    if ver < STATE_VERSION:
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
        # 迁移 price_alerts
        if isinstance(raw.get("price_alerts"), dict):
            migrated["price_alerts"] = dict(raw["price_alerts"])
        print(f"已将 state 从 v{ver} 迁移到 v{STATE_VERSION}。")
        return migrated

    state = _empty_state()
    state["last_digest_at"] = raw.get("last_digest_at")
    state["portfolios"] = dict(raw.get("portfolios") or {})
    if isinstance(raw.get("price_alerts"), dict):
        state["price_alerts"] = dict(raw["price_alerts"])
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


def _price_alert_already_triggered(state: dict[str, Any], target_key: str) -> bool:
    return bool(state.get("price_alerts", {}).get("triggered", {}).get(target_key))


def _mark_price_alert_triggered(state: dict[str, Any], target_key: str) -> None:
    state.setdefault("price_alerts", {}).setdefault("triggered", {})
    state["price_alerts"]["triggered"][target_key] = _now().strftime("%Y-%m-%d %H:%M:%S")


# ===================================================================
# 价格抓取
# ===================================================================

_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def fetch_sina_spot(sina_symbol: str) -> float | None:
    """从新浪实时行情获取现价。"""
    url = f"https://hq.sinajs.cn/list={sina_symbol}"
    try:
        resp = requests.get(url, headers=_SINA_HEADERS, timeout=15)
        resp.encoding = "gbk"
        text = resp.text
        eq = text.find("=")
        if eq < 0:
            return None
        payload = text[eq + 1:].strip().strip('";')
        parts = payload.split(",")
        if len(parts) < 4:
            return None
        price = float(parts[3])
        if price <= 0:
            return None
        return price
    except Exception as e:
        print(f"获取行情失败: {e}")
        return None


def check_price_alerts(state: dict[str, Any]) -> PriceAlert:
    """检查所有监控股票的价格触发。"""
    stock_results = []
    any_triggered = False

    for cfg in PRICE_ALERT_STOCKS:
        price = fetch_sina_spot(cfg["sina_symbol"])
        if price is None:
            stock_results.append({
                "name": cfg["name"],
                "code": cfg["code"],
                "holdings": cfg.get("holdings", ""),
                "current_price": None,
                "error": f"无法获取行情",
                "targets_hit": [],
            })
            continue

        targets_hit = []
        for target, label, direction in cfg["targets"]:
            target_key = f"{cfg['code']}_{target}_{direction}"
            already = _price_alert_already_triggered(state, target_key)

            if direction == "below":
                hit_now = price <= target
            else:  # "above"
                hit_now = price >= target

            is_new = hit_now and not already
            targets_hit.append({
                "target": target,
                "label": label,
                "direction": direction,
                "hit": hit_now,
                "already_triggered": already,
                "triggered_now": is_new,
            })
            if is_new:
                any_triggered = True
                _mark_price_alert_triggered(state, target_key)

        stock_results.append({
            "name": cfg["name"],
            "code": cfg["code"],
            "holdings": cfg.get("holdings", ""),
            "current_price": price,
            "error": "",
            "targets_hit": targets_hit,
        })

    return PriceAlert(triggered=any_triggered, stocks=stock_results)


# ===================================================================
# DeepSeek
# ===================================================================


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
    print(f"      抓取舆情: {symbol}（目标 >={COMMENT_TARGET_COUNT} 条）…")
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


# ===================================================================
# 组合调仓
# ===================================================================


def fetch_rebalances_since(
    client: XueQiuApiClient,
    portfolio_id: str,
    since_time: str,
    *,
    as_of: datetime | None = None,
    today_only: bool = False,
    lookback_days: int = 2,
) -> list[dict[str, Any]]:
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


# ===================================================================
# Cookie
# ===================================================================


def _probe_xueqiu_cookie() -> dict[str, Any]:
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


# ===================================================================
# 推送
# ===================================================================


def send_dingtalk_digest(
    *,
    run_time: str,
    updates: list[PortfolioUpdate] | None = None,
    force_markdown: bool = False,
    cookie_alert: dict[str, str] | None = None,
    price_alert: PriceAlert | None = None,
) -> None:
    """推送简报：有事件→图片；无事件→文字心跳。"""
    from digest import render as digest_render

    updates = updates or []
    watch_summary = _build_watch_summary(updates)
    title = _digest_push_title(updates)
    if cookie_alert and not updates:
        title = "Cookie 过期 · 请更新"
    if price_alert and price_alert.triggered:
        title += " · 加仓提醒"
    simulate_note = f"模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    push_mode = digest_render.DIGEST_PUSH_MODE or "image"

    has_event = bool(updates) or (price_alert and price_alert.triggered) or bool(cookie_alert)

    if has_event and not force_markdown and push_mode in ("image", "both"):
        try:
            ok, local_path = digest_render.push_digest_image(
                run_time=run_time,
                simulate_note=simulate_note,
                updates=updates,
                watch_summary=watch_summary,
                cookie_alert=cookie_alert,
                price_alert=price_alert,
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

    # Markdown 推送（回退 或 心跳）
    mode = f" · 模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    md_parts = [
        f"## 📊 每日组合简报{mode}",
        "",
        f"> {run_time}",
        "",
    ]

    if cookie_alert:
        md_parts.extend([
            "### ⚠️ Cookie 已失效",
            "",
            cookie_alert.get("message", ""),
            "",
            cookie_alert.get("hint", COOKIE_REFRESH_HINT),
            "",
            "---",
            "",
        ])

    # 价格提醒
    if price_alert:
        md_parts.append("### 💰 价格监控")
        md_parts.append("")
        for s in price_alert.stocks:
            price_str = f"{s['current_price']:.2f}" if s["current_price"] else "获取失败"
            md_parts.append(f"**{s['name']}** `{s['code']}` 现价: **{price_str}**")
            md_parts.append("")
            md_parts.append("| 目标价 | 方向 | 操作 | 状态 |")
            md_parts.append("|--------|------|------|------|")
            for t in s["targets_hit"]:
                dir_label = "📈 止盈" if t["direction"] == "above" else "📉 买入/止损"
                if t["triggered_now"]:
                    status = "🔔 **刚触发！**"
                elif t["hit"]:
                    status = "⚠ 已触发"
                else:
                    diff = t["target"] - s["current_price"] if s["current_price"] else 0
                    status = f"差 {diff:+.2f}"
                md_parts.append(f"| {t['target']:.1f} | {dir_label} | {t['label']} | {status} |")
            md_parts.append("")

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
            p_names = "、".join(p["name"] for p in watch_summary["portfolios"])
            md_parts.append(f"今晚无新调仓（已巡检 {watch_summary['count']} 个组合: {p_names}）。")
        md_parts.append("")

    if not updates and not price_alert.triggered and not cookie_alert:
        # 纯心跳
        md_parts.append("---")
        md_parts.append("")
        md_parts.append("✅ 系统运行正常")
        for s in price_alert.stocks:
            price_str = f"{s['current_price']:.2f}" if s["current_price"] else "获取失败"
            md_parts.append(f"📌 {s['name']} 现价 {price_str}，目标点位均未触发")
        p_names = "、".join(p["name"] for p in (watch_summary["portfolios"] if watch_summary else []))
        md_parts.append(f"📋 关注组合（{p_names}）无新调仓")
        md_parts.append("")
        md_parts.append(f"> 心跳时间: {run_time}")

    send_dingtalk_markdown(title, "\n".join(md_parts).rstrip() + "\n")


# ===================================================================
# 组合巡检
# ===================================================================


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


def _run_stock_query(keyword: str) -> None:
    """单独查询某只股票的雪球讨论区 AI 分析，推送到钉钉。"""
    kw = keyword.strip()
    print(f"开始分析股票: {kw}")

    from xueqiu.domain.codes import to_xueqiu_code
    from xueqiu.integrations.xueqiu.client import XueQiuApiClient

    # ---- 解析代码 ----
    digits = "".join(c for c in kw if c.isdigit())
    xq_code = ""
    stock_name = kw
    company_info: dict[str, str] = {}

    # 数字代码：支持 4-6 位，自动补全 + 推断市场
    if 4 <= len(digits) <= 6:
        digits = digits.zfill(6)  # 1270 -> 001270
        if digits.startswith(("5", "6", "9")):
            xq_code = f"SH{digits}"
        else:
            xq_code = f"SZ{digits}"

    # 通过 ashare_system 数据库查名称/行业
    try:
        from sqlalchemy import create_engine, text as sa_text
        from xueqiu.config import DATABASE_URL

        ashare_url = DATABASE_URL.replace("/portfolio?", "/ashare_system?")
        ashare_engine = create_engine(ashare_url, pool_pre_ping=True)
        with ashare_engine.connect() as conn:
            if xq_code:
                raw_code = xq_code[2:]
                row = conn.execute(
                    sa_text(
                        "SELECT ts_code, name, industry, area FROM stock_basic "
                        "WHERE symbol = :sym OR ts_code = :ts LIMIT 1"
                    ),
                    {"sym": raw_code, "ts": f"{raw_code}.{xq_code[:2]}"},
                ).fetchone()
                if row:
                    stock_name = str(row[1])
                    company_info = {"industry": str(row[2] or ""), "area": str(row[3] or "")}
            else:
                # 模糊名称匹配：%铖昌% 能匹配到铖昌科技
                rows = conn.execute(
                    sa_text(
                        "SELECT ts_code, name, industry, area FROM stock_basic "
                        "WHERE name LIKE :kw ORDER BY "
                        "CASE WHEN name = :exact THEN 0 ELSE 1 END, ts_code LIMIT 5"
                    ),
                    {"kw": f"%{kw}%", "exact": kw},
                ).fetchall()
                if rows:
                    row = rows[0]
                    xq_code = to_xueqiu_code(str(row[0]))
                    stock_name = str(row[1])
                    company_info = {"industry": str(row[2] or ""), "area": str(row[3] or "")}
    except Exception as exc:
        print(f"数据库查询失败: {exc}")

    if not xq_code:
        title = f"查询失败 · {kw}"
        md_text = f"❌ 未找到匹配的股票: **{kw}**\n\n请尝试输入 6 位代码，如 001270"
        send_dingtalk_markdown(title, md_text)
        return

    print(f"解析结果: {stock_name} ({xq_code})")

    # ---- 爬取讨论区 ----
    print("正在爬取雪球讨论区...")
    client = XueQiuApiClient()
    all_posts = []
    max_pages = 8  # 单股查询翻更多页
    from xueqiu.integrations.xueqiu.posts import fetch_stock_posts_page

    for page in range(1, max_pages + 1):
        try:
            posts, has_more = fetch_stock_posts_page(client, xq_code, page=page, size=20, sort="time")
            all_posts.extend(posts)
            print(f"  第 {page} 页: {len(posts)} 条 {'(已到底)' if not has_more else ''}")
            if not has_more:
                break
        except Exception as exc:
            print(f"  第 {page} 页获取失败: {exc}")
            break

    if not all_posts:
        title = f"无数据 · {stock_name}"
        md_text = f"⚠ {stock_name}({xq_code}) 讨论区暂无近期帖子。"
        send_dingtalk_markdown(title, md_text)
        return

    print(f"共获取 {len(all_posts)} 条帖子")

    # ---- 清洗评论 ----
    comments = [p.text for p in all_posts if p.text]
    unique = list(dict.fromkeys(comments))
    cleaned = clean_xueqiu_comments(unique)
    comment_text = "\n".join(f"{i}. {t}" for i, t in enumerate(cleaned[:100], 1))
    print(f"清洗: {len(comments)} -> {len(unique)} -> {len(cleaned)} 条")

    # ---- AI 分析 ----
    print("正在调用 DeepSeek 深度分析...")
    company_section = ""
    if company_info:
        parts = [f"行业: {company_info.get('industry', '未知')}"]
        if company_info.get("area"):
            parts.append(f"地区: {company_info['area']}")
        company_section = "\n".join(parts)

    prompt = f"""你是资深A股行业研究员。请基于雪球用户讨论，对{stock_name}({xq_code})做一份深度分析报告。

{company_section}

**输出格式（1200-1800字，内容要充实）：**

### {stock_name} 深度分析

**公司定位与商业模式**
2-3句话讲清公司做什么、怎么赚钱、处于产业链什么位置。

**行业地位与竞争壁垒**
在讨论区信息基础上，分析公司的护城河（技术/客户/成本/规模等），以及面临的竞争威胁。

**看多逻辑**
- 每条2-3句话，包含具体的数据、逻辑链或政策依据
- 区分短期催化（1-3个月）和长期逻辑（1年+）
- 至少4条

**看空风险**
- 每条2-3句话，说明风险的具体机制和影响程度
- 区分已知风险（市场已反映）和潜在风险（可能尚未定价）
- 至少3条

**近期关键事件追踪**
- 按时间线列出近期影响股价的重大事件
- 每个事件说明市场反应和后续影响

**市场情绪与资金面**
- 当前雪球社区的整体情绪倾向（给出看多/看空/分歧的比例估计）
- 讨论热度变化趋势（升温/冷却）
- 有无大V或机构观点值得关注

**投资要点总结**
3-5条核心结论，给出需要持续跟踪的关键变量

**写作要求**
- 只基于讨论区真实内容，不凭空编造
- 如果某个维度讨论区信息不足，标注「讨论区信息有限，待进一步研究」
- 语言专业、类似券商研报风格
- 关键数字和逻辑用**加粗**标注

雪球讨论区原始数据（{len(cleaned)} 条有效评论）:
{comment_text[:12000]}
"""
    try:
        ai_client = _deepseek_client()
        if ai_client is None:
            raise RuntimeError("未配置 DeepSeek")

        resp = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资深A股行业研究员。写作风格：专业、深度、信息密度高。"
                        "只基于提供的讨论区内容分析，不编造。不足处诚实标注。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        summary = (resp.choices[0].message.content or "").strip()
        print(f"AI 分析完成 ({len(summary)} 字)")
    except Exception as exc:
        title = f"AI 失败 · {stock_name}"
        md_text = f"❌ {stock_name}({xq_code}) 分析失败: {exc}"
        send_dingtalk_markdown(title, md_text)
        return

    # ---- 推送到钉钉 ----
    title = f"{stock_name} 分析"
    # 钉钉 markdown 限制约 20000 字符
    md_text = (
        f"## {stock_name}({xq_code}) 深度分析\n\n"
        f"> 基于雪球 {len(all_posts)} 条帖子 · AI 生成仅供参考\n\n"
        f"---\n\n"
        f"{summary[:18000]}"
    )

    send_dingtalk_markdown(title, md_text)
    print("分析完成，已推送到钉钉。")


def init_portfolio_state_only() -> None:
    """仅把各组合「当前最新调仓时间」写入 state，不推送、不调 AI。"""
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


# ===================================================================
# Main
# ===================================================================


def main(*, skip_portfolios: bool = False, force_markdown: bool = False) -> None:
    run_time = _now().strftime("%Y-%m-%d %H:%M")
    sim_note = f" [模拟时间，TEST_SIMULATE_NOW={TEST_SIMULATE_NOW}]" if TEST_SIMULATE_NOW else ""
    print(f"=== 每日组合 Digest ({run_time}){sim_note} ===")
    print(f"调仓回溯天数 DIGEST_LOOKBACK_DAYS={DIGEST_LOOKBACK_DAYS}")
    print(f"关注组合: {len(WATCH_PORTFOLIOS)} 个 ({', '.join(PORTFOLIO_NAMES.get(p, p) for p in WATCH_PORTFOLIOS)})")

    stock_names = [s["name"] for s in PRICE_ALERT_STOCKS]
    print(f"价格监控: {len(PRICE_ALERT_STOCKS)} 只 ({', '.join(stock_names)})")

    base_url = (DEEPSEEK_BASE_URL or "https://api.deepseek.com").strip()
    print(f"DeepSeek: {base_url}")

    state = load_state()

    # ---- Step 1: 检查价格 ----
    print(f"\n--- 价格检查 ---")
    price_alert = check_price_alerts(state)
    for s in price_alert.stocks:
        if s["current_price"] is not None:
            print(f"  {s['name']}({s['code']}) 现价: {s['current_price']:.2f}  ({s['holdings']})")
            for t in s["targets_hit"]:
                direction_icon = "📈" if t["direction"] == "above" else "📉"
                if t["triggered_now"]:
                    print(f"    🔔 {direction_icon} 触发 {t['label']}！")
                elif t["hit"]:
                    print(f"    ⚠ {direction_icon} {t['label']}（已触发过）")
                else:
                    diff = t["target"] - s["current_price"]
                    sign = "+" if diff > 0 else ""
                    print(f"    · {direction_icon} {t['label']}（差 {sign}{diff:.2f}）")
        else:
            print(f"  {s['name']}({s['code']}): {s['error']}")

    # ---- Step 2: 检查组合 ----
    updates: list[PortfolioUpdate] = []
    ai_budget = [MAX_AI_CALLS_PER_RUN]
    cookie_alert: dict[str, str] | None = None

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

        elif skip_portfolios:
            print("已跳过组合调仓巡检（--skip-portfolios）。")
        elif cookie_alert:
            print("Cookie 无效，已跳过组合调仓巡检。")

        state["last_digest_at"] = run_time

        # ---- Step 3: 推送 ----
        send_dingtalk_digest(
            run_time=run_time,
            updates=updates,
            force_markdown=force_markdown,
            cookie_alert=cookie_alert,
            price_alert=price_alert,
        )
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
    parser.add_argument(
        "--stock-query",
        default="",
        help="查询指定股票（名称或代码），分析雪球讨论区并推送钉钉",
    )
    args = parser.parse_args()

    # ---- stock_query 模式 ----
    stock_query = (args.stock_query or os.getenv("STOCK_QUERY", "")).strip()
    if stock_query:
        print(f"=== 股票查询模式: {stock_query} ===")
        _run_stock_query(stock_query)
        sys.exit(0)

    if args.init_state:
        init_portfolio_state_only()
    else:
        main(skip_portfolios=False, force_markdown=args.push_markdown)
