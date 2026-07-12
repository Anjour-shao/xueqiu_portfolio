from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, strip_html

# 讨论区须走 api 子域；主站 xueqiu.com 同路径会触发阿里云 WAF（返回 HTML）
STOCK_STATUS_URL = "https://api.xueqiu.com/query/v1/symbol/search/status"
USER_TIMELINE_URL = "https://api.xueqiu.com/v4/statuses/user_timeline.json"
USER_SEARCH_URL = "https://xueqiu.com/query/v1/search/user.json"
STATUS_DETAIL_URL = "https://api.xueqiu.com/statuses/show.json"
STATUS_COMMENTS_URL = "https://api.xueqiu.com/statuses/comments.json"


@dataclass
class XueQiuPost:
    id: int
    created_at: str
    text: str
    user_id: int | None
    user_name: str
    retweet_count: int = 0
    reply_count: int = 0
    like_count: int = 0
    source: str = ""
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _format_created_at(raw: Any) -> str:
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw / 1000).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(raw, str) and raw:
        return raw
    return ""


def parse_post(raw: dict[str, Any]) -> XueQiuPost | None:
    post_id = raw.get("id")
    if post_id is None:
        return None
    user = raw.get("user") or {}
    # 评论数据中 user_id / screen_name 在顶层而非嵌套在 user 中
    uid = user.get("id") or raw.get("user_id")
    uname = str(
        user.get("screen_name")
        or user.get("name")
        or raw.get("screen_name")
        or raw.get("user_name")
        or ""
    )
    return XueQiuPost(
        id=int(post_id),
        created_at=_format_created_at(raw.get("created_at") or raw.get("timeBefore")),
        text=strip_html(str(raw.get("text") or raw.get("description") or "")),
        user_id=uid,
        user_name=uname,
        retweet_count=int(raw.get("retweet_count") or 0),
        reply_count=int(raw.get("reply_count") or 0),
        like_count=int(raw.get("like_count") or 0),
        source=str(raw.get("source") or ""),
        target=str(raw.get("target") or ""),
    )


def extract_post_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("list", "statuses", "comments", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _has_more_pages(data: dict[str, Any], page: int, batch_size: int) -> bool:
    max_page = data.get("maxPage") or data.get("max_page")
    if isinstance(max_page, int) and max_page > 0:
        return page < max_page
    if data.get("next_max_id") or data.get("next_id"):
        return True
    if data.get("next_page") is False:
        return False
    return len(extract_post_list(data)) >= batch_size


def fetch_stock_posts_page(
    client: XueQiuApiClient,
    symbol: str,
    *,
    page: int = 1,
    size: int = 20,
    sort: str = "time",
) -> tuple[list[XueQiuPost], bool]:
    symbol = to_xueqiu_code(symbol)
    stock_referer = f"https://xueqiu.com/S/{symbol}"
    data = client.get_json(
        STOCK_STATUS_URL,
        params={
            "symbol": symbol,
            "page": page,
            "size": size,
            "comment": "0",
            "hl": "0",
            "source": "all",
            "sort": sort,
        },
        referer=stock_referer,
    )
    posts = [p for p in (parse_post(item) for item in extract_post_list(data)) if p]
    has_more = isinstance(data, dict) and _has_more_pages(data, page, size)
    return posts, has_more


def fetch_stock_posts(
    client: XueQiuApiClient,
    symbol: str,
    *,
    max_pages: int = 5,
    page_size: int = 20,
    sort: str = "time",
) -> list[XueQiuPost]:
    def fetch_page(page: int) -> tuple[list[Any], bool]:
        posts, has_more = fetch_stock_posts_page(
            client, symbol, page=page, size=page_size, sort=sort
        )
        return posts, has_more

    return client.paginate(fetch_page, max_pages=max_pages)


def fetch_user_timeline_page(
    client: XueQiuApiClient,
    user_id: int | str,
    *,
    page: int = 1,
    count: int = 20,
) -> tuple[list[XueQiuPost], bool]:
    data = client.get_json(
        USER_TIMELINE_URL,
        params={"user_id": str(user_id), "page": page, "count": count},
    )
    posts = [p for p in (parse_post(item) for item in extract_post_list(data)) if p]
    has_more = isinstance(data, dict) and (
        bool(data.get("next_max_id")) or data.get("next_page") is True or _has_more_pages(data, page, count)
    )
    return posts, has_more


def fetch_user_posts(
    client: XueQiuApiClient,
    user_id: int | str,
    *,
    max_pages: int = 10,
    page_size: int = 20,
) -> list[XueQiuPost]:
    def fetch_page(page: int) -> tuple[list[Any], bool]:
        posts, has_more = fetch_user_timeline_page(
            client, user_id, page=page, count=page_size
        )
        return posts, has_more

    return client.paginate(fetch_page, max_pages=max_pages)


def fetch_post_detail(
    client: XueQiuApiClient,
    post_id: int | str,
    *,
    user_id: int | str | None = None,
) -> XueQiuPost | None:
    """获取单篇帖子/文章详情。

    Args:
        client: XueQiuApiClient 实例
        post_id: 帖子 ID（如 URL 中 319291242）
        user_id: 可选，帖子作者的 user_id。传入可提高反爬成功率（设置更精准的 Referer）

    Returns:
        XueQiuPost 对象，若帖子不存在或解析失败则返回 None
    """
    pid = str(post_id).strip()
    referer = f"https://xueqiu.com/{user_id}/{pid}" if user_id else f"https://xueqiu.com/"
    try:
        data = client.get_json(
            STATUS_DETAIL_URL,
            params={"id": pid},
            referer=referer,
        )
    except Exception:
        return None

    if isinstance(data, dict):
        # 帖子数据可能在顶层，也可能嵌套在 data / status / detail 字段中
        post_raw = data
        for key in ("status", "detail", "data"):
            nested = data.get(key)
            if isinstance(nested, dict) and nested.get("id"):
                post_raw = nested
                break
        return parse_post(post_raw)

    return None


def fetch_post_comments_page(
    client: XueQiuApiClient,
    post_id: int | str,
    *,
    page: int = 1,
    count: int = 20,
    user_id: int | str | None = None,
) -> tuple[list[XueQiuPost], bool]:
    """获取帖子评论列表的一页。"""
    pid = str(post_id).strip()
    referer = f"https://xueqiu.com/{user_id}/{pid}" if user_id else "https://xueqiu.com/"
    data = client.get_json(
        STATUS_COMMENTS_URL,
        params={"id": pid, "page": page, "count": count},
        referer=referer,
    )
    posts = [p for p in (parse_post(item) for item in extract_post_list(data)) if p]
    has_more = isinstance(data, dict) and _has_more_pages(data, page, count)
    return posts, has_more


def fetch_post_comments(
    client: XueQiuApiClient,
    post_id: int | str,
    *,
    max_pages: int = 10,
    page_size: int = 20,
    user_id: int | str | None = None,
) -> list[XueQiuPost]:
    """获取帖子的全部评论（分页）。"""
    def fetch_page(page: int) -> tuple[list[Any], bool]:
        posts, has_more = fetch_post_comments_page(
            client, post_id, page=page, count=page_size, user_id=user_id,
        )
        return posts, has_more

    return client.paginate(fetch_page, max_pages=max_pages)


def resolve_user_id(client: XueQiuApiClient, screen_name: str) -> int:
    data = client.get_json(USER_SEARCH_URL, params={"q": screen_name, "count": 10, "page": 1})
    users = extract_post_list(data)
    if not users and isinstance(data, dict):
        users = data.get("users") or []
    for user in users:
        if str(user.get("screen_name", "")).lower() == screen_name.lower():
            uid = user.get("id")
            if uid is not None:
                return int(uid)
    if users:
        uid = users[0].get("id")
        if uid is not None:
            return int(uid)
    raise ValueError(f"未找到用户: {screen_name}")
