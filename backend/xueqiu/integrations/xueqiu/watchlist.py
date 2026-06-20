"""雪球用户自选组合与组合元数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import CUBE_SHOW_URL, validate_portfolio_id

_ZH_RE = re.compile(r"^ZH\d{4,8}$", re.IGNORECASE)

WATCHLIST_URL = "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json"
PORTFOLIO_LIST_URL = "https://stock.xueqiu.com/v5/stock/portfolio/list.json"
CUBES_LIST_URL = "https://xueqiu.com/cubes/list.json"
WATCHLIST_PID_ALL = -120
_MANAGED_GROUP_PID = -8
_CUBE_CATEGORY = 3


@dataclass(frozen=True)
class CubeShowInfo:
    account_code: str
    account_name: str
    owner_uid: int | None
    owner_name: str | None
    market: str | None
    created_at_ms: int | None = None


def normalize_symbol(raw: Any) -> str | None:
    if not raw:
        return None
    sym = str(raw).strip().upper()
    return sym if _ZH_RE.match(sym) else None


def _parse_cube_items(stocks: Any) -> list[tuple[str, str]]:
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


def fetch_user_cube_watchlist_meta_count(
    user_id: int | str,
    client: XueQiuApiClient | None = None,
) -> int:
    """portfolio/list 中「全部」分组的自选组合数量（仅元数据，可能因隐私拿不到明细）。"""
    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    referer = f"https://xueqiu.com/u/{uid}"
    data = api.get_json_with_retry(
        PORTFOLIO_LIST_URL,
        params={"uid": uid, "system": "true"},
        referer=referer,
        max_retries=3,
    )
    payload = data.get("data") if isinstance(data, dict) else None
    cubes = payload.get("cubes") if isinstance(payload, dict) else None
    if not isinstance(cubes, list):
        return 0
    for group in cubes:
        if not isinstance(group, dict):
            continue
        if group.get("id") == WATCHLIST_PID_ALL:
            try:
                return max(0, int(group.get("symbol_count") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _fetch_watchlist_group(
    uid: str,
    *,
    pid: int,
    client: XueQiuApiClient,
    size: int,
) -> list[tuple[str, str]]:
    data = client.get_json_with_retry(
        WATCHLIST_URL,
        params={
            "uid": uid,
            "category": _CUBE_CATEGORY,
            "pid": pid,
            "size": size,
        },
        referer=f"https://xueqiu.com/u/{uid}",
        max_retries=3,
    )
    payload = data.get("data") if isinstance(data, dict) else None
    stocks = payload.get("stocks") if isinstance(payload, dict) else None
    return _parse_cube_items(stocks)


def fetch_user_managed_cube_codes(
    user_id: int | str,
    client: XueQiuApiClient | None = None,
    *,
    page_size: int = 50,
    max_pages: int = 5,
) -> set[str]:
    """用户创建/管理的组合代码集合（cubes/list.json，用于从自选结果中剔除）。"""
    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    referer = f"https://xueqiu.com/u/{uid}"
    codes: set[str] = set()

    for page in range(1, max_pages + 1):
        data = api.get_json_with_retry(
            CUBES_LIST_URL,
            params={"user_id": uid, "page": page, "count": page_size},
            referer=referer,
            max_retries=3,
        )
        items = data.get("list") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = normalize_symbol(item.get("symbol"))
            if sym:
                codes.add(sym)
        try:
            total = int(data.get("totalCount") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= page * page_size:
            break
    return codes


def fetch_user_watchlist_cubes(
    user_id: int | str,
    client: XueQiuApiClient | None = None,
    *,
    size: int = 200,
    exclude_managed: bool = True,
) -> list[tuple[str, str]]:
    """返回用户自选组合 (ZH代码, 名称) 列表；默认剔除其创建/管理的组合。"""
    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    referer = f"https://xueqiu.com/u/{uid}"

    merged: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(batch: list[tuple[str, str]]) -> None:
        for sym, name in batch:
            if sym in seen:
                continue
            seen.add(sym)
            merged.append((sym, name))

    list_data = api.get_json_with_retry(
        PORTFOLIO_LIST_URL,
        params={"uid": uid, "system": "true"},
        referer=referer,
        max_retries=3,
    )
    payload = list_data.get("data") if isinstance(list_data, dict) else None
    groups = payload.get("cubes") if isinstance(payload, dict) else None
    pids: list[int] = [WATCHLIST_PID_ALL]
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            try:
                count = int(group.get("symbol_count") or 0)
                pid = int(group.get("id"))
            except (TypeError, ValueError):
                continue
            if pid == _MANAGED_GROUP_PID:
                continue
            if count > 0 and pid not in pids:
                pids.append(pid)

    for pid in pids:
        _add(_fetch_watchlist_group(uid, pid=pid, client=api, size=size))

    if exclude_managed and merged:
        managed = fetch_user_managed_cube_codes(uid, client=api)
        if managed:
            merged = [(sym, name) for sym, name in merged if sym not in managed]

    return merged


def _parse_owner(data: dict[str, Any]) -> tuple[int | None, str | None]:
    owner = data.get("owner")
    if isinstance(owner, dict):
        owner_uid = owner.get("id")
        name = owner.get("screen_name") or owner.get("name")
        if owner_uid is not None:
            return int(owner_uid), str(name).strip() if name else None
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
    created_at_ms: int | None = None
    raw_created = data.get("created_at")
    if raw_created is not None:
        try:
            created_at_ms = int(raw_created)
        except (TypeError, ValueError):
            created_at_ms = None
    return CubeShowInfo(
        account_code=code,
        account_name=str(data.get("name") or code).strip(),
        owner_uid=owner_uid,
        owner_name=owner_name,
        market=str(data.get("market") or "").strip() or None,
        created_at_ms=created_at_ms,
    )
