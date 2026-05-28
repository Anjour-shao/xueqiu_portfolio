"""雪球官方组合净值曲线（读 cube_nav_points + 基准对比）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from xueqiu.config import BENCHMARK_TS_CODE
from xueqiu.domain.benchmark_series import aligned_benchmark_returns, load_benchmark_series
from xueqiu.storage.db import cube_nav_points_table, get_conn


def _display_date(trade_date: str) -> str:
    if len(trade_date) == 8 and trade_date.isdigit():
        return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    return trade_date


def load_official_equity_curve(account_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            select(
                cube_nav_points_table.c.trade_date,
                cube_nav_points_table.c.nav_value,
                cube_nav_points_table.c.cum_return_pct,
            )
            .where(cube_nav_points_table.c.account_id == account_id)
            .order_by(cube_nav_points_table.c.trade_date.asc())
        ).fetchall()

    if not rows:
        return {"curve": [], "overview_patch": {}, "has_official": False}

    trade_dates = [str(r.trade_date) for r in rows]
    bench_series = load_benchmark_series(set(trade_dates))
    _, bench_aligned = aligned_benchmark_returns(trade_dates, bench_series)

    curve: list[dict[str, Any]] = []
    prev_nav: float | None = None
    for row in rows:
        trade_date = str(row.trade_date)
        nav_value = float(row.nav_value)
        cum_return_pct = float(row.cum_return_pct)
        display = _display_date(trade_date)
        trade_time = f"{display} 15:00:00"

        period_return_pct = None
        if prev_nav is not None and prev_nav > 0:
            period_return_pct = round((nav_value / prev_nav - 1.0) * 100, 4)
        prev_nav = nav_value

        bench = bench_aligned.get(trade_date, {})
        bench_return_pct = bench.get("cum_return_pct")
        bench_daily_pct = bench.get("pct_chg")
        excess_return_pct = (
            round(cum_return_pct - float(bench_return_pct), 2) if bench_return_pct is not None else None
        )

        curve.append(
            {
                "trade_date": display,
                "trade_time": trade_time,
                "nav": round(nav_value, 6),
                "cum_return_pct": round(cum_return_pct, 4),
                "realized_return_pct": round(cum_return_pct, 4),
                "unrealized_return_pct": 0.0,
                "period_return_pct": period_return_pct,
                "benchmark_return_pct": bench_return_pct,
                "benchmark_daily_pct": bench_daily_pct,
                "excess_return_pct": excess_return_pct,
                "event_count": 0,
                "nav_source": "official",
            }
        )

    last = curve[-1]
    overview_patch = {
        "cum_return_pct": last["cum_return_pct"],
        "benchmark_return_pct": last.get("benchmark_return_pct"),
        "excess_return_pct": last.get("excess_return_pct"),
        "final_nav": last["nav"],
        "nav_return_pct": last["cum_return_pct"],
    }

    return {
        "curve": curve,
        "overview_patch": overview_patch,
        "has_official": True,
        "point_count": len(curve),
        "benchmark_ts_code": BENCHMARK_TS_CODE,
    }
