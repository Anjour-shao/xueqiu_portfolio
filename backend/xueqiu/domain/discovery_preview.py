from __future__ import annotations

from datetime import datetime
from typing import Any

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import (
    CUBE_QUOTE_URL,
    REBALANCE_HISTORY_URL,
    _classify_rebalance_batch,
    _fetch_cube_show_snapshot,
    _fetch_rebalance_raw_batch,
    _format_rebalance_time,
    _get_json_with_retry,
    _parse_api_stock_item,
    fetch_portfolio_rebalance,
    validate_portfolio_id,
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _holdings_from_rebalance_batch(batch: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in batch.get("rebalancing_histories") or []:
        if not isinstance(item, dict):
            continue
        weight = item.get("target_weight")
        if weight is None:
            weight = item.get("weight")
        w = _safe_float(weight) or 0.0
        if w <= 0.5:
            continue
        name = item.get("stock_name") or item.get("name")
        symbol = item.get("stock_symbol") or item.get("code")
        if not name or not symbol:
            continue
        out.append({"stock_name": str(name), "symbol": str(symbol).upper(), "weight": round(w, 2)})
    out.sort(key=lambda row: row["weight"], reverse=True)
    return out


def _quote_block(api: XueQiuApiClient, code: str) -> dict[str, Any]:
    data = api.get_json(
        CUBE_QUOTE_URL,
        params={"code": code},
        warm_symbol=code,
        referer=f"https://xueqiu.com/P/{code}",
    )
    if not isinstance(data, dict):
        return {}
    block = data.get(code)
    return block if isinstance(block, dict) else {}


def _recent_rebalance_rows(
    api: XueQiuApiClient,
    code: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    data = _get_json_with_retry(
        api,
        REBALANCE_HISTORY_URL,
        params={"cube_symbol": code, "page": 1, "count": 20},
        warm_symbol=code,
    )
    batches = data.get("list") if isinstance(data, dict) else None
    if not isinstance(batches, list):
        return []

    rows: list[dict[str, Any]] = []
    for batch in batches:
        if not isinstance(batch, dict) or batch.get("status") != "success":
            continue
        has_manual, _ = _classify_rebalance_batch(batch)
        if not has_manual:
            continue
        try:
            trade_time = _format_rebalance_time(batch.get("updated_at"))
        except RuntimeError:
            continue
        actions: list[str] = []
        for item in batch.get("rebalancing_histories") or []:
            if not isinstance(item, dict):
                continue
            parsed = _parse_api_stock_item(item)
            if parsed:
                actions.append(f"{parsed['action']} {parsed['name']} {parsed['weight_change']}")
        if not actions:
            continue
        rows.append({"trade_time": trade_time, "actions": actions[:4]})
        if len(rows) >= limit:
            break
    return rows


def build_discovery_cube_preview(account_code: str) -> dict[str, Any]:
    code = validate_portfolio_id(account_code)
    api = XueQiuApiClient()

    show = _fetch_cube_show_snapshot(api, code)
    quote = _quote_block(api, code)
    owner = show.get("owner") if isinstance(show.get("owner"), dict) else {}
    performance = show.get("performance") if isinstance(show.get("performance"), dict) else {}

    created_at: str | None = None
    raw_created = show.get("created_at")
    if raw_created is not None:
        try:
            created_at = datetime.fromtimestamp(int(raw_created) / 1000).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            created_at = None

    latest = fetch_portfolio_rebalance(code, client=api)

    holdings: list[dict[str, Any]] = []
    try:
        rb_id = show.get("last_user_rb_gid") or show.get("last_rb_id")
        if rb_id is not None:
            batch = _fetch_rebalance_raw_batch(api, code, int(rb_id))
            holdings = _holdings_from_rebalance_batch(batch)
    except (XueQiuApiError, TypeError, ValueError):
        holdings = []

    latest_trades = [
        {
            "action": str(rec.get("action") or ""),
            "stock_name": str(rec.get("name") or ""),
            "symbol": str(rec.get("code") or ""),
            "weight_change": str(rec.get("weight_change") or ""),
        }
        for rec in latest.get("records") or []
    ]

    try:
        recent_rebalances = _recent_rebalance_rows(api, code)
    except XueQiuApiError:
        recent_rebalances = []

    return {
        "account_code": code,
        "account_name": str(quote.get("name") or show.get("name") or code),
        "owner_name": str(owner.get("screen_name") or "").strip() or None,
        "description": str(show.get("description") or "").strip() or None,
        "market": str(quote.get("market") or show.get("market") or "").strip() or None,
        "created_at": created_at,
        "follower_count": int(show.get("follower_count") or 0),
        "net_value": _safe_float(quote.get("net_value")),
        "total_gain_pct": _safe_float(quote.get("total_gain")),
        "monthly_gain_pct": _safe_float(quote.get("monthly_gain")),
        "daily_gain_pct": _safe_float(quote.get("daily_gain")),
        "annualized_gain_pct": _safe_float(quote.get("annualized_gain")),
        "top_gainer_name": performance.get("top_gainer_name"),
        "top_gainer_symbol": performance.get("top_gainer_symbol"),
        "holdings": holdings,
        "latest_rebalance": {
            "trade_time": latest.get("rebalance_time"),
            "trades": latest_trades,
        },
        "recent_rebalances": recent_rebalances,
        "xueqiu_url": f"https://xueqiu.com/P/{code}",
    }
