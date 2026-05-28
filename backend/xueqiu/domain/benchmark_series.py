"""基准指数序列：收盘、日涨跌、累计收益。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from xueqiu.config import BENCHMARK_TS_CODE
from xueqiu.storage.db import benchmark_table, get_conn


def enrich_benchmark_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: str(item["trade_date"]))
    prev_close: float | None = None
    base_close: float | None = None
    for row in ordered:
        close = float(row["close"])
        if base_close is None:
            base_close = close
        pct_chg = None
        if prev_close is not None and prev_close > 0:
            pct_chg = round((close / prev_close - 1.0) * 100, 4)
        cum_return_pct = round((close / base_close - 1.0) * 100, 2) if base_close and base_close > 0 else 0.0
        row["pct_chg"] = pct_chg
        row["cum_return_pct"] = cum_return_pct
        prev_close = close
    return ordered


def load_benchmark_series(trade_dates: set[str]) -> dict[str, dict[str, float | None]]:
    if not trade_dates:
        return {}
    ts_code = BENCHMARK_TS_CODE.upper()
    with get_conn() as conn:
        rows = conn.execute(
            select(
                benchmark_table.c.trade_date,
                benchmark_table.c.close,
                benchmark_table.c.pct_chg,
                benchmark_table.c.cum_return_pct,
            ).where(
                benchmark_table.c.ts_code == ts_code,
                benchmark_table.c.trade_date.in_(sorted(trade_dates)),
            )
        ).fetchall()
    result: dict[str, dict[str, float | None]] = {}
    for row in rows:
        trade_date = str(row.trade_date)
        if row.close is None or float(row.close) <= 0:
            continue
        result[trade_date] = {
            "close": float(row.close),
            "pct_chg": float(row.pct_chg) if row.pct_chg is not None else None,
            "cum_return_pct": float(row.cum_return_pct) if row.cum_return_pct is not None else None,
        }
    return result


def aligned_benchmark_returns(
    trade_dates: list[str],
    bench_series: dict[str, dict[str, float | None]],
) -> tuple[str | None, dict[str, dict[str, float | None]]]:
    eligible = sorted(d for d in trade_dates if d in bench_series)
    if not eligible:
        return None, {}
    base_date = eligible[0]
    base_cum = bench_series[base_date].get("cum_return_pct") or 0.0
    aligned: dict[str, dict[str, float | None]] = {}
    for trade_date in trade_dates:
        item = bench_series.get(trade_date)
        if not item:
            continue
        cum = item.get("cum_return_pct")
        aligned[trade_date] = {
            "pct_chg": item.get("pct_chg"),
            "cum_return_pct": round(float(cum) - float(base_cum), 2) if cum is not None else None,
        }
    return base_date, aligned
