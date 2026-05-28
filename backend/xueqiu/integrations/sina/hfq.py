"""新浪财经后复权价（houfuquan 接口）。"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import requests

from xueqiu.domain.codes import to_xueqiu_code

_SINA_HFQ_PATTERN = re.compile(r"_(\d{4})_(\d{2})_(\d{2})\s*:\s*\"?([\d.]+)\"?")
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def xueqiu_to_sina(code: str) -> tuple[str, str]:
    normalized = to_xueqiu_code(code)
    if len(normalized) < 8:
        raise ValueError(f"无效股票代码: {code}")
    return normalized[2:], normalized[:2].lower()


def parse_sina_hfq_series(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for match in _SINA_HFQ_PATTERN.finditer(text):
        year, month, day, price_text = match.groups()
        trade_date = f"{year}{month}{day}"
        try:
            price = float(price_text)
        except ValueError:
            continue
        if price > 0:
            result[trade_date] = price
    return result


def fetch_sina_hfq_series(code: str, *, as_of: date | None = None, timeout: float = 10.0) -> dict[str, float]:
    symbol, market = xueqiu_to_sina(code)
    query_date = (as_of or date.today()).strftime("%Y-%m-%d")
    url = (
        f"http://finance.sina.com.cn/realstock/company/{market}{symbol}/houfuquan.js"
        f"?d={query_date}"
    )
    resp = requests.get(url, headers=_SINA_HEADERS, timeout=timeout)
    resp.raise_for_status()
    series = parse_sina_hfq_series(resp.text)
    if not series:
        raise RuntimeError(f"新浪后复权数据为空: {code}")
    return series


def fetch_latest_hfq(code: str, *, timeout: float = 10.0) -> tuple[str, float]:
    series = fetch_sina_hfq_series(code, timeout=timeout)
    latest_date = max(series.keys())
    return latest_date, series[latest_date]


def build_hfq_quote_rows(
    code: str,
    *,
    trade_dates: set[str] | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    normalized = to_xueqiu_code(code)
    series = fetch_sina_hfq_series(normalized, timeout=timeout)
    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()

    if trade_dates:
        for trade_date in sorted(trade_dates):
            price = series.get(trade_date)
            if price is None:
                continue
            rows.append({"ts_code": normalized, "trade_date": trade_date, "close_hfq": price})
            seen_dates.add(trade_date)

    latest_date = max(series.keys())
    if latest_date not in seen_dates:
        rows.append({"ts_code": normalized, "trade_date": latest_date, "close_hfq": series[latest_date]})

    return rows


def fetch_latest_hfq_batch(
    codes: set[str] | list[str],
    trade_dates_by_code: dict[str, set[str]] | None = None,
    *,
    sleep_seconds: float = 0.15,
    log: Any | None = print,
    cancel_check: Any | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sorted_codes = sorted({to_xueqiu_code(c) for c in codes})
    total = len(sorted_codes)
    trade_dates_by_code = trade_dates_by_code or {}

    for index, code in enumerate(sorted_codes, start=1):
        if cancel_check is not None and cancel_check():
            if log:
                log(f"  已停止（完成 {index - 1}/{total} 只）")
            break
        try:
            code_rows = build_hfq_quote_rows(code, trade_dates=trade_dates_by_code.get(code))
            rows.extend(code_rows)
            latest = max((r["trade_date"] for r in code_rows), default="")
            latest_price = next((r["close_hfq"] for r in code_rows if r["trade_date"] == latest), None)
            if log and latest_price is not None:
                log(f"  [{index}/{total}] {code} 最新 {latest} 后复权 {latest_price:.4f}（写入 {len(code_rows)} 点）")
        except Exception as exc:
            if log:
                log(f"  [{index}/{total}] {code} 失败: {exc}")
        if index < total and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return rows
