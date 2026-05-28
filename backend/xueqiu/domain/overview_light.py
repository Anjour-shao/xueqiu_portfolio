"""组合总览轻量查询（避免对每个账户跑完整 get_dashboard）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from xueqiu.domain.benchmark_series import load_benchmark_series
from xueqiu.storage.db import get_conn


def _display_date(trade_date: str) -> str:
    raw = str(trade_date)
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def load_portfolios_overview_items() -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT
            a.id AS account_id,
            a.account_code,
            a.account_name,
            nav.trade_date AS latest_nav_date_raw,
            nav.cum_return_pct,
            nav.nav_value,
            t.latest_trade_time,
            COALESCE(h.holding_count, 0) AS holding_count
        FROM accounts a
        LEFT JOIN (
            SELECT c1.account_id, c1.trade_date, c1.cum_return_pct, c1.nav_value
            FROM cube_nav_points c1
            INNER JOIN (
                SELECT account_id, MAX(trade_date) AS max_date
                FROM cube_nav_points
                GROUP BY account_id
            ) c2 ON c1.account_id = c2.account_id AND c1.trade_date = c2.max_date
        ) nav ON nav.account_id = a.id
        LEFT JOIN (
            SELECT account_id, MAX(trade_time) AS latest_trade_time
            FROM rebalance_trades
            GROUP BY account_id
        ) t ON t.account_id = a.id
        LEFT JOIN (
            SELECT account_id, COUNT(*) AS holding_count
            FROM (
                SELECT
                    rt.account_id,
                    rt.ts_code,
                    rt.to_weight,
                    ROW_NUMBER() OVER (
                        PARTITION BY rt.account_id, rt.ts_code
                        ORDER BY rt.trade_time DESC, rt.id DESC
                    ) AS rn
                FROM rebalance_trades rt
            ) latest_rt
            WHERE rn = 1 AND to_weight > 0.01
            GROUP BY account_id
        ) h ON h.account_id = a.id
        WHERE UPPER(a.account_code) REGEXP '^ZH[0-9]+$'
        ORDER BY a.account_name ASC
        """
    )

    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()

    bench_dates: set[str] = set()
    raw_items: list[dict[str, Any]] = []
    for row in rows:
        nav_date = str(row.latest_nav_date_raw) if row.latest_nav_date_raw else None
        if nav_date:
            bench_dates.add(nav_date)
        raw_items.append(
            {
                "account_code": str(row.account_code),
                "account_name": str(row.account_name),
                "cum_return_pct": float(row.cum_return_pct) if row.cum_return_pct is not None else None,
                "latest_nav_date": _display_date(nav_date) if nav_date else None,
                "latest_nav_date_raw": nav_date,
                "latest_trade_time": str(row.latest_trade_time) if row.latest_trade_time else None,
                "holding_count": int(row.holding_count or 0),
                "nav_source": "official" if row.cum_return_pct is not None else None,
            }
        )

    bench_series = load_benchmark_series(bench_dates) if bench_dates else {}
    items: list[dict[str, Any]] = []
    for item in raw_items:
        bench_return_pct = None
        excess_return_pct = None
        nav_date = item.pop("latest_nav_date_raw", None)
        cum = item.get("cum_return_pct")
        if nav_date and nav_date in bench_series:
            bench_return_pct = bench_series[nav_date].get("cum_return_pct")
            if cum is not None and bench_return_pct is not None:
                excess_return_pct = round(float(cum) - float(bench_return_pct), 2)
        item["benchmark_return_pct"] = bench_return_pct
        item["excess_return_pct"] = excess_return_pct
        items.append(item)

    items.sort(key=lambda row: float(row.get("cum_return_pct") or -1e9), reverse=True)
    return items
