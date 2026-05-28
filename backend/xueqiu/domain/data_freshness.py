"""各数据源最新日期与新鲜度状态。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, text

from xueqiu.config import BENCHMARK_TS_CODE
from xueqiu.storage.db import benchmark_table, get_conn

FreshnessStatus = Literal["ok", "stale", "empty"]

STALE_DAYS = 3


def _display_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if len(s) >= 10 else s


def _status_for_date(latest_yyyymmdd: str | None, *, today: str) -> FreshnessStatus:
    if not latest_yyyymmdd:
        return "empty"
    try:
        latest = datetime.strptime(latest_yyyymmdd[:8], "%Y%m%d").date()
        ref = datetime.strptime(today[:8], "%Y%m%d").date()
    except ValueError:
        return "stale"
    if (ref - latest).days <= STALE_DAYS:
        return "ok"
    return "stale"


def load_data_freshness() -> dict[str, Any]:
    today = date.today().strftime("%Y%m%d")
    ts_code = BENCHMARK_TS_CODE.upper()

    with get_conn() as conn:
        rebalance_row = conn.execute(
            text(
                """
                SELECT MAX(trade_time) AS latest_trade_time,
                       COUNT(*) AS trade_count
                FROM rebalance_trades
                """
            )
        ).fetchone()

        quotes_row = conn.execute(
            text(
                """
                SELECT MAX(trade_date) AS latest_date,
                       COUNT(DISTINCT ts_code) AS symbol_count
                FROM quote_points
                """
            )
        ).fetchone()

        bench_row = conn.execute(
            select(
                func.max(benchmark_table.c.trade_date),
                func.count(benchmark_table.c.trade_date),
            ).where(benchmark_table.c.ts_code == ts_code)
        ).fetchone()

        nav_row = conn.execute(
            text(
                """
                SELECT MIN(latest_date) AS min_date,
                       MAX(latest_date) AS max_date,
                       COUNT(*) AS account_count
                FROM (
                    SELECT account_id, MAX(trade_date) AS latest_date
                    FROM cube_nav_points
                    GROUP BY account_id
                ) t
                """
            )
        ).fetchone()

        zh_count = conn.execute(
            text("SELECT COUNT(*) AS c FROM accounts WHERE UPPER(account_code) REGEXP '^ZH[0-9]+$'")
        ).scalar()

        stale_nav = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                    SELECT a.id,
                           COALESCE(nav.max_date, '') AS nav_date
                    FROM accounts a
                    LEFT JOIN (
                        SELECT account_id, MAX(trade_date) AS max_date
                        FROM cube_nav_points
                        GROUP BY account_id
                    ) nav ON nav.account_id = a.id
                    WHERE UPPER(a.account_code) REGEXP '^ZH[0-9]+$'
                ) x
                WHERE nav_date = '' OR nav_date < :threshold
                """
            ),
            {"threshold": (date.today() - timedelta(days=STALE_DAYS)).strftime("%Y%m%d")},
        ).scalar()

    latest_trade = str(rebalance_row.latest_trade_time) if rebalance_row and rebalance_row.latest_trade_time else None
    quotes_latest = str(quotes_row.latest_date) if quotes_row and quotes_row.latest_date else None
    bench_latest = str(bench_row[0]) if bench_row and bench_row[0] else None

    nav_max = str(nav_row.max_date) if nav_row and nav_row.max_date else None
    nav_min = str(nav_row.min_date) if nav_row and nav_row.min_date else None

    return {
        "as_of": today,
        "stale_threshold_days": STALE_DAYS,
        "rebalance": {
            "latest_trade_time": latest_trade,
            "trade_count": int(rebalance_row.trade_count or 0) if rebalance_row else 0,
            "status": "ok" if latest_trade else "empty",
        },
        "quotes": {
            "latest_date": _display_date(quotes_latest),
            "latest_date_raw": quotes_latest,
            "symbol_count": int(quotes_row.symbol_count or 0) if quotes_row else 0,
            "status": _status_for_date(quotes_latest, today=today),
        },
        "benchmark": {
            "ts_code": ts_code,
            "latest_date": _display_date(bench_latest),
            "latest_date_raw": bench_latest,
            "point_count": int(bench_row[1] or 0) if bench_row else 0,
            "status": _status_for_date(bench_latest, today=today),
        },
        "cube_nav": {
            "latest_date_min": _display_date(nav_min),
            "latest_date_max": _display_date(nav_max),
            "latest_date_max_raw": nav_max,
            "account_count": int(nav_row.account_count or 0) if nav_row else 0,
            "zh_account_count": int(zh_count or 0),
            "stale_accounts": int(stale_nav or 0),
            "status": _status_for_date(nav_max, today=today),
        },
    }
