from __future__ import annotations

import random
import re
import time
from typing import Any

import requests

from xueqiu.integrations.xueqiu.auth import load_cookie

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://xueqiu.com/",
}


class XueQiuApiError(RuntimeError):
    pass


class XueQiuApiClient:
    def __init__(self, cookie: str | None = None, *, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.cookies.update(self._parse_cookie(cookie or load_cookie()))
        self._warmed = False

    @staticmethod
    def _parse_cookie(cookie_str: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            result[name.strip()] = value.strip()
        return result

    def warm_up(self) -> None:
        if self._warmed:
            return
        resp = self.session.get("https://xueqiu.com/", timeout=self.timeout)
        resp.raise_for_status()
        self._warmed = True

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
        warm_symbol: str | None = None,
    ) -> Any:
        self.warm_up()
        if warm_symbol:
            sym = warm_symbol.strip().upper()
            self.session.get(f"https://xueqiu.com/S/{sym}", timeout=self.timeout)
        extra_headers = {"Referer": referer} if referer else None
        resp = self.session.get(
            url, params=params, headers=extra_headers, timeout=self.timeout
        )
        sc = resp.status_code
        symbol = (params or {}).get("cube_symbol", "")
        sym_hint = f" {symbol}" if symbol else ""
        if sc in {401, 403}:
            raise XueQiuApiError(f"认证失败 ({sc})，请重新导出 Cookie")
        if sc == 429:
            raise XueQiuApiError(f"HTTP 429 限流{sym_hint}")
        if sc == 400:
            raise XueQiuApiError(
                f"HTTP 400（多为请求过快或短暂限流，网页上组合仍可能存在）{sym_hint}"
            )
        if sc in {502, 503}:
            raise XueQiuApiError(f"HTTP {sc} 服务暂不可用{sym_hint}")
        if sc >= 400:
            raise XueQiuApiError(f"HTTP {sc}{sym_hint}: {resp.text[:160]}")
        text = resp.text.strip()
        if not text:
            raise XueQiuApiError(f"空响应: {url}")
        if text.startswith("<"):
            raise XueQiuApiError("返回 HTML 而非 JSON，Cookie 可能已失效或被反爬拦截")
        try:
            return resp.json()
        except ValueError as exc:
            raise XueQiuApiError(f"JSON 解析失败: {text[:200]}") from exc

    def get_json_with_retry(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
        warm_symbol: str | None = None,
        max_retries: int = 5,
        delay: tuple[float, float] = (1.4, 2.6),
    ) -> Any:
        """雪球 400/429/502/503 时退避重试。"""
        last: XueQiuApiError | None = None
        for attempt in range(max_retries):
            try:
                return self.get_json(
                    url,
                    params=params,
                    referer=referer,
                    warm_symbol=warm_symbol,
                )
            except XueQiuApiError as exc:
                last = exc
                msg = str(exc)
                if attempt < max_retries - 1 and any(
                    token in msg for token in ("400", "429", "502", "503")
                ):
                    time.sleep(random.uniform(*delay) * (attempt + 1))
                    continue
                raise
        if last is not None:
            raise last
        raise XueQiuApiError("雪球请求失败")

    def paginate(
        self,
        fetch_page,
        *,
        start_page: int = 1,
        max_pages: int = 10,
        delay: tuple[float, float] = (0.8, 1.6),
    ) -> list[Any]:
        items: list[Any] = []
        for page in range(start_page, start_page + max_pages):
            batch, has_more = fetch_page(page)
            items.extend(batch)
            if not has_more or not batch:
                break
            time.sleep(random.uniform(*delay))
        return items


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", text or "")
    return cleaned.replace("&nbsp;", " ").strip()
