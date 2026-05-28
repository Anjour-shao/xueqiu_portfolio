"""
主行情同步（新浪）：后复权价 + 基准指数收盘。

TuShare 已移至 sync_tushare_aux.py，需手动执行。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import threading

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from xueqiu.config import BENCHMARK_TS_CODE
from xueqiu.domain.benchmark_series import enrich_benchmark_rows
from xueqiu.domain.codes import to_xueqiu_code
from xueqiu.storage.db import benchmark_table, engine, get_conn, init_db, quote_points_table
from xueqiu.integrations.sina.hfq import fetch_latest_hfq_batch
from xueqiu.integrations.sina.index import fetch_index_closes_robust, filter_closes_by_range


def _log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def fetch_trade_points() -> list[tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ts_code, REPLACE(SUBSTRING(trade_time, 1, 10), '-', '') AS trade_date
                FROM rebalance_trades
                WHERE ts_code IS NOT NULL
                ORDER BY trade_date, ts_code
                """
            )
        ).fetchall()
    return [(to_xueqiu_code(str(row.ts_code)), str(row.trade_date)) for row in rows]


def fetch_existing_points() -> set[tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(text("SELECT ts_code, trade_date FROM quote_points")).fetchall()
    return {(str(row.ts_code).upper(), str(row.trade_date)) for row in rows}


def fetch_existing_benchmark_dates(ts_code: str, start_date: str, end_date: str) -> set[str]:
    code = ts_code.upper()
    with get_conn() as conn:
        rows = conn.execute(
            select(benchmark_table.c.trade_date).where(
                benchmark_table.c.ts_code == code,
                benchmark_table.c.trade_date >= start_date,
                benchmark_table.c.trade_date <= end_date,
            )
        ).fetchall()
    return {str(row.trade_date) for row in rows}


def fetch_existing_benchmark_closes(ts_code: str, start_date: str, end_date: str) -> dict[str, float]:
    code = ts_code.upper()
    with get_conn() as conn:
        rows = conn.execute(
            select(benchmark_table.c.trade_date, benchmark_table.c.close).where(
                benchmark_table.c.ts_code == code,
                benchmark_table.c.trade_date >= start_date,
                benchmark_table.c.trade_date <= end_date,
            )
        ).fetchall()
    result: dict[str, float] = {}
    for row in rows:
        if row.close is None or float(row.close) <= 0:
            continue
        result[str(row.trade_date)] = float(row.close)
    return result


def _plan_hfq_sync(
    target_codes: set[str],
    trade_dates_by_code: dict[str, set[str]],
    existing: set[tuple[str, str]],
) -> tuple[set[str], dict[str, set[str]], int]:
    """返回需请求新浪的标的、各标的缺失交易日、跳过数量。"""
    today = date.today().strftime("%Y%m%d")
    codes_to_sync: set[str] = set()
    missing_by_code: dict[str, set[str]] = {}
    skipped = 0

    for code in target_codes:
        normalized = to_xueqiu_code(code)
        required = trade_dates_by_code.get(normalized, set())
        existing_dates = {d for c, d in existing if c == normalized}
        missing = {d for d in required if (normalized, d) not in existing}
        max_existing = max(existing_dates, default="")
        max_required = max(required, default="")

        if not missing:
            if max_existing and max_existing >= max(max_required, today):
                skipped += 1
                continue
            if max_existing and max_required and max_existing >= max_required:
                skipped += 1
                continue

        codes_to_sync.add(normalized)
        missing_by_code[normalized] = missing

    return codes_to_sync, missing_by_code, skipped


def fetch_trade_dates_by_code(codes: set[str] | None = None) -> dict[str, set[str]]:
    with get_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ts_code, REPLACE(SUBSTRING(trade_time, 1, 10), '-', '') AS trade_date
                FROM rebalance_trades
                WHERE ts_code IS NOT NULL
                """
            )
        ).fetchall()

    result: dict[str, set[str]] = {}
    allowed = {to_xueqiu_code(c) for c in codes} if codes else None
    for row in rows:
        code = to_xueqiu_code(str(row.ts_code))
        if allowed is not None and code not in allowed:
            continue
        result.setdefault(code, set()).add(str(row.trade_date))
    return result


def fetch_traded_codes(account_id: int | None = None) -> set[str]:
    sql = "SELECT DISTINCT ts_code FROM rebalance_trades WHERE ts_code IS NOT NULL"
    params: dict[str, Any] = {}
    if account_id is not None:
        sql += " AND account_id = :account_id"
        params["account_id"] = account_id

    with get_conn() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return {to_xueqiu_code(str(row.ts_code)) for row in rows}


def fetch_trade_date_range() -> tuple[str, str] | None:
    with get_conn() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    MIN(REPLACE(SUBSTRING(trade_time, 1, 10), '-', '')) AS start_date,
                    MAX(REPLACE(SUBSTRING(trade_time, 1, 10), '-', '')) AS end_date
                FROM rebalance_trades
                WHERE trade_time IS NOT NULL
                """
            )
        ).fetchone()
    if row is None or not row.start_date or not row.end_date:
        return None
    return str(row.start_date), str(row.end_date)


def _upsert_hfq_close_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    normalized_rows = [
        {
            "ts_code": to_xueqiu_code(str(row["ts_code"])),
            "trade_date": str(row["trade_date"]),
            "adj_factor": 1.0,
            "close_hfq": float(row["close_hfq"]),
        }
        for row in rows
    ]

    stmt = mysql_insert(quote_points_table).values(normalized_rows)
    stmt = stmt.on_duplicate_key_update(close_hfq=stmt.inserted.close_hfq)
    with get_conn() as conn:
        conn.execute(stmt)
    return len(normalized_rows)


def _normalize_benchmark_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "ts_code": str(row["ts_code"]).strip().upper(),
                "trade_date": str(row["trade_date"]).strip(),
                "close": float(row["close"]),
                "pct_chg": float(row["pct_chg"]) if row.get("pct_chg") is not None else None,
                "cum_return_pct": float(row["cum_return_pct"]) if row.get("cum_return_pct") is not None else None,
            }
        )
    return payload


def verify_benchmark_in_db(ts_code: str, trade_dates: set[str]) -> list[str]:
    """返回写入后仍缺失的交易日。"""
    if not trade_dates:
        return []
    code = ts_code.strip().upper()
    with get_conn() as conn:
        rows = conn.execute(
            select(benchmark_table.c.trade_date).where(
                benchmark_table.c.ts_code == code,
                benchmark_table.c.trade_date.in_(sorted(trade_dates)),
            )
        ).fetchall()
    found = {str(row.trade_date) for row in rows}
    return sorted(d for d in trade_dates if d not in found)


def _upsert_benchmark_rows(rows: list[dict[str, Any]], *, already_enriched: bool = False) -> int:
    if not rows:
        return 0
    prepared = enrich_benchmark_rows(list(rows)) if not already_enriched else _normalize_benchmark_payload(rows)
    if not prepared:
        return 0

    # 使用 VALUES() 语法，避免部分环境下 stmt.inserted 在 ON DUPLICATE 时未生效
    sql = text(
        """
        INSERT INTO benchmark (ts_code, trade_date, close, pct_chg, cum_return_pct)
        VALUES (:ts_code, :trade_date, :close, :pct_chg, :cum_return_pct)
        ON DUPLICATE KEY UPDATE
            close = VALUES(close),
            pct_chg = VALUES(pct_chg),
            cum_return_pct = VALUES(cum_return_pct)
        """
    )
    affected = 0
    with get_conn() as conn:
        for row in prepared:
            result = conn.execute(sql, row)
            affected += int(result.rowcount or 0)
    return affected


def backfill_price_hfq() -> None:
    _log("回填 rebalance_trades.price_hfq（优先新浪 close_hfq）。")
    sql = """
    UPDATE rebalance_trades rt
    JOIN quote_points qp
      ON qp.ts_code = UPPER(rt.ts_code)
     AND qp.trade_date = REPLACE(SUBSTRING(rt.trade_time, 1, 10), '-', '')
    SET rt.price_hfq = COALESCE(qp.close_hfq, rt.price * qp.adj_factor)
    WHERE rt.price IS NOT NULL
      AND (qp.close_hfq IS NOT NULL OR qp.adj_factor IS NOT NULL)
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def sync_sina_hfq(
    codes: set[str] | None = None,
    *,
    sink: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    from xueqiu.sync.sync_cancel import SyncCancelled, check_cancel
    from xueqiu.sync.sync_log import LogSink, print_adapter

    log_sink = sink if isinstance(sink, LogSink) else None
    log_fn = print_adapter(log_sink) if log_sink else _log

    check_cancel(cancel_event)
    target_codes = codes if codes is not None else fetch_traded_codes()
    if not target_codes:
        msg = "无标的，跳过新浪后复权价同步。"
        if log_sink:
            log_sink.warn(msg)
        else:
            _log(msg)
        return 0

    trade_dates_by_code = fetch_trade_dates_by_code(target_codes)
    existing = fetch_existing_points()
    codes_to_sync, missing_by_code, skipped = _plan_hfq_sync(target_codes, trade_dates_by_code, existing)

    if log_sink:
        log_sink.info(f"新浪后复权：共 {len(target_codes)} 只，需更新 {len(codes_to_sync)} 只，跳过 {skipped} 只（库内已齐）")
    else:
        _log(f"新浪后复权：共 {len(target_codes)} 只，需更新 {len(codes_to_sync)} 只，跳过 {skipped} 只。")

    if skipped and log_sink:
        if skipped <= 8:
            for code in sorted(target_codes - codes_to_sync):
                n = len(trade_dates_by_code.get(to_xueqiu_code(code), set()))
                log_sink.info(f"  跳过 {to_xueqiu_code(code)}（库内已有 {n} 个调仓日行情）")
        else:
            log_sink.info(f"  已跳过 {skipped} 只标的（库内行情已齐，不逐条列出）")

    if not codes_to_sync:
        done_msg = "新浪后复权：全部标的已在库内，无需请求新浪"
        if log_sink:
            log_sink.success(done_msg)
        else:
            _log(done_msg + "。")
        return 0

    cancel_check = cancel_event.is_set if cancel_event is not None else None
    rows = fetch_latest_hfq_batch(
        codes_to_sync,
        trade_dates_by_code=missing_by_code,
        log=log_fn,
        cancel_check=cancel_check,
    )
    check_cancel(cancel_event)

    inserted = _upsert_hfq_close_rows(rows)
    backfill_price_hfq()
    done_msg = f"新浪后复权完成，写入 {inserted} 条"
    if log_sink:
        log_sink.success(done_msg)
    else:
        _log(done_msg + "。")
    return inserted


def sync_benchmark_from_sina(
    *,
    sink: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    from xueqiu.sync.sync_cancel import check_cancel
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    check_cancel(cancel_event)
    date_range = fetch_trade_date_range()
    if not date_range:
        msg = "无交易记录，跳过基准同步。"
        if log_sink:
            log_sink.warn(msg)
        else:
            _log(msg)
        return 0

    start_date, end_date = date_range
    ts_code = BENCHMARK_TS_CODE.upper()
    if log_sink:
        log_sink.info(f"新浪基准 {ts_code}，区间 {start_date} ~ {end_date}")
    else:
        _log(f"新浪基准 {ts_code}，区间 {start_date} ~ {end_date}。")

    check_cancel(cancel_event)
    try:
        filtered = fetch_index_closes_robust(ts_code, start_date=start_date, end_date=end_date)
    except RuntimeError as exc:
        msg = f"新浪基准失败: {exc}（应急可运行 python scripts/sync_benchmark.py）"
        if log_sink:
            log_sink.error(msg)
        else:
            _log(msg)
        raise

    check_cancel(cancel_event)
    required_dates = set(filtered.keys())
    range_end = max(required_dates, default=end_date)
    existing_dates = fetch_existing_benchmark_dates(ts_code, start_date, range_end)
    missing_dates = required_dates - existing_dates
    if not missing_dates:
        done_msg = f"新浪基准 {ts_code}：库内已有 {len(required_dates)} 个交易日，无需更新"
        if log_sink:
            log_sink.success(done_msg)
        else:
            _log(done_msg + "。")
        return 0

    existing_closes = fetch_existing_benchmark_closes(ts_code, start_date, range_end)
    merged_closes = {**existing_closes, **filtered}
    full_rows = [
        {"ts_code": ts_code, "trade_date": d, "close": float(merged_closes[d])}
        for d in sorted(merged_closes)
        if start_date <= d <= range_end
    ]
    enriched_all = enrich_benchmark_rows(full_rows)
    enriched = [row for row in enriched_all if row["trade_date"] in missing_dates]
    if log_sink:
        log_sink.info(f"  需写入 {len(missing_dates)} 个交易日（库内已有 {len(existing_dates)} 个）")
        for row in enriched:
            pct = row.get("pct_chg")
            cum = row.get("cum_return_pct")
            pct_s = f"{pct:+.2f}%" if pct is not None else "—"
            cum_s = f"{cum:+.2f}%" if cum is not None else "—"
            log_sink.success(
                f"  基准 {row['trade_date']} close={float(row['close']):.2f} 日涨跌={pct_s} 累计={cum_s}"
            )

    inserted = _upsert_benchmark_rows(enriched, already_enriched=True)
    still_missing = verify_benchmark_in_db(ts_code, missing_dates)
    if still_missing:
        msg = f"基准写入后校验失败，库中仍无: {', '.join(still_missing)}"
        if log_sink:
            log_sink.error(msg)
        raise RuntimeError(msg)

    done_msg = f"新浪基准完成，入库 {len(missing_dates)} 日（MySQL 影响行数 {inserted}）"
    if log_sink:
        log_sink.success(done_msg)
    else:
        _log(done_msg + "。")
    return len(missing_dates)


def run_post_import_sync(account_id: int | None = None) -> str | None:
    """导入/雪球更新后的默认同步：仅新浪。"""
    errors: list[str] = []
    codes = fetch_traded_codes(account_id) if account_id else fetch_traded_codes()

    try:
        sync_sina_hfq(codes)
    except Exception as exc:
        errors.append(f"新浪后复权: {exc}")

    try:
        sync_benchmark_from_sina()
    except Exception as exc:
        errors.append(f"新浪基准: {exc}")

    return "; ".join(errors) if errors else None


def run_sync() -> None:
    _log("初始化数据库。")
    init_db()
    sync_sina_hfq()
    sync_benchmark_from_sina()
    _log("新浪同步完成。")


sync_latest_hfq_from_sina = sync_sina_hfq

__all__ = [
    "backfill_price_hfq",
    "fetch_existing_points",
    "fetch_trade_date_range",
    "fetch_trade_points",
    "run_post_import_sync",
    "run_sync",
    "sync_benchmark_from_sina",
    "sync_latest_hfq_from_sina",
    "sync_sina_hfq",
]
