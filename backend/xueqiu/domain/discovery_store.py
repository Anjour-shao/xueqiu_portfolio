"""mined_cubes 读写与统计。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from xueqiu.storage.db import get_conn, mined_cubes_table


def _decode_reasons(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "account_code": row.account_code,
        "account_name": row.account_name,
        "owner_uid": row.owner_uid,
        "owner_name": row.owner_name,
        "source_user_uid": row.source_user_uid,
        "source_account_code": row.source_account_code,
        "depth": int(row.depth or 1),
        "cum_return_pct": row.cum_return_pct,
        "nav_latest_date": row.nav_latest_date,
        "latest_rebalance_time": row.latest_rebalance_time,
        "rebalance_count_6m": row.rebalance_count_6m,
        "cube_market": row.cube_market,
        "has_non_a_share": bool(row.has_non_a_share),
        "auto_pass": bool(row.auto_pass),
        "reject_reasons": _decode_reasons(row.reject_reasons),
        "selected": row.selected,
        "note": row.note,
        "imported_at": row.imported_at.isoformat() if row.imported_at else None,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_discovery_stats() -> dict[str, Any]:
    with get_conn() as conn:
        total = conn.execute(select(func.count()).select_from(mined_cubes_table)).scalar_one()
        auto_pass = conn.execute(
            select(func.count())
            .select_from(mined_cubes_table)
            .where(mined_cubes_table.c.auto_pass.is_(True))
        ).scalar_one()
        pending = conn.execute(
            select(func.count())
            .select_from(mined_cubes_table)
            .where(
                mined_cubes_table.c.auto_pass.is_(True),
                mined_cubes_table.c.selected.is_(None),
                mined_cubes_table.c.imported_at.is_(None),
            )
        ).scalar_one()
        selected = conn.execute(
            select(func.count())
            .select_from(mined_cubes_table)
            .where(mined_cubes_table.c.selected == 1)
        ).scalar_one()
        rejected = conn.execute(
            select(func.count())
            .select_from(mined_cubes_table)
            .where(mined_cubes_table.c.selected == -1)
        ).scalar_one()
        imported = conn.execute(
            select(func.count())
            .select_from(mined_cubes_table)
            .where(mined_cubes_table.c.imported_at.is_not(None))
        ).scalar_one()
    return {
        "total_count": int(total or 0),
        "auto_pass_count": int(auto_pass or 0),
        "pending_count": int(pending or 0),
        "selected_count": int(selected or 0),
        "rejected_count": int(rejected or 0),
        "imported_count": int(imported or 0),
    }


def list_mined_cubes(
    *,
    auto_pass: bool | None = None,
    selected: int | None = None,
    depth: int | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    stmt = select(mined_cubes_table).order_by(
        mined_cubes_table.c.auto_pass.desc(),
        mined_cubes_table.c.cum_return_pct.desc(),
        mined_cubes_table.c.account_code.asc(),
    )
    if auto_pass is not None:
        stmt = stmt.where(mined_cubes_table.c.auto_pass.is_(auto_pass))
    if selected is not None:
        stmt = stmt.where(mined_cubes_table.c.selected == selected)
    if depth is not None:
        stmt = stmt.where(mined_cubes_table.c.depth == depth)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                mined_cubes_table.c.account_code.like(like),
                mined_cubes_table.c.account_name.like(like),
                mined_cubes_table.c.owner_name.like(like),
            )
        )
    stmt = stmt.limit(max(1, min(limit, 2000)))
    with get_conn() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_mined_cube_selection(
    account_code: str,
    *,
    selected: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    code = account_code.strip().upper()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    values: dict[str, Any] = {"updated_at": now}
    if selected is not None:
        if selected not in (-1, 0, 1):
            raise ValueError("selected 须为 1（选中）、-1（拒绝）或 0（清除）")
        values["selected"] = None if selected == 0 else selected
    if note is not None:
        values["note"] = note.strip() or None

    with get_conn() as conn:
        row = conn.execute(
            select(mined_cubes_table).where(mined_cubes_table.c.account_code == code)
        ).fetchone()
        if row is None:
            raise ValueError(f"未找到候选组合: {code}")
        conn.execute(
            mined_cubes_table.update()
            .where(mined_cubes_table.c.account_code == code)
            .values(**values)
        )
        updated = conn.execute(
            select(mined_cubes_table).where(mined_cubes_table.c.account_code == code)
        ).fetchone()
    return _row_to_dict(updated)


def mark_mined_cube_imported(account_code: str) -> None:
    code = account_code.strip().upper()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_conn() as conn:
        conn.execute(
            mined_cubes_table.update()
            .where(mined_cubes_table.c.account_code == code)
            .values(imported_at=now, selected=1, updated_at=now)
        )
