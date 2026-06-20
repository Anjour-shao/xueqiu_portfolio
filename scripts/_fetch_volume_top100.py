"""拉取指定交易日 A 股成交额 Top100，打印 xueqiu 代码列表。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TRADE_DATE = "20260605"


def via_tushare() -> list[str]:
    from xueqiu.config import TUSHARE_API_KEY, TUSHARE_HTTP_URL
    from xueqiu.domain.codes import to_xueqiu_code

    if not TUSHARE_API_KEY:
        return []
    try:
        import tushare as ts

        pro = ts.pro_api(TUSHARE_API_KEY)
        if TUSHARE_HTTP_URL:
            pro._DataApi__http_url = TUSHARE_HTTP_URL
        df = pro.daily(trade_date=TRADE_DATE)
    except Exception as exc:
        print(f"tushare failed: {exc}", file=sys.stderr)
        return []
    if df is None or df.empty:
        return []
    top = df.sort_values("amount", ascending=False).head(100)
    return [to_xueqiu_code(str(row.ts_code)) for row in top.itertuples()]


def via_eastmoney(trade_date: str) -> list[str]:
    """东方财富 datacenter：指定交易日成交额 Top100。"""
    import requests
    from xueqiu.domain.codes import to_xueqiu_code

    iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "TURNOVER_VALUE",
        "sortTypes": -1,
        "pageSize": 100,
        "pageNumber": 1,
        "reportName": "RPT_STOCK_DAILY_TRADE",
        "columns": "SECUCODE,TURNOVER_VALUE,TRADE_DATE",
        "filter": f"(TRADE_DATE='{iso}')",
        "source": "WEB",
        "client": "WEB",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "eastmoney datacenter failed")
    rows = (payload.get("result") or {}).get("data") or []
    out: list[str] = []
    for row in rows:
        secu = str(row.get("SECUCODE") or "")
        if "." not in secu:
            continue
        sym, ex = secu.split(".", 1)
        out.append(to_xueqiu_code(f"{sym}.{ex}"))
    return out[:100]


def via_akshare(trade_date: str) -> list[str]:
    """AkShare 全市场日线聚合（无 token 时 fallback，较慢）。"""
    import akshare as ak
    import pandas as pd
    from xueqiu.domain.codes import to_xueqiu_code

    iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    spot = ak.stock_zh_a_spot_em()
    codes = spot["代码"].astype(str).str.zfill(6).tolist()
    rows: list[tuple[float, str]] = []
    for code in codes:
        ex = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        if code.startswith(("4", "8")):
            ex = "BJ"
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=iso.replace("-", ""),
                end_date=iso.replace("-", ""),
                adjust="",
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        amount = float(df.iloc[-1].get("成交额") or 0)
        if amount > 0:
            rows.append((amount, to_xueqiu_code(f"{code}.{ex}")))
    rows.sort(key=lambda x: -x[0])
    return [c for _, c in rows[:100]]


def main() -> None:
    codes = via_tushare()
    source = "tushare"
    if len(codes) < 100:
        try:
            codes = via_eastmoney(TRADE_DATE)
            source = "eastmoney"
        except Exception as exc:
            print("eastmoney failed:", exc, file=sys.stderr)
    if len(codes) < 100:
        try:
            codes = via_akshare(TRADE_DATE)
            source = "akshare"
        except Exception as exc:
            print("akshare failed:", exc, file=sys.stderr)
    print(json.dumps({"trade_date": TRADE_DATE, "source": source, "count": len(codes), "symbols": codes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
