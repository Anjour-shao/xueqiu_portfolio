"""从雪球发现页榜单同步组合目录到 cube_catalog（增量 upsert）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from xueqiu.integrations.xueqiu.cube_rank import CN_RANK_SOURCES, fetch_all_cn_rank_cubes
from xueqiu.integrations.xueqiu.client import XueQiuApiClient
from xueqiu.storage.db import cube_catalog_table, get_conn, init_db
from xueqiu.sync.sync_cancel import check_cancel
from xueqiu.sync.sync_log import LogSink


def catalog_stats() -> dict[str, Any]:
    with get_conn() as conn:
        total = conn.execute(select(func.count()).select_from(cube_catalog_table)).scalar_one()
        discovered = conn.execute(
            select(func.count())
            .select_from(cube_catalog_table)
            .where(cube_catalog_table.c.discovered.is_(True))
        ).scalar_one()
        last_updated = conn.execute(select(func.max(cube_catalog_table.c.updated_at))).scalar_one()
    total_i = int(total or 0)
    discovered_i = int(discovered or 0)
    return {
        "total_count": total_i,
        "discovered_count": discovered_i,
        "remaining_count": max(0, total_i - discovered_i),
        "last_updated_at": last_updated.isoformat() if last_updated else None,
    }


def mark_catalog_discovered(codes: list[str]) -> None:
    if not codes:
        return
    normalized = [c.strip().upper() for c in codes if c and c.strip()]
    if not normalized:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_conn() as conn:
        conn.execute(
            cube_catalog_table.update()
            .where(cube_catalog_table.c.account_code.in_(normalized))
            .values(discovered=True, discovered_at=now)
        )


def reset_catalog_discovered() -> int:
    """清空「已挖过」标记，返回重置条数。"""
    with get_conn() as conn:
        result = conn.execute(
            cube_catalog_table.update().values(discovered=False, discovered_at=None)
        )
        return int(result.rowcount or 0)


def fetch_catalog_batch_sequential(
    batch_size: int,
    *,
    exclude: set[str] | None = None,
) -> tuple[list[str], int, int, int]:
    """按 account_code 顺序取未挖过的组合。返回 (本批, 总数, 已挖, 未挖)。"""
    exclude = {c.strip().upper() for c in (exclude or set())}
    batch_size = max(1, batch_size)
    stats = catalog_stats()
    total = stats["total_count"]
    discovered = stats["discovered_count"]
    remaining = stats["remaining_count"]
    if total == 0:
        return [], 0, 0, 0

    with get_conn() as conn:
        rows = conn.execute(
            select(cube_catalog_table.c.account_code)
            .where(cube_catalog_table.c.discovered.is_(False))
            .order_by(cube_catalog_table.c.account_code)
        ).fetchall()

    codes: list[str] = []
    for row in rows:
        code = str(row.account_code).strip().upper()
        if not code or code in exclude:
            continue
        codes.append(code)
        if len(codes) >= batch_size:
            break
    return codes, total, discovered, remaining


def upsert_catalog_batch(rows: dict[str, str]) -> dict[str, int]:
    """批量写入；返回 new_count / updated_count。"""
    if not rows:
        return {"new_count": 0, "updated_count": 0}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_count = 0
    updated_count = 0

    with get_conn() as conn:
        existing = {
            str(r.account_code): str(r.account_name)
            for r in conn.execute(
                select(
                    cube_catalog_table.c.account_code,
                    cube_catalog_table.c.account_name,
                ).where(cube_catalog_table.c.account_code.in_(list(rows.keys())))
            )
        }

        for code, name in rows.items():
            old_name = existing.get(code)
            if old_name is None:
                conn.execute(
                    mysql_insert(cube_catalog_table).values(
                        account_code=code,
                        account_name=name,
                        first_seen_at=now,
                        updated_at=now,
                    )
                )
                new_count += 1
            elif old_name != name:
                conn.execute(
                    cube_catalog_table.update()
                    .where(cube_catalog_table.c.account_code == code)
                    .values(account_name=name, updated_at=now)
                )
                updated_count += 1

    return {"new_count": new_count, "updated_count": updated_count}


def sync_cube_catalog_from_ranks(
    *,
    sink: LogSink | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    init_db()
    log = sink or LogSink()
    log.info("▶ 开始同步榜单组合目录（发现页榜单 API，约 ≤130 条）")

    def on_source(label: str, count: int, err: str | None) -> None:
        check_cancel(cancel_event)
        if err:
            log.warn(f"  跳过 {label}: {err}")
        else:
            log.info(f"  ✓ {label}: {count} 条")

    try:
        client = XueQiuApiClient()
        merged, ok_labels, skipped = fetch_all_cn_rank_cubes(
            client,
            sources=CN_RANK_SOURCES,
            on_source_done=on_source,
            cancel_event=cancel_event,
        )
    except Exception as exc:
        log.error(f"榜单拉取失败: {exc}")
        return {"ok": False, "message": str(exc), "logs": log.lines}

    check_cancel(cancel_event)
    log.info(f"  榜单合并去重: {len(merged)} 个组合")
    if skipped:
        log.warn(f"  可选榜未拉取: {'; '.join(skipped)}")

    before = catalog_stats()["total_count"]
    counts = upsert_catalog_batch(merged)
    after = catalog_stats()["total_count"]

    new_count = int(counts["new_count"])
    updated_count = int(counts["updated_count"])
    msg = (
        f"榜单目录同步完成：本次拉取 {len(merged)} 个，"
        f"新增 {new_count}，更新名称 {updated_count}，库内共 {after} 个"
        f"（原 {before}）"
    )
    log.success(f"■ {msg}")
    return {
        "ok": True,
        "message": msg,
        "logs": log.lines,
        "fetched_count": len(merged),
        "new_count": new_count,
        "updated_count": updated_count,
        "total_count": after,
        "sources_ok": ok_labels,
        "sources_skipped": skipped,
    }
