"""
TuShare 辅助同步（可选，不常调用）。

主流程已改用新浪接口，仅在需要补 TuShare 复权因子或基准数据时手动执行。
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Any, Callable

import tushare as ts
from sqlalchemy.dialects.mysql import insert as mysql_insert

from xueqiu.config import BENCHMARK_TS_CODE, TUSHARE_API_KEY, TUSHARE_HTTP_URL
from xueqiu.domain.benchmark_series import enrich_benchmark_rows
from xueqiu.domain.codes import to_tushare_code, to_xueqiu_code
from xueqiu.storage.db import benchmark_table, get_conn, init_db, quote_points_table
from xueqiu.sync.sync_quotes import (
    backfill_price_hfq,
    fetch_existing_points,
    fetch_trade_date_range,
    fetch_trade_points,
)


def _log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def _require_tushare() -> ts.pro_api:
    if not TUSHARE_API_KEY:
        raise RuntimeError("缺少 TUSHARE_API_KEY。")
    pro = ts.pro_api(TUSHARE_API_KEY)
    if TUSHARE_HTTP_URL:
        pro._DataApi__http_url = TUSHARE_HTTP_URL
    return pro


def _fetch_with_retry(func: Callable[..., Any], max_retries: int = 3, **kwargs: Any) -> Any:
    for attempt in range(1, max_retries + 1):
        try:
            return func(**kwargs)
        except Exception as exc:
            if attempt == max_retries:
                raise exc
            _log(f"  [重试 {attempt}/{max_retries}] {exc}")
            time.sleep(1)


def _upsert_adj_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = mysql_insert(quote_points_table).values(rows)
    stmt = stmt.on_duplicate_key_update(adj_factor=stmt.inserted.adj_factor)
    with get_conn() as conn:
        conn.execute(stmt)
    return len(rows)


def _upsert_benchmark_rows(rows: list[dict[str, Any]]) -> int:
    from xueqiu.sync.sync_quotes import _upsert_benchmark_rows as upsert_rows

    return upsert_rows(rows, already_enriched=False)


def sync_trade_adj_factors() -> None:
    points = fetch_trade_points()
    if not points:
        _log("没有交易记录，跳过。")
        return

    existing = fetch_existing_points()
    pending = [point for point in points if point not in existing]
    if not pending:
        _log(f"全部 {len(points)} 个交易点已有复权因子。")
        return

    by_date: dict[str, set[str]] = {}
    for ts_code, trade_date in pending:
        by_date.setdefault(trade_date, set()).add(ts_code)

    pro = _require_tushare()
    total_rows = 0
    for index, (trade_date, codes) in enumerate(sorted(by_date.items()), start=1):
        try:
            adj_df = _fetch_with_retry(pro.adj_factor, trade_date=trade_date)
        except Exception as exc:
            _log(f"[{index}/{len(by_date)}] {trade_date} 失败: {exc}")
            continue

        rows: list[dict[str, Any]] = []
        if adj_df is not None and not adj_df.empty:
            adj_df["ts_code"] = adj_df["ts_code"].astype(str).str.strip().str.upper()
            target = {to_tushare_code(code) for code in codes}
            for item in adj_df[adj_df["ts_code"].isin(target)].to_dict(orient="records"):
                rows.append(
                    {
                        "ts_code": to_xueqiu_code(str(item["ts_code"])),
                        "trade_date": trade_date,
                        "adj_factor": float(item["adj_factor"]),
                        "close_hfq": None,
                    }
                )

        for code in codes - {row["ts_code"] for row in rows}:
            rows.append({"ts_code": code, "trade_date": trade_date, "adj_factor": 1.0, "close_hfq": None})

        total_rows += _upsert_adj_rows(rows)

    _log(f"TuShare 复权因子写入 {total_rows} 条。")


def sync_benchmark_from_tushare(*, sink: Any | None = None, cancel_event: Any | None = None) -> int:
    import threading

    from xueqiu.sync.sync_cancel import check_cancel
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None
    check_cancel(cancel)

    date_range = fetch_trade_date_range()
    if not date_range:
        msg = "没有交易记录，跳过 TuShare 基准。"
        if log_sink:
            log_sink.warn(msg)
        else:
            _log(msg)
        return 0

    start_date, end_date = date_range
    ts_code = BENCHMARK_TS_CODE.upper()
    if log_sink:
        log_sink.info(f"TuShare 基准 {ts_code}，区间 {start_date} ~ {end_date}")
    else:
        _log(f"TuShare 基准 {ts_code}，区间 {start_date} ~ {end_date}")

    pro = _require_tushare()
    df = _fetch_with_retry(pro.index_daily, ts_code=ts_code, start_date=start_date, end_date=end_date)
    check_cancel(cancel)
    if df is None or df.empty:
        msg = "TuShare 基准无数据。"
        if log_sink:
            log_sink.error(msg)
        else:
            _log(msg)
        raise RuntimeError(msg)

    rows = [
        {"ts_code": ts_code, "trade_date": str(item["trade_date"]).strip(), "close": float(item["close"])}
        for item in df.to_dict(orient="records")
        if item.get("close") is not None
    ]
    enriched = enrich_benchmark_rows(rows)
    if log_sink and len(enriched) <= 15:
        for row in enriched:
            pct = row.get("pct_chg")
            cum = row.get("cum_return_pct")
            pct_s = f"{pct:+.2f}%" if pct is not None else "—"
            cum_s = f"{cum:+.2f}%" if cum is not None else "—"
            log_sink.success(
                f"  基准 {row['trade_date']} close={float(row['close']):.2f} 日涨跌={pct_s} 累计={cum_s}"
            )
    elif log_sink:
        log_sink.info(f"  TuShare 返回 {len(enriched)} 个交易日（明细省略）")

    inserted = _upsert_benchmark_rows(enriched)
    done_msg = f"TuShare 基准完成，写入 {inserted} 条"
    if log_sink:
        log_sink.success(done_msg)
    else:
        _log(done_msg)
    return inserted


def run(*, adj: bool = True, benchmark: bool = True) -> None:
    init_db()
    if adj:
        sync_trade_adj_factors()
        backfill_price_hfq()
    if benchmark:
        sync_benchmark_from_tushare()


def main() -> None:
    parser = argparse.ArgumentParser(description="TuShare 辅助同步（可选）")
    parser.add_argument("--adj-only", action="store_true")
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()
    run(
        adj=not args.benchmark_only,
        benchmark=not args.adj_only,
    )


if __name__ == "__main__":
    main()
