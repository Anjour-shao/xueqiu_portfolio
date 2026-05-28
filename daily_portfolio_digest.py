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
    "ZH3207026",
    "ZH3337164",
    "ZH3365207",
    "ZH1797852",
    "ZH3565914",
    "ZH3326300",
    "ZH3472193",
    "ZH3558598",
    "ZH810445",
    "ZH201132",
    "ZH3625288",
    "ZH3459601",
]

PORTFOLIO_NAMES: dict[str, str] = {
    "ZH3207026": "牛永贵",
    "ZH3337164": "三年10倍",
    "ZH3365207": "5年退休计划",
    "ZH1797852": "狗屎运",
    "ZH3565914": "安哲布",
    "ZH3326300": "先锋1号",
    "ZH3472193": "利润断层",
    "ZH3558598": "2026垃圾站",
    "ZH810445": "行业中优选",
    "ZH201132": "荣耀的进击",
    "ZH3625288": "集大成",
    "ZH3459601": "深度夹头",
}

# 股票账户（截图 2026-05-28 收盘）；cost_price 与雪球账本「持有盈亏」反推一致
MY_ACCOUNT: dict[str, Any] = {
    "name": "股票账户1",
    "total_assets": 59092.58,
}

# holding_days：与雪球账本「持股天数」一致；也可用 opened_at: "2026-05-16" 自动推算
MY_HOLDINGS: list[dict[str, Any]] = [
    {"code": "003043", "name": "华亚智能", "shares": 300, "cost_price": 62.03, "holding_days": 12},
    {"code": "600184", "name": "光电股份", "shares": 100, "cost_price": 30.94, "holding_days": 12},
    {"code": "002466", "name": "天齐锂业", "shares": 200, "cost_price": 80.94, "holding_days": 17},
    {"code": "600522", "name": "中天科技", "shares": 200, "cost_price": 25.48, "holding_days": 17},
]

ALWAYS_SEND_HOLDINGS = True

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
BACKEND = ROOT / "backend"
STATE_FILE = ROOT / "daily_digest_state.json"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
load_dotenv(ROOT / ".env")
if not os.getenv("ACCOUNT_DASHBOARD_DATABASE_URL", "").strip():
    os.environ["ACCOUNT_DASHBOARD_DATABASE_URL"] = "sqlite:///:memory:"

import requests
from openai import OpenAI

from xueqiu.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DINGTALK_KEYWORD,
    DINGTALK_WEBHOOK,
)
from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.integrations.sina.hfq import fetch_latest_hfq, xueqiu_to_sina
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import (
    REBALANCE_HISTORY_URL,
    _fetch_portfolio_name,
    _parse_rebalance_batch,
    fetch_portfolio_rebalance,
    validate_portfolio_id,
)
from xueqiu.integrations.xueqiu.posts import fetch_stock_posts

_SINA_SPOT_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _now() -> datetime:
    if TEST_SIMULATE_NOW:
        return datetime.strptime(TEST_SIMULATE_NOW.strip(), "%Y-%m-%d %H:%M:%S")
    return datetime.now()


def _parse_rebalance_dt(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


@dataclass
class AccountSummary:
    name: str
    total_assets: float | None
    market_value: float
    cash: float | None
    daily_pnl: float
    daily_pnl_pct: float | None
    holding_pnl: float
    holding_pnl_pct: float | None


@dataclass
class HoldingQuote:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    cost_price: float | None = None
    shares: float | None = None
    market_value: float | None = None
    daily_pnl_amount: float | None = None
    unrealized_pnl_pct: float | None = None
    unrealized_pnl_amount: float | None = None
    hfq_price: float | None = None
    hfq_cost: float | None = None
    hfq_pnl_pct: float | None = None
    holding_days: int | None = None
    weight_pct: float | None = None
    error: str = ""


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


def normalize_stock_code(raw: str) -> str:
    code = str(raw or "").strip().upper()
    if not code:
        raise ValueError("空股票代码")
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return code
    digits = code.split(".")[0]
    if digits.isdigit() and len(digits) == 6:
        if digits.startswith(("5", "6", "9")):
            return f"SH{digits}"
        return f"SZ{digits}"
    return to_xueqiu_code(code)


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_digest_at": None,
        "account": {"name": MY_ACCOUNT.get("name", "股票账户"), "last_total_assets": None},
        "portfolios": {},
        "holdings": {},
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

    # 兼容 v1 / v2
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
        migrated["holdings"] = dict(raw.get("holdings") or {})
        if isinstance(raw.get("account"), dict):
            migrated["account"].update(raw["account"])
        print(f"已将 state 从 v{ver} 迁移到 v{STATE_VERSION}。")
        return migrated

    state = _empty_state()
    state["last_digest_at"] = raw.get("last_digest_at")
    state["portfolios"] = dict(raw.get("portfolios") or {})
    state["holdings"] = dict(raw.get("holdings") or {})
    if isinstance(raw.get("account"), dict):
        state["account"] = {**state["account"], **raw["account"]}
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
你是一个专业的A股量化投研助手。请根据以下雪球用户的近期原生评论，深度分析当前市场对【{stock_name}】的共识与分歧。

输出要求：
1. 请分为三个维度进行结构化总结：【看多逻辑】、【看空隐患】、【关键事件/基本面追踪】。
2. 提取评论中提及的核心产业逻辑、订单传闻或财报预期。
3. 过滤纯情绪宣泄，只保留有信息量的论点。
4. 字数控制在300-500字左右，格式要求使用 Markdown 小标题与列表，排版清晰易读。

评论原始数据：
{comments}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是一个客观严谨的金融AI分析师，擅长从噪音中提取核心商业和市场逻辑。",
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
) -> list[dict[str, Any]]:
    """拉取 (since_time, as_of] 区间内所有手动调仓批次（时间升序）。"""
    pid = validate_portfolio_id(portfolio_id)

    # 从未推送过：只认最新一批，避免把 2023 年起全部历史当「新调仓」
    if not since_time:
        try:
            latest = fetch_portfolio_rebalance(pid, client=client)
            return [latest]
        except Exception:
            return []

    since_dt = _parse_rebalance_dt(since_time)
    as_of_dt = as_of or _now()
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
            found.append(crawled)

        if stop_paging or len(batches) < page_size:
            break
        if page < REBALANCE_HISTORY_PAGES:
            time.sleep(random.uniform(0.5, 1.0))

    found.sort(key=lambda x: x["rebalance_time"])
    return found


def _code_to_sinajs_symbol(code: str) -> str:
    digits, market = xueqiu_to_sina(code)
    return f"{market}{digits}"


def _parse_sinajs_line(line: str) -> tuple[float | None, float | None]:
    eq = line.find("=")
    if eq < 0:
        return None, None
    payload = line[eq + 1 :].strip().strip('";')
    if not payload or payload == '""':
        return None, None
    parts = payload.split(",")
    if len(parts) < 4:
        return None, None
    try:
        pre_close = float(parts[2])
        current = float(parts[3])
    except ValueError:
        return None, None
    if pre_close <= 0 or current <= 0:
        return None, None
    return current, pre_close


def _float_cfg(item: dict[str, Any], key: str) -> float | None:
    raw = item.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int_cfg(item: dict[str, Any], key: str) -> int | None:
    raw = item.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _resolve_holding_days(
    item: dict[str, Any],
    code: str,
    state: dict[str, Any],
) -> int | None:
    days = _int_cfg(item, "holding_days")
    if days is not None and days >= 0:
        return days
    opened = str(item.get("opened_at") or "").strip()
    if opened:
        try:
            opened_date = datetime.strptime(opened[:10], "%Y-%m-%d").date()
            return max(0, (_now().date() - opened_date).days)
        except ValueError:
            pass
    hist = (state.get("holdings") or {}).get(code) or {}
    return _int_cfg(hist, "holding_days")


def _apply_holding_weights(quotes: list[HoldingQuote]) -> None:
    total_mv = sum(q.market_value or 0.0 for q in quotes if q.market_value)
    if total_mv <= 0:
        return
    for q in quotes:
        if q.market_value is not None and q.weight_pct is None:
            q.weight_pct = round(q.market_value / total_mv * 100, 2)


def fetch_holding_quotes(
    holdings: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> list[HoldingQuote]:
    if not holdings:
        return []

    state = state or {}
    results: list[HoldingQuote] = []
    entries: list[tuple[str, str, str, float | None, float | None, int | None, float | None]] = []
    for item in holdings:
        raw_code = str(item.get("code", "")).strip()
        name = str(item.get("name", raw_code)).strip()
        cost = _float_cfg(item, "cost_price")
        shares = _float_cfg(item, "shares")
        weight_pct = _float_cfg(item, "weight_pct")
        if not raw_code:
            continue
        try:
            code = normalize_stock_code(raw_code)
            holding_days = _resolve_holding_days(item, code, state)
            entries.append(
                (code, name, _code_to_sinajs_symbol(code), cost, shares, holding_days, weight_pct)
            )
        except ValueError as exc:
            print(f"      跳过无效持仓代码 {raw_code}: {exc}")
            results.append(
                HoldingQuote(
                    code=raw_code.upper(),
                    name=name,
                    price=None,
                    change_pct=None,
                    cost_price=cost,
                    shares=shares,
                    error=str(exc),
                )
            )

    if not entries:
        return results

    symbols = [e[2] for e in entries]
    quotes_by_symbol: dict[str, tuple[float | None, float | None]] = {}
    url = "http://hq.sinajs.cn/list=" + ",".join(symbols)
    try:
        resp = requests.get(url, headers=_SINA_SPOT_HEADERS, timeout=15)
        resp.encoding = "gbk"
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            var_name = line.split("=", 1)[0]
            sym = var_name.replace("var hq_str_", "")
            quotes_by_symbol[sym] = _parse_sinajs_line(line)
    except Exception as exc:
        print(f"      新浪行情请求失败: {exc}")
        for code, name, _, cost, shares, hd, wp in entries:
            results.append(
                HoldingQuote(
                    code=code,
                    name=name,
                    price=None,
                    change_pct=None,
                    cost_price=cost,
                    shares=shares,
                    holding_days=hd,
                    weight_pct=wp,
                    error=str(exc),
                )
            )
        return results

    for code, name, sina_sym, cost, shares, holding_days, weight_pct in entries:
        current, pre_close = quotes_by_symbol.get(sina_sym, (None, None))
        if current is None or pre_close is None:
            results.append(
                HoldingQuote(
                    code=code,
                    name=name,
                    price=None,
                    change_pct=None,
                    cost_price=cost,
                    shares=shares,
                    holding_days=holding_days,
                    weight_pct=weight_pct,
                    error="无行情数据",
                )
            )
            continue
        change_pct = round((current - pre_close) / pre_close * 100, 2)
        daily_amt = round((current - pre_close) * shares, 2) if shares and shares > 0 else None
        mkt = round(current * shares, 2) if shares and shares > 0 else None
        pnl_pct = None
        pnl_amt = None
        hfq_price = None
        hfq_cost = None
        hfq_pnl_pct = None
        if cost and cost > 0:
            pnl_pct = round((current - cost) / cost * 100, 2)
            if shares and shares > 0:
                pnl_amt = round((current - cost) * shares, 2)
        try:
            _, hfq_close = fetch_latest_hfq(code)
            hfq_price = hfq_close
            if cost and cost > 0 and current > 0:
                hfq_cost = cost * (hfq_close / current)
                hfq_pnl_pct = round((hfq_close - hfq_cost) / hfq_cost * 100, 2)
        except Exception:
            pass
        results.append(
            HoldingQuote(
                code=code,
                name=name,
                price=current,
                change_pct=change_pct,
                cost_price=cost,
                shares=shares,
                market_value=mkt,
                daily_pnl_amount=daily_amt,
                unrealized_pnl_pct=pnl_pct,
                unrealized_pnl_amount=pnl_amt,
                hfq_price=hfq_price,
                hfq_cost=hfq_cost,
                hfq_pnl_pct=hfq_pnl_pct,
                holding_days=holding_days,
                weight_pct=weight_pct,
            )
        )
    _apply_holding_weights(results)
    return results


def build_account_summary(
    quotes: list[HoldingQuote],
    state: dict[str, Any],
) -> AccountSummary:
    name = str(MY_ACCOUNT.get("name") or state.get("account", {}).get("name") or "股票账户")
    market_value = sum(q.market_value or 0.0 for q in quotes)
    daily_pnl = sum(q.daily_pnl_amount or 0.0 for q in quotes)
    holding_pnl = sum(q.unrealized_pnl_amount or 0.0 for q in quotes)

    cfg_assets = _float_cfg(MY_ACCOUNT, "total_assets")
    cash_cfg = _float_cfg(MY_ACCOUNT, "cash")
    if cash_cfg is not None:
        cash = cash_cfg
        total_assets = market_value + cash
    elif cfg_assets is not None and cfg_assets >= market_value:
        cash = round(cfg_assets - market_value, 2)
        total_assets = cfg_assets
    else:
        cash = None
        total_assets = market_value if market_value > 0 else cfg_assets

    # 当日收益率：相对「昨日总资产」≈ 今日总资产 − 当日盈亏（与雪球 +1.91% 口径一致）
    # 不用 last_total_assets 对比：MY_ACCOUNT.total_assets 是手填静态值，与 state 相同会得到 0%
    daily_pnl_pct = None
    if total_assets is not None:
        prev_total = float(total_assets) - daily_pnl
        if prev_total > 0:
            daily_pnl_pct = round(daily_pnl / prev_total * 100, 2)
    elif market_value > daily_pnl:
        daily_pnl_pct = round(daily_pnl / (market_value - daily_pnl) * 100, 2)

    cost_basis = sum((q.cost_price or 0) * (q.shares or 0) for q in quotes if q.cost_price)
    holding_pnl_pct = None
    if cost_basis > 0:
        holding_pnl_pct = round(holding_pnl / cost_basis * 100, 2)

    return AccountSummary(
        name=name,
        total_assets=total_assets,
        market_value=round(market_value, 2),
        cash=cash,
        daily_pnl=round(daily_pnl, 2),
        daily_pnl_pct=daily_pnl_pct,
        holding_pnl=round(holding_pnl, 2),
        holding_pnl_pct=holding_pnl_pct,
    )


def update_holdings_state(
    state: dict[str, Any],
    quotes: list[HoldingQuote],
    account: AccountSummary,
) -> None:
    state.setdefault("holdings", {})
    state.setdefault("account", {})
    for q in quotes:
        if q.price is None:
            continue
        state["holdings"][q.code] = {
            "name": q.name,
            "cost_price": q.cost_price,
            "shares": q.shares,
            "last_price": q.price,
            "last_change_pct": q.change_pct,
            "daily_pnl_amount": q.daily_pnl_amount,
            "market_value": q.market_value,
            "unrealized_pnl_pct": q.unrealized_pnl_pct,
            "unrealized_pnl_amount": q.unrealized_pnl_amount,
            "hfq_price": q.hfq_price,
            "hfq_cost": q.hfq_cost,
            "hfq_pnl_pct": q.hfq_pnl_pct,
            "holding_days": q.holding_days,
            "weight_pct": q.weight_pct,
            "updated_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    if account.total_assets is not None:
        state["account"]["last_total_assets"] = account.total_assets
    state["account"]["name"] = account.name
    state["account"]["last_snapshot"] = {
        "at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_value": account.market_value,
        "daily_pnl": account.daily_pnl,
        "daily_pnl_pct": account.daily_pnl_pct,
        "holding_pnl": account.holding_pnl,
        "holding_pnl_pct": account.holding_pnl_pct,
    }


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "-"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_money(amount: float | None) -> str:
    if amount is None:
        return "-"
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:,.2f}"


def _fmt_money_plain(amount: float | None) -> str:
    if amount is None:
        return "-"
    return f"{amount:,.2f}"


def _trend_tag(pct: float | None) -> str:
    if pct is None:
        return "·"
    if pct > 0:
        return "涨"
    if pct < 0:
        return "跌"
    return "平"


def _holdings_markdown(quotes: list[HoldingQuote], account: AccountSummary) -> str:
    if not quotes:
        return ""

    lines = [
        f"### 💼 {account.name}",
        "",
        "#### 账户概览",
        "",
    ]
    if account.total_assets is not None:
        d_pct = _fmt_pct(account.daily_pnl_pct) if account.daily_pnl_pct is not None else "-"
        h_pct = _fmt_pct(account.holding_pnl_pct) if account.holding_pnl_pct is not None else "-"
        overview = [
            f"- 总资产 **{_fmt_money_plain(account.total_assets)}**",
            f"- 市值 **{_fmt_money_plain(account.market_value)}**",
        ]
        if account.cash is not None:
            overview.append(f"- 现金 **{_fmt_money_plain(account.cash)}**")
        overview.extend(
            [
                f"- 当日盈亏 **{_fmt_money(account.daily_pnl)}**（{d_pct}）",
                f"- 持有盈亏 **{_fmt_money(account.holding_pnl)}**（{h_pct}）",
                "",
            ]
        )
        lines.extend(overview)

    sorted_quotes = sorted(
        quotes,
        key=lambda q: (q.weight_pct is None, -(q.weight_pct or 0)),
    )
    lines.append(f"#### 持仓明细（{len(sorted_quotes)} 只）")
    lines.append("")

    for idx, q in enumerate(sorted_quotes, 1):
        if q.error:
            lines.append(f"**{idx}. {q.name}** `{q.code}`  \n{q.error}")
            lines.append("")
            continue

        meta_parts: list[str] = []
        if q.holding_days is not None:
            meta_parts.append(f"持股 **{q.holding_days}** 天")
        if q.shares:
            meta_parts.append(f"**{int(q.shares)}** 股")
        if q.weight_pct is not None:
            meta_parts.append(f"仓位 **{q.weight_pct:.1f}%**")
        meta_line = " · ".join(meta_parts) if meta_parts else ""

        price_str = f"{q.price:.2f}" if q.price is not None else "-"
        day_line = f"今日 {_trend_tag(q.change_pct)} **{_fmt_pct(q.change_pct)}**"
        if q.daily_pnl_amount is not None:
            day_line += f"（{_fmt_money(q.daily_pnl_amount)}）"

        lines.append(f"**{idx}. {q.name}** `{q.code}`")
        if meta_line:
            lines.append(f"> {meta_line}")
        lines.append(f"> 现价 **{price_str}** · {day_line}")

        if q.unrealized_pnl_pct is not None:
            pnl_line = f"> 持仓盈亏 **{_fmt_pct(q.unrealized_pnl_pct)}**"
            if q.unrealized_pnl_amount is not None:
                pnl_line += f"（{_fmt_money(q.unrealized_pnl_amount)}）"
            if q.hfq_pnl_pct is not None:
                pnl_line += f" · 后复权 {_fmt_pct(q.hfq_pnl_pct)}"
            lines.append(pnl_line)
        lines.append("")

    return "\n".join(lines)


def _truncate_ai_text(text: str, limit: int = 420) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "…"


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


def send_dingtalk_digest(
    *,
    run_time: str,
    account: AccountSummary | None = None,
    quotes: list[HoldingQuote] | None = None,
    updates: list[PortfolioUpdate] | None = None,
    force_markdown: bool = False,
) -> None:
    """推送简报：默认 HTML 渲染为图片；失败时回退 Markdown。"""
    from digest import render as digest_render

    updates = updates or []
    title = _digest_push_title(updates)
    simulate_note = f"模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    push_mode = digest_render.DIGEST_PUSH_MODE or "image"

    if not force_markdown and push_mode in ("image", "both"):
        try:
            ok, local_path = digest_render.push_digest_image(
                run_time=run_time,
                simulate_note=simulate_note,
                account=account,
                quotes=quotes,
                updates=updates,
                title=title,
            )
            if ok and push_mode == "image":
                return
            if ok and push_mode == "both":
                print("      图片已推送，继续发送 Markdown 摘要…")
            elif local_path:
                print(f"      图床未配置或上传失败，已生成本地预览: {local_path}")
                print("      建议在 .env 配置 IMG_BB_API_KEY 后重试（见 docs/DIGEST_GITHUB_SETUP.md）")
        except Exception as exc:
            print(f"      图片简报失败，回退 Markdown: {exc}")

    holdings_md = ""
    if quotes and account:
        holdings_md = _holdings_markdown(quotes, account)

    mode = f" · 模拟 {TEST_SIMULATE_NOW}" if TEST_SIMULATE_NOW else ""
    md_parts = [
        f"## 📊 每日组合简报{mode}",
        "",
        f"> {run_time}",
        "",
    ]
    if holdings_md:
        md_parts.append(holdings_md.rstrip())
        md_parts.append("")
    if updates:
        md_parts.append("---")
        md_parts.append("")
        md_parts.append("### 🔔 组合调仓")
        md_parts.append("")
        for upd in updates:
            md_parts.append(_portfolio_update_markdown(upd).rstrip())
            md_parts.append("")
    elif not holdings_md:
        md_parts.append("今晚无持仓配置且无待推送的调仓。")

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

    try:
        new_batches_raw = fetch_rebalances_since(
            client, pid, last_notified, as_of=as_of
        )
    except Exception as exc:
        print(f"[{pid}] 获取调仓历史失败: {exc}")
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


def run_holdings_only(*, force_markdown: bool = False) -> None:
    """仅推送个人持仓行情，不扫组合、不调 DeepSeek。"""
    run_time = _now().strftime("%Y-%m-%d %H:%M")
    print(f"=== 仅持仓简报 ({run_time}) ===")
    if not MY_HOLDINGS:
        print("未配置 MY_HOLDINGS。")
        return
    state = load_state()
    quotes = fetch_holding_quotes(MY_HOLDINGS, state=state)
    account_summary = build_account_summary(quotes, state)
    update_holdings_state(state, quotes, account_summary)
    save_state(state)
    send_dingtalk_digest(
        run_time=run_time,
        account=account_summary,
        quotes=quotes,
        updates=[],
        force_markdown=force_markdown,
    )
    print("=== 执行完毕 ===")


def main(*, skip_portfolios: bool = False, force_markdown: bool = False) -> None:
    run_time = _now().strftime("%Y-%m-%d %H:%M")
    sim_note = f" [模拟时间，TEST_SIMULATE_NOW={TEST_SIMULATE_NOW}]" if TEST_SIMULATE_NOW else ""
    print(f"=== 每日组合 Digest ({run_time}){sim_note} ===")

    base_url = (DEEPSEEK_BASE_URL or "https://api.deepseek.com").strip()
    print(f"DeepSeek: {base_url}")

    if not WATCH_PORTFOLIOS and not MY_HOLDINGS:
        print("请在脚本顶部配置 WATCH_PORTFOLIOS 或 MY_HOLDINGS。")
        return

    state = load_state()
    updates: list[PortfolioUpdate] = []
    ai_budget = [MAX_AI_CALLS_PER_RUN]

    if not skip_portfolios and WATCH_PORTFOLIOS:
        client = XueQiuApiClient()
        portfolios = [p.strip().upper() for p in WATCH_PORTFOLIOS if p.strip()]
        total = len(portfolios)
        for index, pid in enumerate(portfolios, 1):
            upd = check_portfolio_for_nightly_digest(
                client, pid, state, ai_budget=ai_budget
            )
            if upd is not None:
                updates.append(upd)
            if index < total:
                time.sleep(random.uniform(1.0, 2.0))
        if ai_budget[0] < MAX_AI_CALLS_PER_RUN:
            used = MAX_AI_CALLS_PER_RUN - ai_budget[0]
            print(f"\n本 run 已调用 DeepSeek {used} 次（上限 {MAX_AI_CALLS_PER_RUN}）。")
    elif skip_portfolios:
        print("已跳过组合调仓巡检（--holdings-only）。")

    state["last_digest_at"] = run_time

    quotes: list[HoldingQuote] | None = None
    account_summary: AccountSummary | None = None
    if MY_HOLDINGS and (ALWAYS_SEND_HOLDINGS or updates):
        print("\n拉取个人持仓行情（现价 + 后复权）…")
        quotes = fetch_holding_quotes(MY_HOLDINGS, state=state)
        account_summary = build_account_summary(quotes, state)
        update_holdings_state(state, quotes, account_summary)

    save_state(state)

    should_send = bool(updates) or (ALWAYS_SEND_HOLDINGS and bool(MY_HOLDINGS))
    if should_send:
        send_dingtalk_digest(
            run_time=run_time,
            account=account_summary,
            quotes=quotes,
            updates=updates,
            force_markdown=force_markdown,
        )
    else:
        print("今晚无调仓待推送且未开启持仓推送，跳过钉钉。")

    print("=== 执行完毕 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日组合 Digest")
    parser.add_argument(
        "--init-state",
        action="store_true",
        help="仅同步各组合最新调仓时间到 state，不推送",
    )
    parser.add_argument(
        "--holdings-only",
        action="store_true",
        help="仅推送个人持仓，不扫组合、不调 AI",
    )
    parser.add_argument(
        "--push-markdown",
        action="store_true",
        help="强制 Markdown 文本推送（默认 HTML 渲染为图片）",
    )
    args = parser.parse_args()
    if args.init_state:
        init_portfolio_state_only()
    elif args.holdings_only:
        run_holdings_only(force_markdown=args.push_markdown)
    else:
        main(skip_portfolios=False, force_markdown=args.push_markdown)
