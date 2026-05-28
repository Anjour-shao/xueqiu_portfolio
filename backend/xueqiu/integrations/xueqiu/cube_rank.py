"""雪球组合发现页榜单 API（与 /p/discover 页同源 JSON）。

网页: https://xueqiu.com/p/discover?first_name=5&second_name=1
接口: GET /cubes/discover/rank/cube/list.json

服务端硬上限（实测）:
- category=14 热门: totalCount=100
- category=12 + profit: 各榜 totalCount=10
- 多榜去重后约 120~130 条，无法通过加大 count/翻页突破
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Any

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError

RANK_LIST_URL = "https://xueqiu.com/cubes/discover/rank/cube/list.json"

_ZH_RE = re.compile(r"^ZH\d{4,8}$", re.IGNORECASE)


@dataclass(frozen=True)
class RankSource:
    label: str
    params: dict[str, Any]
    paginate: bool = True
    optional: bool = False
    page_size: int = 50


# A 股发现页榜单（不含港美股；周/总收益参数多已 400）
CN_RANK_SOURCES: tuple[RankSource, ...] = (
    RankSource("热门榜", {"category": 14}, paginate=True, page_size=50),
    RankSource(
        "年收益榜",
        {"category": 12, "market": "cn", "profit": "annualized_gain_rate"},
        paginate=False,
        page_size=50,
    ),
    RankSource(
        "月收益榜",
        {"category": 12, "market": "cn", "profit": "monthly_gain"},
        paginate=False,
        page_size=50,
    ),
    RankSource(
        "日收益榜",
        {"category": 12, "market": "cn", "profit": "daily_gain"},
        paginate=False,
        page_size=50,
    ),
    RankSource(
        "周收益榜",
        {"category": 12, "market": "cn", "profit": "weekly_gain"},
        paginate=False,
        optional=True,
        page_size=50,
    ),
    RankSource(
        "总收益榜",
        {"category": 12, "market": "cn", "profit": "total_gain"},
        paginate=False,
        optional=True,
        page_size=50,
    ),
)


def normalize_symbol(raw: Any) -> str | None:
    if not raw:
        return None
    sym = str(raw).strip().upper()
    return sym if _ZH_RE.match(sym) else None


def parse_rank_item(item: dict[str, Any]) -> tuple[str, str] | None:
    sym = normalize_symbol(item.get("symbol") or item.get("code"))
    if not sym:
        return None
    name = str(item.get("name") or item.get("cube_name") or sym).strip()
    return sym, name


def fetch_rank_page(
    client: XueQiuApiClient,
    base_params: dict[str, Any],
    *,
    page: int,
    count: int,
    max_retries: int = 3,
) -> dict[str, Any]:
    params = {**base_params, "page": page, "count": count}
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            data = client.get_json(RANK_LIST_URL, params=params)
            if isinstance(data, dict):
                return data
            raise XueQiuApiError(f"榜单响应非 dict: {type(data)}")
        except XueQiuApiError as exc:
            last_err = exc
            if attempt + 1 >= max_retries:
                raise
            time.sleep(1.2 * (attempt + 1) + random.uniform(0.3, 0.8))
    raise last_err or XueQiuApiError("榜单请求失败")


def fetch_rank_source(
    client: XueQiuApiClient,
    source: RankSource,
    *,
    sleep_range: tuple[float, float] = (0.7, 1.4),
) -> list[tuple[str, str]]:
    """拉取单个榜单全部条目。"""
    seen: set[str] = set()
    rows: list[tuple[str, str]] = []
    page = 1
    max_pages = 1

    while page <= max_pages:
        data = fetch_rank_page(
            client,
            source.params,
            page=page,
            count=source.page_size,
        )
        lst = data.get("list") or []
        if not isinstance(lst, list):
            lst = []

        if page == 1:
            total = int(data.get("totalCount") or 0)
            api_max_page = int(data.get("maxPage") or 1)
            if source.paginate and total > 0:
                max_pages = max(1, api_max_page)
            else:
                max_pages = 1

        for raw in lst:
            if not isinstance(raw, dict):
                continue
            parsed = parse_rank_item(raw)
            if not parsed:
                continue
            sym, name = parsed
            if sym in seen:
                continue
            seen.add(sym)
            rows.append((sym, name))

        if not lst or page >= max_pages:
            break
        page += 1
        time.sleep(random.uniform(*sleep_range))

    return rows


def fetch_all_cn_rank_cubes(
    client: XueQiuApiClient | None = None,
    *,
    sources: tuple[RankSource, ...] = CN_RANK_SOURCES,
    sleep_between_sources: tuple[float, float] = (0.8, 1.6),
    on_source_done: Any | None = None,
    cancel_event: Any | None = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """合并多榜去重。返回 (code->name, 成功榜名, 跳过榜名)。"""
    api = client or XueQiuApiClient()
    merged: dict[str, str] = {}
    ok_labels: list[str] = []
    skipped: list[str] = []

    for source in sources:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            from xueqiu.sync.sync_cancel import SyncCancelled

            raise SyncCancelled()
        try:
            rows = fetch_rank_source(api, source)
            for sym, name in rows:
                merged.setdefault(sym, name)
            ok_labels.append(f"{source.label}({len(rows)})")
            if on_source_done:
                on_source_done(source.label, len(rows), None)
        except XueQiuApiError as exc:
            if source.optional:
                skipped.append(f"{source.label}: {exc}")
                if on_source_done:
                    on_source_done(source.label, 0, str(exc))
            else:
                raise
        time.sleep(random.uniform(*sleep_between_sources))

    return merged, ok_labels, skipped
