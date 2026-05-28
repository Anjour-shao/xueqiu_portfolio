"""同步雪球组合官方日净值到 cube_nav_points。"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from xueqiu.integrations.xueqiu.client import XueQiuApiClient
from xueqiu.integrations.xueqiu.portfolio import fetch_cube_nav_daily, validate_portfolio_id
from xueqiu.storage.db import accounts_table, cube_nav_points_table, get_conn, init_db


def _list_zh_accounts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.account_code,
                accounts_table.c.account_name,
            ).order_by(accounts_table.c.account_code.asc())
        ).fetchall()
    accounts: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.account_code or "").strip().upper()
        if not code.startswith("ZH"):
            continue
        accounts.append(
            {
                "account_id": int(row.id),
                "account_code": code,
                "account_name": str(row.account_name),
            }
        )
    return accounts


def _latest_cube_nav_date(account_id: int) -> str | None:
    with get_conn() as conn:
        value = conn.execute(
            select(func.max(cube_nav_points_table.c.trade_date)).where(
                cube_nav_points_table.c.account_id == account_id
            )
        ).scalar()
    if value is None:
        return None
    return str(value)


def _since_ms_after_date(trade_date: str) -> int:
    dt = datetime.strptime(trade_date, "%Y%m%d") + timedelta(days=1)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _format_nav_date(trade_date: str) -> str:
    dk = str(trade_date)
    if len(dk) == 8 and dk.isdigit():
        return f"{dk[:4]}-{dk[4:6]}-{dk[6:8]}"
    return dk


def upsert_cube_nav_points(account_id: int, points: list[Any], *, sink: Any | None = None) -> int:
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    if not points:
        return 0
    synced_at = datetime.now()
    rows = [
        {
            "account_id": account_id,
            "trade_date": p.trade_date,
            "nav_value": float(p.nav_value),
            "cum_return_pct": float(p.cum_return_pct),
            "synced_at": synced_at,
        }
        for p in points
    ]
    if log_sink:
        for p in points:
            log_sink.info(
                f"    + {_format_nav_date(p.trade_date)} "
                f"nav={float(p.nav_value):.4f} cum={float(p.cum_return_pct):+.2f}%"
            )
    stmt = mysql_insert(cube_nav_points_table).values(rows)
    stmt = stmt.on_duplicate_key_update(
        nav_value=stmt.inserted.nav_value,
        cum_return_pct=stmt.inserted.cum_return_pct,
        synced_at=stmt.inserted.synced_at,
    )
    with get_conn() as conn:
        result = conn.execute(stmt)
        return int(result.rowcount or 0)


def sync_account_cube_nav(
    account_id: int,
    account_code: str,
    client: XueQiuApiClient | None = None,
    *,
    sink: Any | None = None,
) -> dict[str, Any]:
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    code = validate_portfolio_id(account_code)
    api = client or XueQiuApiClient()
    latest_db = _latest_cube_nav_date(account_id)

    if latest_db:
        since_ms = _since_ms_after_date(latest_db)
        if log_sink:
            log_sink.info(f"  增量拉取 {code}（库内最新 {_format_nav_date(latest_db)}）…")
        portfolio_name, fetched = fetch_cube_nav_daily(code, client=api, since_ms=since_ms)
        new_points = [p for p in fetched if p.trade_date > latest_db]
        if log_sink:
            log_sink.info(
                f"  [{code}] {portfolio_name} API 返回 {len(fetched)} 条，待写入新增 {len(new_points)} 条"
            )
        if not new_points:
            if log_sink:
                log_sink.success(f"  ✓ {code} 净值已是最新")
            return {
                "account_id": account_id,
                "account_code": code,
                "account_name": portfolio_name,
                "point_count": 0,
                "new_count": 0,
                "upserted": 0,
                "latest_date": latest_db,
                "mode": "incremental",
            }
        upserted = upsert_cube_nav_points(account_id, new_points, sink=log_sink)
        latest = new_points[-1].trade_date
    else:
        if log_sink:
            log_sink.info(f"  首次全量拉取 {code}（库内无记录）…")
        portfolio_name, points = fetch_cube_nav_daily(code, client=api)
        if log_sink:
            log_sink.info(f"  [{code}] {portfolio_name} 共 {len(points)} 个交易日，写入中…")
            if len(points) > 30:
                log_sink.info(f"    （首次同步明细省略，共 {len(points)} 日）")
        detail_sink = log_sink if log_sink and len(points) <= 30 else None
        upserted = upsert_cube_nav_points(account_id, points, sink=detail_sink)
        new_points = points
        latest = points[-1].trade_date if points else None

    return {
        "account_id": account_id,
        "account_code": code,
        "account_name": portfolio_name,
        "point_count": len(new_points),
        "new_count": len(new_points),
        "upserted": upserted,
        "latest_date": latest,
        "mode": "incremental" if latest_db else "full",
    }


def sync_all_cube_nav(
    client: XueQiuApiClient | None = None,
    *,
    sink: Any | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    import threading

    from xueqiu.sync.sync_cancel import check_cancel
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None
    init_db()
    accounts = _list_zh_accounts()
    if not accounts:
        return {"account_count": 0, "results": [], "message": "没有 ZH 组合账户"}

    api = client or XueQiuApiClient()
    results: list[dict[str, Any]] = []
    for index, account in enumerate(accounts):
        check_cancel(cancel)
        code = account["account_code"]
        if log_sink:
            log_sink.info(f"── [{index + 1}/{len(accounts)}] {code} ──")
        try:
            result = sync_account_cube_nav(
                account["account_id"],
                code,
                client=api,
                sink=log_sink,
            )
            results.append({"ok": True, **result})
            if log_sink:
                mode = result.get("mode", "")
                new_n = int(result.get("new_count") or result.get("point_count") or 0)
                mode_tag = "增量" if mode == "incremental" else "全量"
                log_sink.success(
                    f"  ✓ {code} [{mode_tag}] 新增 {new_n} 日，最新 {result.get('latest_date') or '—'}"
                )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "account_id": account["account_id"],
                    "account_code": code,
                    "error": str(exc),
                }
            )
            if log_sink:
                log_sink.error(f"  ✗ {code} 失败: {exc}")
        if index < len(accounts) - 1:
            time.sleep(random.uniform(0.6, 1.2))

    ok_count = sum(1 for item in results if item.get("ok"))
    return {
        "account_count": len(accounts),
        "ok_count": ok_count,
        "failed_count": len(accounts) - ok_count,
        "results": results,
        "message": f"官方净值同步完成：{ok_count}/{len(accounts)} 个组合",
    }


def sync_cube_nav_for_account(account_id: int) -> dict[str, Any]:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_code).where(
                accounts_table.c.id == account_id
            )
        ).fetchone()
    if row is None:
        raise ValueError(f"账户不存在: {account_id}")
    return sync_account_cube_nav(int(row.id), str(row.account_code))
