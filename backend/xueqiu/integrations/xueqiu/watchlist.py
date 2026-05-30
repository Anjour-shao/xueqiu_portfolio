"""雪球用户自选组合与组合元数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import CUBE_SHOW_URL, validate_portfolio_id

_ZH_RE = re.compile(r"^ZH\d{4,8}$", re.IGNORECASE)

WATCHLIST_URL = "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json"
WATCHLIST_PID_ALL = -120


@dataclass(frozen=True)
class CubeShowInfo:
    account_code: str
    account_name: str
    owner_uid: int | None
    owner_name: str | None
    market: str | None


def normalize_symbol(raw: Any) -> str | None:
    if not raw:
        return None
    sym = str(raw).strip().upper()
    return sym if _ZH_RE.match(sym) else None


def fetch_user_watchlist_cubes(
    user_id: int | str,
    client: XueQiuApiClient | None = None,
    *,
    size: int = 200,
) -> list[tuple[str, str]]:
    """返回 (ZH代码, 名称) 列表。"""
    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    referer = f"https://xueqiu.com/u/{uid}"
    data = api.get_json_with_retry(
        WATCHLIST_URL,
        params={
            "uid": uid,
            "category": 3,
            "pid": WATCHLIST_PID_ALL,
            "size": size,
        },
        referer=referer,
        max_retries=4,
    )
    payload = data.get("data") if isinstance(data, dict) else None
    stocks = payload.get("stocks") if isinstance(payload, dict) else None
    if not isinstance(stocks, list):
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in stocks:
        if not isinstance(item, dict):
            continue
        sym = normalize_symbol(item.get("symbol") or item.get("code"))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        name = str(item.get("name") or sym).strip()
        out.append((sym, name))
    return out


def _parse_owner(data: dict[str, Any]) -> tuple[int | None, str | None]:
    owner = data.get("owner")
    if isinstance(owner, dict):
        uid = owner.get("id")
        name = owner.get("screen_name") or owner.get("name")
        if uid is not None:
            return int(uid), str(name).strip() if name else None
    raw_uid = data.get("owner_id")
    if raw_uid is not None:
        return int(raw_uid), None
    return None, None


def fetch_cube_show(
    portfolio_id: str,
    client: XueQiuApiClient | None = None,
    *,
    max_retries: int = 5,
) -> CubeShowInfo:
    code = validate_portfolio_id(portfolio_id)
    api = client or XueQiuApiClient()
    referer = f"https://xueqiu.com/P/{code}"
    data = api.get_json_with_retry(
        CUBE_SHOW_URL,
        params={"symbol": code},
        referer=referer,
        warm_symbol=code,
        max_retries=max_retries,
        delay=(1.5, 2.8),
    )
    if not isinstance(data, dict):
        raise XueQiuApiError(f"组合 {code} show 响应无效")
    owner_uid, owner_name = _parse_owner(data)
    if owner_name is None and owner_uid is not None:
        owner_block = data.get("owner")
        if isinstance(owner_block, dict):
            owner_name = str(owner_block.get("screen_name") or "").strip() or None
    return CubeShowInfo(
        account_code=code,
        account_name=str(data.get("name") or code).strip(),
        owner_uid=owner_uid,
        owner_name=owner_name,
        market=str(data.get("market") or "").strip() or None,
    )
