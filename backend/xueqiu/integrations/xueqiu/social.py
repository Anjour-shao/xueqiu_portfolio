"""雪球社交扩展 API：关注链、个股活跃用户（尚未接入挖组合主流程）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError

FOLLOWING_URL = "https://xueqiu.com/friendships/groups/members.json"
STOCK_HOT_USER_URL = "https://xueqiu.com/recommend/user/stock_hot_user.json"


@dataclass(frozen=True)
class XueQiuUserBrief:
    uid: int
    screen_name: str
    followers_count: int
    friends_count: int
    verified_type: int
    allow_all_stock: bool | None
    description: str


def _parse_user(raw: dict[str, Any]) -> XueQiuUserBrief | None:
    uid = raw.get("id")
    if uid is None:
        return None
    allow = raw.get("allow_all_stock")
    return XueQiuUserBrief(
        uid=int(uid),
        screen_name=str(raw.get("screen_name") or raw.get("name") or "").strip(),
        followers_count=int(raw.get("followers_count") or 0),
        friends_count=int(raw.get("friends_count") or 0),
        verified_type=int(raw.get("verified_type") or 0),
        allow_all_stock=bool(allow) if allow is not None else None,
        description=str(raw.get("description") or "").strip(),
    )


def _extract_users(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("users", "followers", "friends", "list", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def fetch_user_following_page(
    user_id: int | str,
    *,
    page: int = 1,
    gid: int = 0,
    client: XueQiuApiClient | None = None,
) -> tuple[list[XueQiuUserBrief], int | None]:
    """拉取用户关注列表的一页。"""
    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    data = api.get_json_with_retry(
        FOLLOWING_URL,
        params={"uid": uid, "page": page, "gid": gid},
        referer=f"https://xueqiu.com/u/{uid}",
        max_retries=3,
    )
    raw_users = _extract_users(data)
    users = [u for u in (_parse_user(x) for x in raw_users) if u is not None]
    max_page = None
    if isinstance(data, dict):
        for key in ("maxPage", "max_page", "totalPages"):
            val = data.get(key)
            if isinstance(val, int) and val > 0:
                max_page = val
                break
    return users, max_page


def fetch_stock_hot_users(
    symbol: str,
    *,
    start: int = 0,
    count: int = 8,
    client: XueQiuApiClient | None = None,
) -> list[XueQiuUserBrief]:
    """某只股票讨论区的推荐/活跃用户。"""
    sym = str(symbol).strip().upper()
    api = client or XueQiuApiClient()
    data = api.get_json_with_retry(
        STOCK_HOT_USER_URL,
        params={"symbol": sym, "start": start, "count": count},
        referer=f"https://xueqiu.com/S/{sym}",
        max_retries=3,
    )
    raw_users = _extract_users(data)
    return [u for u in (_parse_user(x) for x in raw_users) if u is not None]


def probe_watchlist_yield(
    user_id: int | str,
    *,
    client: XueQiuApiClient | None = None,
) -> tuple[int, int]:
    """返回 (自选组合数, meta 自选数)。"""
    from xueqiu.integrations.xueqiu.watchlist import (
        fetch_user_cube_watchlist_meta_count,
        fetch_user_watchlist_cubes,
    )

    uid = str(user_id).strip()
    api = client or XueQiuApiClient()
    watchlist = fetch_user_watchlist_cubes(uid, client=api)
    meta = fetch_user_cube_watchlist_meta_count(uid, client=api)
    return len(watchlist), meta
