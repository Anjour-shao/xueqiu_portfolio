"""A 股代码 → 名称（TuShare / 雪球 batch quote）。"""

from __future__ import annotations

import time
from typing import Iterable

from xueqiu.domain.codes import to_tushare_code, to_xueqiu_code
from xueqiu.integrations.xueqiu.client import XueQiuApiClient

STOCK_BATCH_QUOTE_URL = "https://stock.xueqiu.com/v5/stock/batch/quote.json"
_BATCH_SIZE = 40


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = to_xueqiu_code(str(raw).strip().upper())
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _via_tushare(symbols: list[str]) -> dict[str, str]:
    from xueqiu.config import TUSHARE_API_KEY, TUSHARE_HTTP_URL

    if not TUSHARE_API_KEY or not symbols:
        return {}
    try:
        import tushare as ts

        pro = ts.pro_api(TUSHARE_API_KEY)
        if TUSHARE_HTTP_URL:
            pro._DataApi__http_url = TUSHARE_HTTP_URL
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    want = {to_tushare_code(s) for s in symbols}
    out: dict[str, str] = {}
    for row in df.itertuples():
        ts_code = str(row.ts_code)
        if ts_code not in want:
            continue
        name = str(getattr(row, "name", "") or "").strip()
        if name:
            out[to_xueqiu_code(ts_code)] = name
    return out


def _via_xueqiu_batch(symbols: list[str], *, client: XueQiuApiClient | None = None) -> dict[str, str]:
    if not symbols:
        return {}
    api = client or XueQiuApiClient()
    out: dict[str, str] = {}
    for i in range(0, len(symbols), _BATCH_SIZE):
        batch = symbols[i : i + _BATCH_SIZE]
        sym_param = ",".join(batch)
        try:
            data = api.get_json_with_retry(
                STOCK_BATCH_QUOTE_URL,
                params={"symbol": sym_param, "extend": "detail"},
                referer="https://xueqiu.com/",
                max_retries=3,
            )
        except Exception:
            time.sleep(0.3)
            continue
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else item
            sym = to_xueqiu_code(str(quote.get("symbol") or quote.get("code") or ""))
            name = str(quote.get("name") or "").strip()
            if sym and name:
                out[sym] = name
        time.sleep(0.2)
    return out


def resolve_a_share_names(
    symbols: Iterable[str],
    *,
    client: XueQiuApiClient | None = None,
) -> dict[str, str]:
    """返回 {SZ300308: 中际旭创, ...}，未解析到的 symbol 不在 map 中。"""
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return {}
    names = _via_tushare(normalized)
    missing = [s for s in normalized if s not in names]
    if missing:
        names.update(_via_xueqiu_batch(missing, client=client))
    return names
