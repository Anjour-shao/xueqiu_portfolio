"""挖组合股票池（MySQL discovery_symbol_pool）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update

from xueqiu.domain.codes import is_cn_a_share, to_xueqiu_code
from xueqiu.domain.discovery_hot_symbols import VOLUME_TOP100_SYMBOLS, VOLUME_TOP100_TRADE_DATE
from xueqiu.integrations.stock_names import resolve_a_share_names
from xueqiu.storage.db import discovery_symbol_pool_table, get_conn, init_db


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_symbol(raw: str) -> str:
    sym = to_xueqiu_code(str(raw).strip().upper())
    if not is_cn_a_share(sym):
        raise ValueError(f"非 A 股代码: {raw}")
    return sym


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "stock_name": row.stock_name,
        "note": row.note,
        "enabled": bool(row.enabled),
        "sort_order": int(row.sort_order or 0),
        "is_builtin": bool(row.is_builtin),
        "volume_rank_date": row.volume_rank_date,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def backfill_missing_stock_names() -> int:
    """补全 stock_name 为空的行，返回更新条数。"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            select(discovery_symbol_pool_table.c.symbol).where(
                (discovery_symbol_pool_table.c.stock_name.is_(None))
                | (discovery_symbol_pool_table.c.stock_name == "")
            )
        ).fetchall()
    symbols = [str(r[0]) for r in rows if r[0]]
    if not symbols:
        return 0
    names = resolve_a_share_names(symbols)
    if not names:
        return 0
    now = _now()
    updated = 0
    with get_conn() as conn:
        for sym, name in names.items():
            conn.execute(
                update(discovery_symbol_pool_table)
                .where(discovery_symbol_pool_table.c.symbol == sym)
                .values(stock_name=name, updated_at=now)
            )
            updated += 1
    return updated


def _load_custom_pool_rows() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            select(discovery_symbol_pool_table).where(
                discovery_symbol_pool_table.c.is_builtin.is_(False)
            )
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def sync_builtin_symbol_pool_if_stale() -> bool:
    """内置 Top100 基准日变更时，刷新 DB 中的内置股票池（保留用户自定义条目）。"""
    init_db()
    with get_conn() as conn:
        rank_date = conn.execute(
            select(discovery_symbol_pool_table.c.volume_rank_date)
            .where(discovery_symbol_pool_table.c.is_builtin.is_(True))
            .limit(1)
        ).scalar_one_or_none()
    if rank_date == VOLUME_TOP100_TRADE_DATE:
        return False

    custom = _load_custom_pool_rows()
    names = resolve_a_share_names(VOLUME_TOP100_SYMBOLS)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            discovery_symbol_pool_table.delete().where(
                discovery_symbol_pool_table.c.is_builtin.is_(True)
            )
        )
        for idx, sym in enumerate(VOLUME_TOP100_SYMBOLS):
            conn.execute(
                discovery_symbol_pool_table.insert().values(
                    symbol=sym,
                    stock_name=names.get(sym),
                    note=None,
                    enabled=True,
                    sort_order=idx,
                    is_builtin=True,
                    volume_rank_date=VOLUME_TOP100_TRADE_DATE,
                    created_at=now,
                    updated_at=now,
                )
            )
        base_order = len(VOLUME_TOP100_SYMBOLS)
        for offset, item in enumerate(custom):
            sym = _normalize_symbol(item["symbol"])
            conn.execute(
                discovery_symbol_pool_table.insert().values(
                    symbol=sym,
                    stock_name=item.get("stock_name"),
                    note=item.get("note"),
                    enabled=bool(item.get("enabled", True)),
                    sort_order=base_order + offset,
                    is_builtin=False,
                    volume_rank_date=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    backfill_missing_stock_names()
    return True


def seed_symbol_pool_if_empty() -> int:
    """表为空时写入 Top100 默认池（含股票名称）。"""
    init_db()
    now = _now()
    with get_conn() as conn:
        count = conn.execute(select(func.count()).select_from(discovery_symbol_pool_table)).scalar_one()
        if int(count or 0) > 0:
            sync_builtin_symbol_pool_if_stale()
            backfill_missing_stock_names()
            return 0
    names = resolve_a_share_names(VOLUME_TOP100_SYMBOLS)
    with get_conn() as conn:
        for idx, sym in enumerate(VOLUME_TOP100_SYMBOLS):
            conn.execute(
                discovery_symbol_pool_table.insert().values(
                    symbol=sym,
                    stock_name=names.get(sym),
                    note=None,
                    enabled=True,
                    sort_order=idx,
                    is_builtin=True,
                    volume_rank_date=VOLUME_TOP100_TRADE_DATE,
                    created_at=now,
                    updated_at=now,
                )
            )
    backfill_missing_stock_names()
    return len(VOLUME_TOP100_SYMBOLS)


def get_symbol_pool_meta() -> dict[str, Any]:
    init_db()
    seed_symbol_pool_if_empty()
    with get_conn() as conn:
        total = conn.execute(select(func.count()).select_from(discovery_symbol_pool_table)).scalar_one()
        enabled = conn.execute(
            select(func.count())
            .select_from(discovery_symbol_pool_table)
            .where(discovery_symbol_pool_table.c.enabled.is_(True))
        ).scalar_one()
        rank_date = conn.execute(
            select(discovery_symbol_pool_table.c.volume_rank_date)
            .where(discovery_symbol_pool_table.c.is_builtin.is_(True))
            .limit(1)
        ).scalar_one_or_none()
    return {
        "total_count": int(total or 0),
        "enabled_count": int(enabled or 0),
        "volume_rank_date": rank_date,
    }


def list_symbol_pool(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    init_db()
    seed_symbol_pool_if_empty()
    stmt = select(discovery_symbol_pool_table).order_by(
        discovery_symbol_pool_table.c.sort_order.asc(),
        discovery_symbol_pool_table.c.symbol.asc(),
    )
    if enabled_only:
        stmt = stmt.where(discovery_symbol_pool_table.c.enabled.is_(True))
    with get_conn() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_enabled_symbols() -> list[str]:
    return [row["symbol"] for row in list_symbol_pool(enabled_only=True)]


def replace_symbol_pool(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全量替换股票池（自动补全缺失的股票名称）。"""
    init_db()
    now = _now()
    builtin = set(VOLUME_TOP100_SYMBOLS)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(items):
        sym = _normalize_symbol(str(item.get("symbol") or ""))
        if sym in seen:
            continue
        seen.add(sym)
        raw_name = str(item.get("stock_name") or "").strip() or None
        normalized.append(
            {
                "symbol": sym,
                "stock_name": raw_name,
                "note": (str(item.get("note") or "").strip() or None),
                "enabled": bool(item.get("enabled", True)),
                "sort_order": int(item.get("sort_order", idx)),
                "is_builtin": sym in builtin,
                "volume_rank_date": VOLUME_TOP100_TRADE_DATE if sym in builtin else None,
                "created_at": now,
                "updated_at": now,
            }
        )
    if not normalized:
        raise ValueError("股票池不能为空")

    need_names = [row["symbol"] for row in normalized if not row.get("stock_name")]
    if need_names:
        resolved = resolve_a_share_names(need_names)
        for row in normalized:
            if not row.get("stock_name") and row["symbol"] in resolved:
                row["stock_name"] = resolved[row["symbol"]]

    with get_conn() as conn:
        conn.execute(discovery_symbol_pool_table.delete())
        for row in normalized:
            conn.execute(discovery_symbol_pool_table.insert().values(**row))
    return list_symbol_pool()
