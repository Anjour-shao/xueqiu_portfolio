"""新浪指数日线（用于 benchmark 对比）。"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import requests

_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_MAX_DATALEN = 1023


def benchmark_to_sina_symbol(ts_code: str) -> str:
    code = ts_code.strip().upper()
    if "." in code:
        num, exchange = code.split(".", 1)
        return f"{exchange.lower()}{num}"
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return f"{code[:2].lower()}{code[2:]}"
    return f"sh{code}"


def _day_to_yyyymmdd(day: str) -> str:
    return day[:10].replace("-", "")


def parse_sina_kline_payload(text: str) -> list[dict[str, Any]]:
    """解析新浪 K 线：支持直出 JSON 数组或 jsonp 包装。"""
    stripped = text.strip()
    if not stripped:
        return []

    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
            return payload if isinstance(payload, list) else []
        except json.JSONDecodeError:
            pass

    match = re.search(r"\(\s*(\[.*\])\s*\)\s*;?\s*$", stripped, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            return payload if isinstance(payload, list) else []
        except json.JSONDecodeError:
            return []

    return []


def parse_sina_kline(text: str) -> list[dict[str, Any]]:
    """兼容旧调用名。"""
    return parse_sina_kline_payload(text)


def rows_to_closes(rows: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        day = str(row.get("day", ""))[:10]
        close_raw = row.get("close")
        if not day or close_raw is None:
            continue
        try:
            close = float(close_raw)
        except (TypeError, ValueError):
            continue
        if close > 0:
            result[_day_to_yyyymmdd(day)] = close
    return result


def _fetch_url(url: str, *, timeout: float) -> str:
    resp = requests.get(url, headers=_SINA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def _try_provider(
    name: str,
    fetch_fn: Callable[[int], str],
    *,
    datalen: int,
    timeout: float,
) -> tuple[dict[str, float], str | None]:
    try:
        text = fetch_fn(datalen)
        rows = parse_sina_kline_payload(text)
        closes = rows_to_closes(rows)
        if closes:
            return closes, None
        return {}, f"{name}: 解析后无有效收盘"
    except Exception as exc:
        return {}, f"{name}: {exc}"


def _merge_closes(target: dict[str, float], chunk: dict[str, float]) -> int:
    added = 0
    for day, price in chunk.items():
        if day not in target:
            added += 1
        target[day] = price
    return added


def fetch_index_closes_chunk(
    ts_code: str,
    *,
    datalen: int = _MAX_DATALEN,
    timeout: float = 15.0,
) -> dict[str, float]:
    """单次请求（兼容旧接口）。"""
    return fetch_index_closes_robust(ts_code, start_date="19700101", end_date="20991231", timeout=timeout)


def fetch_index_closes(
    ts_code: str,
    *,
    datalen: int = 2000,
    timeout: float = 15.0,
) -> dict[str, float]:
    """兼容旧接口：按 datalen 拉取最近 N 根日 K。"""
    symbol = benchmark_to_sina_symbol(ts_code)
    errors: list[str] = []

    def quotes_json_v2(length: int) -> str:
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
            f"?symbol={symbol}&scale=240&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    def money_json_v2(length: int) -> str:
        url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    def quotes_jsonp_v2(length: int) -> str:
        millis = int(time.time() * 1000)
        var_name = f"_{symbol}_240_{millis}"
        url = (
            "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
            f"var%20{var_name}=/CN_MarketDataService.getKLineData"
            f"?symbol={symbol}&scale=240&ma=no&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    length = min(max(datalen, 1), _MAX_DATALEN)
    for name, fn in (
        ("quotes_json_v2", quotes_json_v2),
        ("money_json_v2", money_json_v2),
        ("quotes_jsonp_v2", quotes_jsonp_v2),
    ):
        closes, err = _try_provider(name, fn, datalen=length, timeout=timeout)
        if closes:
            return closes
        if err:
            errors.append(err)

    detail = "; ".join(errors) if errors else "未知错误"
    raise RuntimeError(f"新浪指数数据为空: {ts_code} ({symbol}) — {detail}")


def fetch_index_closes_robust(
    ts_code: str,
    *,
    start_date: str,
    end_date: str,
    timeout: float = 15.0,
    sleep_seconds: float = 0.12,
) -> dict[str, float]:
    """多新浪接口 + 分页，覆盖 [start_date, end_date]。"""
    symbol = benchmark_to_sina_symbol(ts_code)
    errors: list[str] = []
    merged: dict[str, float] = {}

    def quotes_json_v2(length: int) -> str:
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
            f"?symbol={symbol}&scale=240&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    def money_json_v2(length: int) -> str:
        url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    def quotes_jsonp_v2(length: int) -> str:
        millis = int(time.time() * 1000)
        var_name = f"_{symbol}_240_{millis}"
        url = (
            "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
            f"var%20{var_name}=/CN_MarketDataService.getKLineData"
            f"?symbol={symbol}&scale=240&ma=no&datalen={length}"
        )
        return _fetch_url(url, timeout=timeout)

    providers: list[tuple[str, Callable[[int], str]]] = [
        ("quotes_json_v2", quotes_json_v2),
        ("money_json_v2", money_json_v2),
        ("quotes_jsonp_v2", quotes_jsonp_v2),
    ]

    working_fetch: Callable[[int], str] | None = None
    for name, fn in providers:
        chunk, err = _try_provider(name, fn, datalen=_MAX_DATALEN, timeout=timeout)
        if chunk:
            working_fetch = fn
            _merge_closes(merged, chunk)
            break
        if err:
            errors.append(err)

    if working_fetch is None:
        detail = "; ".join(errors) if errors else "未知错误"
        raise RuntimeError(f"新浪指数数据为空: {ts_code} ({symbol}) — {detail}")

    stagnant_rounds = 0
    while merged and min(merged.keys()) > start_date and stagnant_rounds < 2:
        prev_count = len(merged)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        chunk, _ = _try_provider("paginate", working_fetch, datalen=_MAX_DATALEN, timeout=timeout)
        if not chunk:
            stagnant_rounds += 1
            continue
        added = _merge_closes(merged, chunk)
        if added == 0 or len(merged) == prev_count:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

    filtered = filter_closes_by_range(merged, start_date, end_date)
    if not filtered:
        raise RuntimeError(
            f"新浪指数在区间 {start_date}~{end_date} 无数据: {ts_code} ({symbol})，"
            f"已合并 {len(merged)} 个交易日"
        )
    return filtered


def filter_closes_by_range(closes: dict[str, float], start_date: str, end_date: str) -> dict[str, float]:
    return {d: p for d, p in closes.items() if start_date <= d <= end_date}
