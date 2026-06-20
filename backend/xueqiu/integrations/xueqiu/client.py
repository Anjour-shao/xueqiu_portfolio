from __future__ import annotations

import random
import re
import time
from typing import Any

import requests
from requests.exceptions import RequestException

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


def _parse_xueqiu_error_body(resp: requests.Response) -> tuple[str | None, str | None]:
    try:
        payload = resp.json()
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    code = str(payload.get("error_code") or "").strip() or None
    desc = str(payload.get("error_description") or "").strip() or None
    return code, desc


def _cookie_invalid_message(*, error_code: str | None, error_desc: str | None, sym_hint: str) -> str | None:
    if error_code == "400016":
        return (
            f"雪球 Cookie 已失效（error_code={error_code}），"
            f"请运行 python ../scripts/xueqiu_login.py 重新登录后再试{sym_hint}"
        )
    if error_desc and "重新登录" in error_desc:
        return (
            f"雪球登录态失效：{error_desc}。"
            f"请运行 python ../scripts/xueqiu_login.py 重新登录{sym_hint}"
        )
    return None


def _is_cookie_invalid_error(exc: XueQiuApiError) -> bool:
    msg = str(exc)
    return "Cookie 已失效" in msg or "登录态失效" in msg or "400016" in msg or "重新登录" in msg


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, RequestException):
        return True
    if isinstance(exc, XueQiuApiError):
        if _is_cookie_invalid_error(exc):
            return False
        msg = str(exc)
        if any(code in msg for code in ("10022", "10020", "error_code=10022", "error_code=10020")):
            return False
        return any(token in msg for token in ("400", "429", "502", "503", "网络请求失败"))
    return False


def _network_error_message(exc: RequestException, *, sym_hint: str = "") -> str:
    return f"网络请求失败{sym_hint}: {exc}"


class XueQiuApiClient:
    def __init__(self, cookie: str | None = None, *, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
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

    def _http_get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        sym_hint: str = "",
    ) -> requests.Response:
        try:
            return self.session.get(
                url, params=params, headers=headers, timeout=self.timeout
            )
        except RequestException as exc:
            raise XueQiuApiError(_network_error_message(exc, sym_hint=sym_hint)) from exc

    def warm_up(self) -> None:
        if self._warmed:
            return
        resp = self._http_get("https://xueqiu.com/")
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
        symbol = (params or {}).get("cube_symbol", "")
        sym_hint = f" {symbol}" if symbol else ""
        if warm_symbol:
            sym = warm_symbol.strip().upper()
            try:
                warm_path = f"P/{sym}" if sym.startswith("ZH") else f"S/{sym}"
                self._http_get(f"https://xueqiu.com/{warm_path}", sym_hint=f" {sym}")
            except XueQiuApiError:
                pass
        extra_headers = {"Referer": referer} if referer else None
        resp = self._http_get(url, params=params, headers=extra_headers, sym_hint=sym_hint)
        sc = resp.status_code
        if sc in {401, 403}:
            raise XueQiuApiError(f"认证失败 ({sc})，请重新导出 Cookie")
        if sc == 429:
            raise XueQiuApiError(f"HTTP 429 限流{sym_hint}")
        if sc == 400:
            err_code, err_desc = _parse_xueqiu_error_body(resp)
            cookie_msg = _cookie_invalid_message(
                error_code=err_code,
                error_desc=err_desc,
                sym_hint=sym_hint,
            )
            if cookie_msg:
                raise XueQiuApiError(cookie_msg)
            code_part = f" error_code={err_code}" if err_code else ""
            desc_part = f" {err_desc}" if err_desc else ""
            raise XueQiuApiError(
                f"HTTP 400{code_part}{sym_hint}{desc_part}".strip()
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
        """雪球 400/429/502/503 及网络抖动时退避重试。"""
        last: BaseException | None = None
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
                if not _is_retryable_error(exc):
                    raise
                if attempt < max_retries - 1:
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
