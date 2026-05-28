from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Callable
from typing import Any

from xueqiu.domain.codes import is_cn_a_share, is_hk_us_or_non_a_share, to_xueqiu_code
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError

PORTFOLIO_ID_RE = re.compile(r"^ZH\d+$", re.IGNORECASE)
REBALANCE_HISTORY_URL = "https://xueqiu.com/cubes/rebalancing/history.json"
CUBE_NAV_URL = "https://xueqiu.com/cubes/nav_daily/all.json"
CUBE_SHOW_URL = "https://xueqiu.com/cubes/show.json"
CUBE_QUOTE_URL = "https://xueqiu.com/cubes/quote.json"

DIVIDEND_MARKERS = ("分红送配", "分红", "送股", "转增", "配股")
# 分红送配后 API 常返回极小权重差（总仓位重算），低于该阈值视为公司行为而非手动减仓
DIVIDEND_WEIGHT_DRIFT_MAX = 0.25

ACTION_CN = {
    "BUY": "买入",
    "SELL": "卖出",
    "INCREASE": "加仓",
    "DECREASE": "减仓",
    "HOLD": "持平",
}


@dataclass
class CubeNavPoint:
    trade_date: str
    nav_value: float
    cum_return_pct: float
    timestamp_ms: int | None = None


def _parse_trade_date(raw: Any) -> str:
    if isinstance(raw, str) and raw:
        if len(raw) == 8 and raw.isdigit():
            return raw
        if len(raw) >= 10 and raw[4] == "-":
            return raw[:10].replace("-", "")
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw / 1000).strftime("%Y%m%d")
    raise ValueError(f"无法解析日期: {raw}")


def _extract_nav_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("list"), list):
            return [item for item in first["list"] if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return [item for item in data["list"] if isinstance(item, dict)]
    return []


def fetch_cube_nav_daily(
    cube_symbol: str,
    client: XueQiuApiClient | None = None,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> tuple[str, list[CubeNavPoint]]:
    cube_symbol = validate_portfolio_id(cube_symbol)
    api = client or XueQiuApiClient()
    params: dict[str, Any] = {"cube_symbol": cube_symbol}
    if since_ms is not None:
        params["since"] = since_ms
    if until_ms is not None:
        params["until"] = until_ms

    try:
        data = api.get_json(CUBE_NAV_URL, params=params)
    except XueQiuApiError as exc:
        # 部分组合带 since 会 400，去掉 since 再试一次（仍可能是限流，由上层退避）
        if since_ms is not None and "400" in str(exc):
            time.sleep(random.uniform(1.2, 2.2))
            data = api.get_json(CUBE_NAV_URL, params={"cube_symbol": cube_symbol})
        else:
            raise
    portfolio_name = cube_symbol
    if isinstance(data, list) and data and isinstance(data[0], dict):
        portfolio_name = str(data[0].get("name") or cube_symbol)

    points: list[CubeNavPoint] = []
    for item in _extract_nav_list(data):
        try:
            trade_date = _parse_trade_date(item.get("date") or item.get("time"))
            nav_value = float(item.get("value") or 0)
            cum_return_pct = float(item.get("percent") or 0)
        except (TypeError, ValueError):
            continue
        if nav_value <= 0:
            continue
        timestamp_ms = int(item["time"]) if item.get("time") is not None else None
        points.append(
            CubeNavPoint(
                trade_date=trade_date,
                nav_value=nav_value,
                cum_return_pct=cum_return_pct,
                timestamp_ms=timestamp_ms,
            )
        )

    points.sort(key=lambda p: p.trade_date)
    if since_ms is None and points:
        base_value = points[0].nav_value
        if base_value > 0:
            points = [
                CubeNavPoint(
                    trade_date=p.trade_date,
                    nav_value=p.nav_value,
                    cum_return_pct=round((p.nav_value / base_value - 1.0) * 100, 4),
                    timestamp_ms=p.timestamp_ms,
                )
                for p in points
            ]
    return portfolio_name, points


def validate_portfolio_id(portfolio_id: str) -> str:
    code = portfolio_id.strip().upper()
    if not PORTFOLIO_ID_RE.match(code):
        raise ValueError(f"账户代码必须是雪球组合 ID（如 ZH3207026），当前为: {portfolio_id}")
    return code


def _line_weights(item: dict[str, Any]) -> tuple[float, float]:
    from_weight = float(
        item.get("prev_weight")
        if item.get("prev_weight") is not None
        else item.get("prev_target_weight") or 0
    )
    to_weight = float(
        item.get("target_weight")
        if item.get("target_weight") is not None
        else item.get("weight") or 0
    )
    return from_weight, to_weight


def _iter_json_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_json_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_json_strings(value)


def _line_is_dividend_corporate(item: dict[str, Any]) -> bool:
    for text in _iter_json_strings(item):
        if any(marker in text for marker in DIVIDEND_MARKERS):
            return True

    from_weight, to_weight = _line_weights(item)
    # 分红送配在 UI 上常显示为「参考成交价」且权重不变或几乎不变
    if from_weight > 1e-9 and abs(from_weight - to_weight) < 1e-9:
        return True
    # 如中矿资源 0.42%→0.36%：雪球标记为分红送配，但 API 只给微小权重差
    if from_weight > 1e-9 and to_weight > 1e-9:
        if abs(to_weight - from_weight) < DIVIDEND_WEIGHT_DRIFT_MAX:
            return True
    return False


def _line_is_manual_rebalance(item: dict[str, Any]) -> bool:
    if _line_is_dividend_corporate(item):
        return False
    symbol = item.get("stock_symbol") or item.get("code")
    if not symbol or not is_cn_a_share(str(symbol)):
        return False
    from_weight, to_weight = _line_weights(item)
    return infer_action(from_weight, to_weight) != "HOLD"


def _classify_rebalance_batch(batch: dict[str, Any]) -> tuple[bool, bool]:
    """返回 (含手动调仓, 含港美股/非A股)。"""
    has_manual = False
    has_non_a = False
    for item in batch.get("rebalancing_histories") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("stock_symbol") or item.get("code") or "").strip()
        if symbol and is_hk_us_or_non_a_share(symbol):
            has_non_a = True
        if _line_is_manual_rebalance(item):
            has_manual = True
    return has_manual, has_non_a


def _collect_symbols_from_payload(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"stock_symbol", "symbol", "code"} and isinstance(value, str):
                text = value.strip().upper()
                if text and not text.startswith("ZH"):
                    out.add(text)
            else:
                _collect_symbols_from_payload(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_symbols_from_payload(item, out)


def fetch_cube_holdings_symbols(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
) -> set[str]:
    """当前组合持仓中的标的代码（show/quote 接口）。"""
    portfolio_id = validate_portfolio_id(portfolio_id)
    api = client or XueQiuApiClient()
    symbols: set[str] = set()
    for url, param_key in ((CUBE_SHOW_URL, "symbol"), (CUBE_QUOTE_URL, "code")):
        try:
            data = api.get_json(url, params={param_key: portfolio_id})
            _collect_symbols_from_payload(data, symbols)
        except XueQiuApiError:
            continue
    return symbols


def portfolio_has_non_a_share(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
) -> bool:
    symbols = fetch_cube_holdings_symbols(portfolio_id, client=client)
    return any(is_hk_us_or_non_a_share(sym) for sym in symbols)


def infer_action(from_weight: float, to_weight: float) -> str:
    if from_weight <= 1e-9 and to_weight > 1e-9:
        return "BUY"
    if from_weight > 1e-9 and to_weight <= 1e-9:
        return "SELL"
    if to_weight > from_weight + 1e-9:
        return "INCREASE"
    if to_weight + 1e-9 < from_weight:
        return "DECREASE"
    return "HOLD"


def _format_rebalance_time(updated_at: Any) -> str:
    if isinstance(updated_at, (int, float)) and updated_at > 0:
        return datetime.fromtimestamp(updated_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(updated_at, str) and updated_at:
        return updated_at
    raise RuntimeError(f"无法解析调仓时间: {updated_at}")


def _fetch_portfolio_name(client: XueQiuApiClient, portfolio_id: str) -> str:
    try:
        data = client.get_json(CUBE_NAV_URL, params={"cube_symbol": portfolio_id})
    except XueQiuApiError:
        return portfolio_id
    if isinstance(data, list) and data:
        return str(data[0].get("name") or portfolio_id)
    if isinstance(data, dict):
        return str(data.get("name") or portfolio_id)
    return portfolio_id


def _parse_api_stock_item(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = item.get("stock_symbol") or item.get("code")
    name = item.get("stock_name") or item.get("name")
    if not symbol or not name:
        return None
    if _line_is_dividend_corporate(item) or is_hk_us_or_non_a_share(str(symbol)):
        return None

    from_weight, to_weight = _line_weights(item)
    price_raw = item.get("price")
    price_text = f"成交价：{price_raw}" if price_raw is not None else ""
    action = ACTION_CN.get(infer_action(from_weight, to_weight), "未知")

    return {
        "action": action,
        "name": str(name),
        "code": to_xueqiu_code(str(symbol)),
        "price": price_text,
        "weight_change": f"{from_weight:g}% -> {to_weight:g}%",
        "from_weight": from_weight,
        "to_weight": to_weight,
    }


def record_to_trade(rebalance_time: str, record: dict[str, Any]) -> dict[str, Any]:
    from_weight = float(record["from_weight"])
    to_weight = float(record["to_weight"])
    ts_code = to_xueqiu_code(str(record["code"]))
    price_raw = str(record.get("price", "")).replace("成交价：", "").replace("成交价:", "").strip()
    price = float(price_raw) if price_raw else None
    action = infer_action(from_weight, to_weight)
    raw_block = (
        f"{record['name']}\n{ts_code}\n{from_weight:.2f}%{to_weight:.2f}%\n"
        f"参考成交价 {price if price is not None else ''}"
    )
    return {
        "trade_time": rebalance_time,
        "stock_name": str(record["name"]),
        "ts_code": ts_code,
        "from_weight": from_weight,
        "to_weight": to_weight,
        "weight_delta": round(to_weight - from_weight, 4),
        "price": price,
        "action": action,
        "raw_block": raw_block,
    }


def fetch_rebalance_events(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
    *,
    lookback_days: int = 120,
    max_pages: int = 5,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], bool]:
    """拉取近期手动调仓事件；分红送配批次不计入。返回 (events, 历史含港美股)。"""
    portfolio_id = validate_portfolio_id(portfolio_id)
    api = client or XueQiuApiClient()
    cutoff = datetime.now() - timedelta(days=lookback_days)
    events: list[dict[str, Any]] = []
    history_has_non_a = False

    for page in range(1, max_pages + 1):
        data = api.get_json(
            REBALANCE_HISTORY_URL,
            params={"cube_symbol": portfolio_id, "page": page, "count": page_size},
        )
        batches = data.get("list") if isinstance(data, dict) else None
        if not isinstance(batches, list) or not batches:
            break

        for batch in batches:
            if not isinstance(batch, dict) or batch.get("status") != "success":
                continue
            has_manual, has_non_a = _classify_rebalance_batch(batch)
            if has_non_a:
                history_has_non_a = True
            if not has_manual:
                continue
            try:
                trade_time = _format_rebalance_time(batch.get("updated_at"))
                dt = datetime.strptime(trade_time[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, RuntimeError):
                continue
            if dt < cutoff:
                continue
            events.append(
                {
                    "trade_time": trade_time,
                    "month_key": trade_time[:7],
                    "timestamp": dt.timestamp(),
                }
            )

        if len(batches) < page_size:
            break
        if page < max_pages:
            time.sleep(random.uniform(0.35, 0.7))

    events.sort(key=lambda x: float(x["timestamp"]), reverse=True)
    return events, history_has_non_a


def _parse_rebalance_batch(
    portfolio_id: str,
    portfolio_name: str,
    batch: dict[str, Any],
) -> dict[str, Any] | None:
    """将单次调仓 batch 解析为 crawled 结构；无有效手动调仓时返回 None。"""
    has_manual, _ = _classify_rebalance_batch(batch)
    if not has_manual:
        return None
    try:
        rebalance_time = _format_rebalance_time(batch.get("updated_at"))
    except RuntimeError:
        return None

    histories = batch.get("rebalancing_histories") or []
    records: list[dict[str, Any]] = []
    parse_skipped = 0
    for item in histories:
        if not isinstance(item, dict):
            parse_skipped += 1
            continue
        parsed = _parse_api_stock_item(item)
        if parsed:
            records.append(parsed)
        else:
            parse_skipped += 1

    if not records:
        return None

    trades = [record_to_trade(rebalance_time, record) for record in records]
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "rebalance_time": rebalance_time,
        "records": records,
        "trades": trades,
        "parse_skipped": parse_skipped,
    }


def _get_json_with_retry(
    api: XueQiuApiClient,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 4,
) -> Any:
    """雪球接口 400/429 时自动退避重试。"""
    last: XueQiuApiError | None = None
    for attempt in range(max_retries):
        try:
            return api.get_json(url, params=params)
        except XueQiuApiError as exc:
            last = exc
            msg = str(exc)
            if attempt < max_retries - 1 and any(token in msg for token in ("400", "429", "502", "503")):
                time.sleep(random.uniform(1.2, 2.2) * (attempt + 1))
                continue
            raise
    if last is not None:
        raise last
    raise RuntimeError("雪球请求失败")


def fetch_portfolio_rebalance_all(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
    *,
    max_pages: int = 50,
    page_size: int = 20,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """分页拉取全部手动调仓批次（从旧到新），供全量入库。"""
    portfolio_id = validate_portfolio_id(portfolio_id)
    api = client or XueQiuApiClient()
    portfolio_name = _fetch_portfolio_name(api, portfolio_id)
    seen_times: set[str] = set()
    batches_out: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        if on_progress is not None:
            on_progress(page, len(batches_out))
        data = _get_json_with_retry(
            api,
            REBALANCE_HISTORY_URL,
            params={"cube_symbol": portfolio_id, "page": page, "count": page_size},
        )
        raw_batches = data.get("list") if isinstance(data, dict) else None
        if not isinstance(raw_batches, list) or not raw_batches:
            break

        page_added = 0
        for batch in raw_batches:
            if not isinstance(batch, dict) or batch.get("status") != "success":
                continue
            crawled = _parse_rebalance_batch(portfolio_id, portfolio_name, batch)
            if crawled is None:
                continue
            rt = crawled["rebalance_time"]
            if rt in seen_times:
                continue
            seen_times.add(rt)
            batches_out.append(crawled)
            page_added += 1

        if on_progress is not None:
            on_progress(page, len(batches_out))

        if len(raw_batches) < page_size:
            break
        if page < max_pages:
            time.sleep(random.uniform(0.55, 1.05))

    batches_out.sort(key=lambda x: x["rebalance_time"])
    return batches_out


def fetch_portfolio_rebalance(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
) -> dict[str, Any]:
    portfolio_id = validate_portfolio_id(portfolio_id)
    api = client or XueQiuApiClient()

    data = api.get_json(
        REBALANCE_HISTORY_URL,
        params={"cube_symbol": portfolio_id, "page": 1, "count": 1},
    )
    batches = data.get("list") if isinstance(data, dict) else None
    if not batches:
        raise RuntimeError(f"组合 {portfolio_id} 未找到调仓记录。")

    batch: dict[str, Any] | None = None
    for item in batches:
        if not isinstance(item, dict) or item.get("status") != "success":
            continue
        has_manual, _ = _classify_rebalance_batch(item)
        if has_manual:
            batch = item
            break
    if batch is None:
        raise RuntimeError(f"组合 {portfolio_id} 最新手动调仓不可用（可能仅分红送配或已取消）。")

    portfolio_name = _fetch_portfolio_name(api, portfolio_id)
    crawled = _parse_rebalance_batch(portfolio_id, portfolio_name, batch)
    if crawled is None:
        rebalance_time = _format_rebalance_time(batch.get("updated_at"))
        raise RuntimeError(f"组合 {portfolio_id} 最新调仓 ({rebalance_time}) 未解析到有效记录")
    return crawled


def fetch_all_portfolios_rebalance(
    portfolio_ids: list[str],
    client: XueQiuApiClient | None = None,
) -> list[dict[str, Any]]:
    codes = [validate_portfolio_id(pid) for pid in portfolio_ids]
    if not codes:
        return []

    api = client or XueQiuApiClient()
    results: list[dict[str, Any]] = []
    total = len(codes)
    for index, portfolio_id in enumerate(codes):
        try:
            data = fetch_portfolio_rebalance(portfolio_id, client=api)
            results.append({"portfolio_id": portfolio_id, "ok": True, "data": data})
        except Exception as exc:
            results.append({"portfolio_id": portfolio_id, "ok": False, "error": str(exc)})
        if index < total - 1:
            time.sleep(random.uniform(0.8, 1.6))
    return results
