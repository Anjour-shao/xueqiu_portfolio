from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from xueqiu.storage.db import accounts_table, cube_nav_points_table, get_conn, init_db, rebalance_trades_table
from xueqiu.domain.nav_engine import TradeInput, compute_pseudo_nav, fetch_holding_codes_for_account
from xueqiu.domain.holdings_snapshot import attach_curve_extras
from xueqiu.domain.official_nav import load_official_equity_curve
from xueqiu.integrations.xueqiu.portfolio import (
    fetch_all_portfolios_rebalance,
    fetch_portfolio_rebalance,
    fetch_portfolio_rebalance_all,
    validate_portfolio_id,
)
from xueqiu.sync.sync_log import LogSink
from xueqiu.sync.sync_quotes import run_post_import_sync, sync_latest_hfq_from_sina
from xueqiu.sync.sync_cube_nav import sync_cube_nav_for_account

PORTFOLIO_CODE_RE = re.compile(r"^ZH\d+$", re.IGNORECASE)


@dataclass
class TradeRecord:
    id: int
    trade_time: str
    stock_name: str
    ts_code: str
    action: str
    from_weight: float
    to_weight: float
    weight_delta: float
    price: float | None
    price_hfq: float | None


def _to_input(trade: TradeRecord) -> TradeInput:
    return TradeInput(
        id=trade.id,
        trade_time=trade.trade_time,
        stock_name=trade.stock_name,
        ts_code=trade.ts_code,
        action=trade.action,
        from_weight=trade.from_weight,
        to_weight=trade.to_weight,
        weight_delta=trade.weight_delta,
        price=trade.price,
        price_hfq=trade.price_hfq,
    )


def compute_account_returns(trades: list[TradeRecord]) -> dict[str, Any]:
    return compute_pseudo_nav([_to_input(t) for t in trades])


def _merge_official_nav(metrics: dict[str, Any], account_id: int) -> dict[str, Any]:
    from xueqiu.domain.risk_metrics import compute_risk_metrics

    official = load_official_equity_curve(account_id)
    if not official.get("has_official"):
        return metrics
    metrics = dict(metrics)
    overview = dict(metrics.get("overview") or {})
    overview.update(official.get("overview_patch") or {})
    overview["nav_source"] = "official"
    curve = official["curve"]
    overview.update(compute_risk_metrics(curve))
    metrics["overview"] = overview
    metrics["equity_curve"] = curve
    metrics["daily_nav"] = curve
    metrics["nav_source"] = "official"
    return metrics


def _row_to_account(row: Any) -> dict[str, Any]:
    account_code = row.account_code or str(row.id)
    return {
        "id": str(account_code),
        "name": str(row.account_name),
        "internal_id": int(row.id),
    }


def _fetch_accounts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_code, accounts_table.c.account_name).order_by(
                accounts_table.c.account_name.asc()
            )
        ).fetchall()
    return [_row_to_account(row) for row in rows]


def _resolve_account(account_key: str) -> tuple[int, str]:
    with get_conn() as conn:
        row = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_name)
            .where((accounts_table.c.account_code == account_key) | (accounts_table.c.account_name == account_key))
            .limit(1)
        ).fetchone()
    if row is None:
        raise ValueError(f"账户不存在: {account_key}")
    return int(row.id), str(row.account_name)


def _fetch_trades(account_id: int) -> list[TradeRecord]:
    with get_conn() as conn:
        rows = conn.execute(
            select(
                rebalance_trades_table.c.id,
                rebalance_trades_table.c.trade_time,
                rebalance_trades_table.c.stock_name,
                rebalance_trades_table.c.ts_code,
                rebalance_trades_table.c.action,
                rebalance_trades_table.c.from_weight,
                rebalance_trades_table.c.to_weight,
                rebalance_trades_table.c.weight_delta,
                rebalance_trades_table.c.price,
                rebalance_trades_table.c.price_hfq,
            )
            .where(rebalance_trades_table.c.account_id == account_id)
            .order_by(rebalance_trades_table.c.trade_time.asc(), rebalance_trades_table.c.id.asc())
        ).fetchall()
    return [TradeRecord(**dict(row._mapping)) for row in rows]


def list_accounts() -> list[dict[str, Any]]:
    return _fetch_accounts()


def get_dashboard(account_key: str) -> dict[str, Any]:
    account_id, account_name = _resolve_account(account_key)
    trades = _fetch_trades(account_id)
    metrics = compute_account_returns(trades)
    metrics = _merge_official_nav(metrics, account_id)
    curve = metrics.get("equity_curve")
    if isinstance(curve, list) and curve:
        metrics["equity_curve"] = attach_curve_extras(curve, trades)
        metrics["daily_nav"] = metrics["equity_curve"]
    return {
        "account": account_name,
        **metrics,
    }


def recompute_returns(account_key: str) -> dict[str, Any]:
    account_id, account_name = _resolve_account(account_key)
    trades = _fetch_trades(account_id)
    metrics = _merge_official_nav(compute_account_returns(trades), account_id)
    overview = metrics["overview"]
    return {
        "account": account_name,
        "trade_count": overview["trade_count"],
        "cum_return_pct": overview["cum_return_pct"],
        "realized_return_pct": overview["realized_return_pct"],
        "unrealized_return_pct": overview["unrealized_return_pct"],
        "holding_count": overview["holding_count"],
    }


def upsert_account(account_code: str, account_name: str) -> int:
    with get_conn() as conn:
        existing = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_name)
            .where(accounts_table.c.account_code == account_code)
            .limit(1)
        ).fetchone()
        if existing is not None:
            conn.execute(
                accounts_table.update()
                .where(accounts_table.c.id == existing.id)
                .values(account_name=account_name)
            )
            return int(existing.id)

        conflict_name = conn.execute(
            select(accounts_table.c.id, accounts_table.c.account_code)
            .where(accounts_table.c.account_name == account_name)
            .limit(1)
        ).fetchone()
        if conflict_name is not None:
            raise ValueError(
                f"账户名称已存在且绑定了不同 account_code: {account_name} -> {conflict_name.account_code}"
            )

        result = conn.execute(
            accounts_table.insert().values(account_code=account_code, account_name=account_name)
        )
        return int(result.inserted_primary_key[0])


def _trade_identity_key(trade: dict[str, Any]) -> tuple[Any, ...]:
    return (
        trade["trade_time"],
        trade["ts_code"],
        float(trade["from_weight"]),
        float(trade["to_weight"]),
        trade.get("price"),
    )


def _fetch_db_sync_context(account_db_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        latest_row = conn.execute(
            select(rebalance_trades_table.c.trade_time)
            .where(rebalance_trades_table.c.account_id == account_db_id)
            .order_by(rebalance_trades_table.c.trade_time.desc(), rebalance_trades_table.c.id.desc())
            .limit(1)
        ).fetchone()
        count_row = conn.execute(
            select(rebalance_trades_table.c.id).where(rebalance_trades_table.c.account_id == account_db_id)
        ).fetchall()
        identity_rows = conn.execute(
            select(
                rebalance_trades_table.c.trade_time,
                rebalance_trades_table.c.ts_code,
                rebalance_trades_table.c.from_weight,
                rebalance_trades_table.c.to_weight,
                rebalance_trades_table.c.price,
            ).where(rebalance_trades_table.c.account_id == account_db_id)
        ).fetchall()

    identities = {
        (r.trade_time, r.ts_code, float(r.from_weight), float(r.to_weight), r.price) for r in identity_rows
    }
    return {
        "db_trade_count": len(count_row),
        "db_latest_trade_time": str(latest_row.trade_time) if latest_row else None,
        "existing_identities": identities,
    }


def import_trades(account_code: str, account_name: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
    detailed = import_trades_incremental(account_code, account_name, trades)
    return {
        "account_id": detailed["account_id"],
        "account_name": detailed["account_name"],
        "inserted_count": detailed["inserted_count"],
        "skipped_duplicates": detailed["skipped_duplicates"],
        "total_received": detailed["total_received"],
    }


def import_trades_incremental(
    account_code: str,
    account_name: str,
    trades: list[dict[str, Any]],
    *,
    rebalance_time: str | None = None,
) -> dict[str, Any]:
    """与库内全量记录去重后增量写入（非仅比对当日）。"""
    account_db_id = upsert_account(account_code, account_name)
    ctx = _fetch_db_sync_context(account_db_id)
    existing: set[tuple[Any, ...]] = set(ctx["existing_identities"])

    logs: list[dict[str, str]] = [
        {"level": "info", "message": f"账户 {account_name}（{account_code}）"},
        {
            "level": "info",
            "message": f"库内已有 {ctx['db_trade_count']} 条调仓，最近一批时间：{ctx['db_latest_trade_time'] or '无'}",
        },
        {
            "level": "info",
            "message": "增量规则：按「调仓时间 + 代码 + 仓位 + 价格」与库内全量比对，已存在则跳过",
        },
    ]
    if rebalance_time:
        in_db_batch = sum(1 for key in existing if key[0] == rebalance_time)
        logs.append(
            {
                "level": "info",
                "message": f"雪球本批调仓时间 {rebalance_time}，库内同时间已有 {in_db_batch} 条",
            }
        )

    trade_results: list[dict[str, Any]] = []
    inserted = 0

    with get_conn() as conn:
        for trade in trades:
            key = _trade_identity_key(trade)
            row_base = {
                "stock_name": trade["stock_name"],
                "ts_code": trade["ts_code"],
                "action": trade["action"],
                "from_weight": float(trade["from_weight"]),
                "to_weight": float(trade["to_weight"]),
                "price": trade.get("price"),
            }
            if key in existing:
                trade_results.append({**row_base, "status": "skipped"})
                logs.append(
                    {
                        "level": "info",
                        "message": (
                            f"  跳过 {trade['stock_name']}({trade['ts_code']}) "
                            f"{float(trade['from_weight']):.0f}→{float(trade['to_weight']):.0f}%"
                        ),
                    }
                )
                continue
            try:
                result = conn.execute(
                    rebalance_trades_table.insert().values(account_id=account_db_id, **trade)
                )
                if result.rowcount:
                    inserted += result.rowcount
                    existing.add(key)
                    trade_results.append({**row_base, "status": "inserted"})
                    price_note = f" @{trade.get('price')}" if trade.get("price") else ""
                    logs.append(
                        {
                            "level": "success",
                            "message": (
                                f"  写入 {trade['stock_name']}({trade['ts_code']}) "
                                f"{trade['action']} {float(trade['from_weight']):.0f}→{float(trade['to_weight']):.0f}%"
                                f"{price_note}"
                            ),
                        }
                    )
                else:
                    trade_results.append({**row_base, "status": "skipped"})
                    logs.append(
                        {
                            "level": "warn",
                            "message": f"  未写入 {trade['stock_name']}({trade['ts_code']})",
                        }
                    )
            except IntegrityError:
                existing.add(key)
                trade_results.append({**row_base, "status": "skipped"})
                logs.append(
                    {
                        "level": "warn",
                        "message": f"  冲突跳过 {trade['stock_name']}({trade['ts_code']})",
                    }
                )

    skipped = len(trades) - inserted
    if inserted > 0:
        logs.append({"level": "success", "message": f"新增写入 {inserted} 条"})
    if skipped > 0:
        logs.append({"level": "warn", "message": f"跳过重复 {skipped} 条（库内已有相同记录）"})
    if not trades:
        logs.append({"level": "warn", "message": "本批无可写入记录"})

    return {
        "account_id": account_code,
        "account_name": account_name,
        "inserted_count": inserted,
        "skipped_duplicates": skipped,
        "total_received": len(trades),
        "db_trade_count": ctx["db_trade_count"] + inserted,
        "db_latest_trade_time": ctx["db_latest_trade_time"],
        "logs": logs,
        "trade_results": trade_results,
    }


def sync_latest_hfq_prices(account_key: str) -> dict[str, Any]:
    account_id, account_name = _resolve_account(account_key)
    codes = fetch_holding_codes_for_account(account_id)
    if not codes:
        metrics = get_dashboard(account_key)
        return {
            "account": account_name,
            "synced_count": 0,
            "holding_count": 0,
            "message": "当前无持仓，无需刷新最新价。",
            "overview": metrics["overview"],
        }

    synced = sync_latest_hfq_from_sina(codes)
    metrics = get_dashboard(account_key)
    return {
        "account": account_name,
        "synced_count": synced,
        "holding_count": len(codes),
        "message": f"已刷新 {synced} 只持仓的最新后复权价。",
        "overview": metrics["overview"],
    }


def list_xueqiu_portfolio_codes() -> list[str]:
    return sorted(a["id"] for a in _fetch_accounts() if PORTFOLIO_CODE_RE.match(str(a["id"])))


def _crawl_summary_logs(crawled: dict[str, Any]) -> list[dict[str, str]]:
    trades = crawled["trades"]
    rebalance_time = crawled["rebalance_time"]
    crawled_count = len(trades)
    sell_n = sum(1 for t in trades if t["action"] in ("SELL", "DECREASE"))
    buy_n = sum(1 for t in trades if t["action"] in ("BUY", "INCREASE"))
    logs = [
        {
            "level": "success",
            "message": (
                f"已读取「最新调仓」{rebalance_time}：共 {crawled_count} 条"
                f"（买入/加仓 {buy_n}，卖出/减仓 {sell_n}）"
            ),
        },
    ]
    skipped = int(crawled.get("parse_skipped") or 0)
    if skipped > 0:
        logs.append({"level": "warn", "message": f"页面另有 {skipped} 条未能解析，已跳过"})
    return logs


def _sync_crawled_batch(
    crawled: dict[str, Any],
    logs: list[dict[str, str]],
    *,
    skip_followup_sync: bool = False,
) -> dict[str, Any]:
    portfolio_id = crawled["portfolio_id"]
    rebalance_time = crawled["rebalance_time"]
    crawled_count = len(crawled["trades"])

    logs.extend(_crawl_summary_logs(crawled))

    import_result = import_trades_incremental(
        account_code=crawled["portfolio_id"],
        account_name=crawled["portfolio_name"],
        trades=crawled["trades"],
        rebalance_time=rebalance_time,
    )
    logs.extend(import_result["logs"])

    account_id, _ = _resolve_account(portfolio_id)
    adj_error: str | None = None
    nav_error: str | None = None
    if skip_followup_sync:
        logs.append({"level": "info", "message": "全量流水线模式：行情与官方净值将在后续步骤统一同步"})
    elif import_result["inserted_count"] > 0:
        logs.append({"level": "info", "message": "正在同步本批涉及标的的后复权行情…"})
        adj_error = run_post_import_sync(account_id)
        if adj_error:
            logs.append({"level": "error", "message": f"行情同步失败：{adj_error}"})
        else:
            logs.append({"level": "success", "message": "行情同步完成"})
    else:
        logs.append({"level": "info", "message": "无新增记录，跳过行情同步"})

    if not skip_followup_sync:
        try:
            nav_sync = sync_cube_nav_for_account(account_id)
            logs.append(
                {
                    "level": "success",
                    "message": (
                        f"官方净值已同步 {nav_sync.get('point_count', 0)} 点"
                        f"（最新 {nav_sync.get('latest_date', '-')}）"
                    ),
                }
            )
        except Exception as exc:
            nav_error = str(exc)
            logs.append({"level": "error", "message": f"官方净值同步失败：{nav_error}"})

    metrics = get_dashboard(portfolio_id) if not skip_followup_sync else {"overview": {}}

    if import_result["inserted_count"] > 0:
        message = f"已增量写入 {import_result['inserted_count']} 条（{rebalance_time}）。"
    elif import_result["skipped_duplicates"] > 0:
        message = f"本批 {rebalance_time} 均已存在于库中，无新增。"
    else:
        message = "抓取完成，但未写入任何记录。"
    if adj_error:
        message += f" 行情同步失败: {adj_error}"
    if nav_error:
        message += f" 官方净值同步失败: {nav_error}"

    return {
        "ok": True,
        "account_id": import_result["account_id"],
        "account_name": import_result["account_name"],
        "rebalance_time": rebalance_time,
        "crawled_count": crawled_count,
        "inserted_count": import_result["inserted_count"],
        "skipped_duplicates": import_result["skipped_duplicates"],
        "total_received": import_result["total_received"],
        "db_trade_count": import_result["db_trade_count"],
        "db_latest_trade_time": import_result["db_latest_trade_time"],
        "logs": logs,
        "trade_results": import_result["trade_results"],
        "adj_sync_ok": adj_error is None,
        "nav_sync_ok": nav_error is None,
        "message": message,
        "overview": metrics["overview"],
    }


def sync_from_xueqiu(
    account_key: str,
    *,
    latest_only: bool = False,
    sink: LogSink | None = None,
) -> dict[str, Any]:
    portfolio_id = validate_portfolio_id(account_key)
    log = sink if sink is not None else LogSink()
    log.info(f"开始连接雪球组合 {portfolio_id}…")

    if latest_only:
        log.info("拉取最新一批调仓…")
        crawled = fetch_portfolio_rebalance(portfolio_id)
        result = _sync_crawled_batch(crawled, log.lines)
        log.success("看板数据已刷新")
        result["logs"] = log.lines
        return result

    log.info("正在分页拉取全部历史调仓（调仓越多页数越多，遇限流会自动重试）…")

    def _on_page(page: int, total_batches: int) -> None:
        log.info(f"第 {page} 页已解析，累计 {total_batches} 批调仓")

    batches = fetch_portfolio_rebalance_all(portfolio_id, on_progress=_on_page)
    if not batches:
        raise RuntimeError(f"组合 {portfolio_id} 未找到可入库的手动调仓记录。")

    portfolio_name = str(batches[0]["portfolio_name"])
    log.info(f"共 {len(batches)} 次调仓待入库（从旧到新）…")

    total_inserted = 0
    total_skipped = 0
    total_crawled = 0
    last_rebalance_time = batches[-1]["rebalance_time"]

    for idx, crawled in enumerate(batches, start=1):
        log.info(f"── 入库 {idx}/{len(batches)}：{crawled['rebalance_time']} ──")
        for line in _crawl_summary_logs(crawled):
            log.add(line["level"], line["message"])
        import_result = import_trades_incremental(
            account_code=crawled["portfolio_id"],
            account_name=crawled["portfolio_name"],
            trades=crawled["trades"],
            rebalance_time=crawled["rebalance_time"],
        )
        for line in import_result["logs"]:
            log.add(line["level"], line["message"])
        total_inserted += int(import_result["inserted_count"])
        total_skipped += int(import_result["skipped_duplicates"])
        total_crawled += len(crawled["trades"])

    account_id, _ = _resolve_account(portfolio_id)
    adj_error: str | None = None
    nav_error: str | None = None

    if total_inserted > 0:
        log.info("正在同步本组合涉及标的的后复权行情…")
        adj_error = run_post_import_sync(account_id)
        if adj_error:
            log.error(f"行情同步失败：{adj_error}")
        else:
            log.success("行情同步完成")
    else:
        log.info("无新增调仓记录，跳过行情同步")

    try:
        nav_sync = sync_cube_nav_for_account(account_id)
        log.success(
            f"官方净值已同步 {nav_sync.get('point_count', 0)} 点"
            f"（最新 {nav_sync.get('latest_date', '-')}）"
        )
    except Exception as exc:
        nav_error = str(exc)
        log.error(f"官方净值同步失败：{nav_error}")

    metrics = get_dashboard(portfolio_id)
    if total_inserted > 0:
        message = f"全量同步完成：{len(batches)} 次调仓，新增 {total_inserted} 条。"
    elif total_skipped > 0:
        message = f"全量同步完成：{len(batches)} 次调仓均已存在，无新增。"
    else:
        message = "全量同步完成，但未写入任何记录。"
    if adj_error:
        message += f" 行情同步失败: {adj_error}"
    if nav_error:
        message += f" 官方净值同步失败: {nav_error}"

    log.success("看板数据已刷新")
    return {
        "ok": True,
        "account_id": portfolio_id,
        "account_name": portfolio_name,
        "rebalance_time": last_rebalance_time,
        "crawled_count": total_crawled,
        "inserted_count": total_inserted,
        "skipped_duplicates": total_skipped,
        "total_received": total_crawled,
        "db_trade_count": metrics["overview"].get("trade_count", 0),
        "db_latest_trade_time": metrics["overview"].get("latest_trade_time"),
        "logs": log.lines,
        "trade_results": [],
        "adj_sync_ok": adj_error is None,
        "nav_sync_ok": nav_error is None,
        "message": message,
        "overview": metrics["overview"],
    }


def iter_sync_xueqiu_stream(account_key: str, cancel_event: Any | None = None):
    import json
    import queue
    import threading

    cancel = cancel_event if isinstance(cancel_event, threading.Event) else threading.Event()
    event_queue: queue.Queue = queue.Queue()

    def emit(item: dict[str, Any]) -> None:
        event_queue.put(item)

    def worker() -> None:
        sink = LogSink(emit=emit)
        try:
            if cancel.is_set():
                raise RuntimeError("用户已取消")
            result = sync_from_xueqiu(account_key, sink=sink)
            if cancel.is_set():
                emit({"type": "done", "ok": False, "message": "用户已取消"})
            else:
                emit({"type": "done", "ok": True, "result": result})
        except Exception as exc:
            sink.error(str(exc))
            emit({"type": "done", "ok": False, "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        if cancel.is_set():
            try:
                while True:
                    item = event_queue.get_nowait()
                    if item is None:
                        return
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except queue.Empty:
                return
        try:
            item = event_queue.get(timeout=0.3)
        except queue.Empty:
            continue
        if item is None:
            break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


def delete_account(account_key: str) -> dict[str, Any]:
    account_id, account_code = _resolve_account(account_key)
    with get_conn() as conn:
        trades_deleted = conn.execute(
            delete(rebalance_trades_table).where(rebalance_trades_table.c.account_id == account_id)
        ).rowcount
        nav_deleted = conn.execute(
            delete(cube_nav_points_table).where(cube_nav_points_table.c.account_id == account_id)
        ).rowcount
        account_deleted = conn.execute(
            delete(accounts_table).where(accounts_table.c.id == account_id)
        ).rowcount
        conn.commit()

    if not account_deleted:
        raise ValueError(f"账户不存在: {account_key}")

    return {
        "ok": True,
        "account_code": account_code,
        "trades_deleted": int(trades_deleted or 0),
        "nav_points_deleted": int(nav_deleted or 0),
        "message": f"已删除组合 {account_code}（调仓 {trades_deleted or 0} 条，净值 {nav_deleted or 0} 点）",
    }


def sync_all_from_xueqiu(
    *,
    sink: Any | None = None,
    skip_followup_sync: bool = False,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    import threading

    from xueqiu.sync.sync_cancel import check_cancel
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else None
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None
    codes = list_xueqiu_portfolio_codes()
    if not codes:
        raise ValueError("没有可同步的雪球组合（账户 ID 需为 ZH 开头，请先导入或添加账户）")

    logs: list[dict[str, str]] = []
    intro = f"全量同步：将依次更新 {len(codes)} 个雪球组合"
    logs.append({"level": "info", "message": intro})
    logs.append({"level": "info", "message": "、".join(codes)})
    if log_sink:
        log_sink.info(intro)
        log_sink.info("、".join(codes))

    batch = fetch_all_portfolios_rebalance(codes)

    accounts: list[dict[str, Any]] = []
    total_inserted = 0
    total_crawled = 0
    failed = 0

    for item in batch:
        check_cancel(cancel)
        pid = item["portfolio_id"]
        if not item.get("ok"):
            failed += 1
            err = str(item.get("error") or "未知错误")
            logs.append({"level": "error", "message": f"[{pid}] 抓取失败：{err}"})
            if log_sink:
                log_sink.error(f"[{pid}] 抓取失败：{err}")
            accounts.append(
                {
                    "ok": False,
                    "account_id": pid,
                    "account_name": pid,
                    "message": err,
                    "logs": [{"level": "error", "message": err}],
                    "inserted_count": 0,
                    "skipped_duplicates": 0,
                    "crawled_count": 0,
                    "trade_results": [],
                }
            )
            continue

        crawled = item["data"]
        acc_logs: list[dict[str, str]] = [{"level": "info", "message": f"── {pid} {crawled.get('portfolio_name', '')} ──"}]
        try:
            one = _sync_crawled_batch(crawled, acc_logs, skip_followup_sync=skip_followup_sync)
            total_inserted += one["inserted_count"]
            total_crawled += one["crawled_count"]
            accounts.append(one)
            logs.extend(acc_logs)
            if log_sink:
                for line in acc_logs:
                    log_sink.add(line["level"], line["message"])
            logs.append(
                {
                    "level": "success" if one["inserted_count"] > 0 else "info",
                    "message": f"[{pid}] {one['message']}",
                }
            )
        except Exception as exc:
            failed += 1
            err = str(exc)
            logs.append({"level": "error", "message": f"[{pid}] 入库失败：{err}"})
            logs.extend(acc_logs)
            if log_sink:
                for line in acc_logs:
                    log_sink.add(line["level"], line["message"])
                log_sink.error(f"[{pid}] 入库失败：{err}")
            accounts.append(
                {
                    "ok": False,
                    "account_id": pid,
                    "account_name": crawled.get("portfolio_name", pid),
                    "message": err,
                    "logs": acc_logs + [{"level": "error", "message": err}],
                    "inserted_count": 0,
                    "skipped_duplicates": 0,
                    "crawled_count": len(crawled.get("trades", [])),
                    "trade_results": [],
                }
            )

    summary = (
        f"全量同步完成：{len(codes)} 个组合，新增 {total_inserted} 条，"
        f"抓取 {total_crawled} 条变动，失败 {failed} 个。"
    )
    logs.append({"level": "success", "message": summary})
    if log_sink:
        log_sink.success(summary)

    return {
        "account_count": len(codes),
        "failed_count": failed,
        "total_inserted": total_inserted,
        "total_crawled": total_crawled,
        "logs": logs,
        "accounts": accounts,
        "message": summary,
    }


def get_portfolios_overview() -> dict[str, Any]:
    from xueqiu.domain.overview_light import load_portfolios_overview_items

    return {"items": load_portfolios_overview_items()}


def get_data_freshness() -> dict[str, Any]:
    from xueqiu.domain.data_freshness import load_data_freshness

    return load_data_freshness()


def get_portfolios_overview_stats() -> dict[str, Any]:
    from xueqiu.domain.overview_stats import load_portfolios_overview_stats

    return load_portfolios_overview_stats()


def sync_quotes_data(*, sink: Any | None = None, cancel_event: Any | None = None) -> dict[str, Any]:
    import threading

    from xueqiu.storage.db import init_db
    from xueqiu.sync.sync_cancel import SyncCancelled
    from xueqiu.sync.sync_log import LogSink
    from xueqiu.sync.sync_quotes import sync_benchmark_from_sina, sync_sina_hfq

    log_sink = sink if isinstance(sink, LogSink) else LogSink()
    use_external_sink = isinstance(sink, LogSink)
    logs = log_sink.lines if not use_external_sink else sink.lines  # type: ignore[union-attr]
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None

    init_db()
    errors: list[str] = []
    hfq_count = 0
    bench_count = 0

    try:
        hfq_count = int(sync_sina_hfq(sink=log_sink, cancel_event=cancel) or 0)
    except SyncCancelled:
        raise
    except Exception as exc:
        errors.append(str(exc))
        log_sink.error(f"新浪后复权失败: {exc}")

    try:
        bench_count = int(sync_benchmark_from_sina(sink=log_sink, cancel_event=cancel) or 0)
    except SyncCancelled:
        raise
    except Exception as exc:
        errors.append(str(exc))
        log_sink.error(f"基准同步失败: {exc}")

    ok = not errors
    message = "新浪行情同步完成" if ok else f"部分失败: {'; '.join(errors)}"
    if ok:
        log_sink.success(message)
    else:
        log_sink.error(message)
    return {
        "ok": ok,
        "message": message,
        "logs": logs,
        "hfq_count": hfq_count,
        "benchmark_count": bench_count,
    }


def sync_cube_nav_all(*, sink: Any | None = None, cancel_event: Any | None = None) -> dict[str, Any]:
    import threading

    from xueqiu.sync.sync_log import LogSink
    from xueqiu.sync.sync_cube_nav import sync_all_cube_nav

    log_sink = sink if isinstance(sink, LogSink) else LogSink()
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None
    use_external_sink = isinstance(sink, LogSink)
    logs = log_sink.lines if not use_external_sink else sink.lines  # type: ignore[union-attr]

    result = sync_all_cube_nav(sink=log_sink, cancel_event=cancel)
    msg = str(result.get("message", ""))
    log_sink.info(msg)

    failed_count = int(result.get("failed_count") or 0)
    summary = msg if failed_count == 0 else f"{msg}（{failed_count} 个失败）"
    if failed_count == 0:
        log_sink.success(summary)
    else:
        log_sink.warn(summary)
    return {
        "ok": failed_count == 0,
        "message": summary,
        "logs": logs,
        "account_count": int(result.get("account_count") or 0),
        "ok_count": int(result.get("ok_count") or 0),
        "failed_count": failed_count,
        "results": result.get("results") or [],
    }


def run_sync_all_pipeline(sink: Any, *, cancel_event: Any | None = None) -> dict[str, Any]:
    import threading

    from xueqiu.storage.db import init_db
    from xueqiu.sync.sync_cancel import check_cancel
    from xueqiu.sync.sync_log import LogSink

    if not isinstance(sink, LogSink):
        raise TypeError("sink must be LogSink")
    cancel = cancel_event if isinstance(cancel_event, threading.Event) else None
    init_db()
    sink.info("▶ 开始一键全量同步（调仓 → 行情 → 官方净值）")

    sink.info("── 1/3 雪球调仓 ──")
    xueqiu = sync_all_from_xueqiu(sink=sink, skip_followup_sync=True, cancel_event=cancel)
    check_cancel(cancel)

    sink.info("── 2/3 新浪行情（后复权 → 基准指数）──")
    quotes = sync_quotes_data(sink=sink, cancel_event=cancel)
    check_cancel(cancel)

    sink.info("── 3/3 官方净值 ──")
    cube = sync_cube_nav_all(sink=sink, cancel_event=cancel)

    ok = quotes.get("ok", False) and cube.get("ok", False) and int(xueqiu.get("failed_count") or 0) == 0
    if ok:
        sink.success("■ 全量同步完成")
    else:
        sink.warn("■ 全量同步完成，但存在失败项，请查看上方日志")
    return {"ok": ok, "xueqiu": xueqiu, "quotes": quotes, "cube_nav": cube}


def get_discovery_stats() -> dict[str, Any]:
    from xueqiu.domain.discovery_store import get_discovery_stats

    return get_discovery_stats()


def list_discovery_cubes(
    *,
    auto_pass: bool | None = None,
    selected: int | None = None,
    pending_only: bool = False,
    depth: int | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    from xueqiu.domain.discovery_store import list_mined_cubes

    return list_mined_cubes(
        auto_pass=auto_pass,
        selected=selected,
        pending_only=pending_only,
        depth=depth,
        q=q,
    )


def patch_discovery_cube(
    account_code: str,
    *,
    selected: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    from xueqiu.domain.discovery_store import update_mined_cube_selection

    return update_mined_cube_selection(account_code, selected=selected, note=note)


def get_discovery_cube_preview(account_code: str) -> dict[str, Any]:
    from xueqiu.domain.discovery_preview import build_discovery_cube_preview
    from xueqiu.integrations.xueqiu.client import XueQiuApiError

    try:
        return build_discovery_cube_preview(account_code)
    except XueQiuApiError as exc:
        raise RuntimeError(str(exc)) from exc


def import_discovery_cube(account_code: str, *, sink: Any | None = None) -> dict[str, Any]:
    import random
    import time

    from xueqiu.domain.discovery_store import mark_mined_cube_imported, update_mined_cube_selection
    from xueqiu.integrations.xueqiu.client import XueQiuApiError
    from xueqiu.sync.sync_log import LogSink

    code = account_code.strip().upper()
    if not code:
        raise ValueError("组合代码不能为空")

    log = sink if isinstance(sink, LogSink) else LogSink()
    row = update_mined_cube_selection(code, selected=1)
    if row.get("imported_at"):
        msg = f"组合 {code} 已入库"
        log.info(msg)
        return {
            "ok": True,
            "message": msg,
            "account_code": code,
            "sync": {},
        }

    name = str(row.get("account_name") or code)
    log.info(f"▶ 开始入库 {code}（{name}）…")
    time.sleep(random.uniform(1.2, 2.5))
    try:
        sync_result = sync_from_xueqiu(code, sink=log)
    except XueQiuApiError as exc:
        msg = str(exc)
        if "Cookie 已失效" in msg or "登录态失效" in msg or "400016" in msg:
            raise RuntimeError(msg) from exc
        if any(token in msg for token in ("400", "429", "502", "503")):
            raise RuntimeError(f"雪球限流或暂不可用，请稍后重试：{msg}") from exc
        raise RuntimeError(msg) from exc

    mark_mined_cube_imported(code)
    message = str(sync_result.get("message") or "入库完成")
    log.success(f"■ {code} {message}")
    return {
        "ok": bool(sync_result.get("ok", True)),
        "message": message,
        "account_code": code,
        "sync": sync_result,
    }


def iter_discovery_import_stream(
    account_codes: list[str],
    cancel_event: Any | None = None,
):
    import json
    import queue
    import random
    import threading
    import time

    cancel = cancel_event if isinstance(cancel_event, threading.Event) else threading.Event()
    event_queue: queue.Queue = queue.Queue()
    codes = []
    seen: set[str] = set()
    for raw in account_codes:
        code = str(raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)

    def emit(item: dict[str, Any]) -> None:
        event_queue.put(item)

    def worker() -> None:
        from xueqiu.sync.sync_log import LogSink

        sink = LogSink(emit=emit)
        ok_count = 0
        fail_count = 0
        if not codes:
            sink.warn("未选择任何组合")
            emit({"type": "done", "ok": False, "message": "未选择任何组合"})
            event_queue.put(None)
            return

        sink.info(f"▶ 批量入库：共 {len(codes)} 个组合（逐个同步，遇限流会自动重试）")
        for idx, code in enumerate(codes, start=1):
            if cancel.is_set():
                sink.warn("■ 入库已停止")
                emit(
                    {
                        "type": "done",
                        "ok": False,
                        "message": f"已停止：成功 {ok_count}，失败 {fail_count}",
                    }
                )
                event_queue.put(None)
                return
            sink.info(f"── [{idx}/{len(codes)}] {code} ──")
            try:
                import_discovery_cube(code, sink=sink)
                ok_count += 1
            except Exception as exc:
                fail_count += 1
                sink.error(f"✗ {code} 入库失败：{exc}")
            if idx < len(codes) and not cancel.is_set():
                pause = random.uniform(2.5, 4.0)
                sink.info(f"暂停 {pause:.0f}s 后继续下一个…")
                time.sleep(pause)

        summary = f"批量入库完成：成功 {ok_count}，失败 {fail_count}"
        if fail_count == 0:
            sink.success(f"■ {summary}")
            emit({"type": "done", "ok": True, "message": summary})
        else:
            sink.warn(f"■ {summary}")
            emit({"type": "done", "ok": ok_count > 0, "message": summary})
        event_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        if cancel.is_set():
            try:
                while True:
                    item = event_queue.get_nowait()
                    if item is None:
                        return
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except queue.Empty:
                return
        try:
            item = event_queue.get(timeout=0.3)
        except queue.Empty:
            continue
        if item is None:
            break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


def run_discovery_mine_api(
    *,
    max_depth: int = 1,
    modes: list[str] | None = None,
    sink: Any | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    from xueqiu.domain.discovery_mine import run_discovery_mine
    from xueqiu.sync.sync_log import LogSink

    log_sink = sink if isinstance(sink, LogSink) else LogSink()
    return run_discovery_mine(
        max_depth=max_depth,
        modes=modes,
        sink=log_sink,
        cancel_event=cancel_event,
    )


def get_discovery_symbol_pool_api() -> dict[str, Any]:
    from xueqiu.domain.discovery_symbol_pool import get_symbol_pool_meta, list_symbol_pool

    return {"meta": get_symbol_pool_meta(), "items": list_symbol_pool()}


def replace_discovery_symbol_pool_api(items: list[dict[str, Any]]) -> dict[str, Any]:
    from xueqiu.domain.discovery_symbol_pool import get_symbol_pool_meta, list_symbol_pool, replace_symbol_pool

    replace_symbol_pool(items)
    return {"meta": get_symbol_pool_meta(), "items": list_symbol_pool()}


def iter_discovery_mine_stream(
    *,
    max_depth: int = 1,
    modes: list[str] | None = None,
    cancel_event: Any | None = None,
):
    import json
    import queue
    import threading

    cancel = cancel_event if isinstance(cancel_event, threading.Event) else threading.Event()
    event_queue: queue.Queue = queue.Queue()

    def emit(item: dict[str, str]) -> None:
        event_queue.put(item)

    def worker() -> None:
        from xueqiu.sync.sync_cancel import SyncCancelled
        from xueqiu.sync.sync_log import LogSink

        sink = LogSink(emit=emit)
        try:
            result = run_discovery_mine_api(
                max_depth=max_depth,
                modes=modes,
                sink=sink,
                cancel_event=cancel,
            )
            if cancel.is_set():
                event_queue.put({"type": "done", "ok": False, "message": "用户已停止挖掘"})
            else:
                event_queue.put(
                    {
                        "type": "done",
                        "ok": bool(result.get("ok")),
                        "message": str(result.get("message") or ""),
                    }
                )
        except SyncCancelled:
            sink.warn("■ 挖组合已停止")
            event_queue.put({"type": "done", "ok": False, "message": "用户已停止挖掘"})
        except Exception as exc:
            sink.error(f"挖组合中断: {exc}")
            event_queue.put({"type": "done", "ok": False, "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        if cancel.is_set():
            try:
                while True:
                    item = event_queue.get_nowait()
                    if item is None:
                        return
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except queue.Empty:
                return

        try:
            item = event_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if item is None:
            break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


def iter_sync_all_stream(cancel_event: Any | None = None):
    import json
    import queue
    import threading

    cancel = cancel_event if isinstance(cancel_event, threading.Event) else threading.Event()
    event_queue: queue.Queue = queue.Queue()

    def emit(item: dict[str, str]) -> None:
        event_queue.put(item)

    def worker() -> None:
        from xueqiu.sync.sync_cancel import SyncCancelled
        from xueqiu.sync.sync_log import LogSink

        sink = LogSink(emit=emit)
        try:
            result = run_sync_all_pipeline(sink, cancel_event=cancel)
            if cancel.is_set():
                event_queue.put({"type": "done", "ok": False, "message": "用户已停止同步"})
            else:
                event_queue.put({"type": "done", "ok": bool(result.get("ok"))})
        except SyncCancelled:
            sink.warn("■ 同步已停止")
            event_queue.put({"type": "done", "ok": False, "message": "用户已停止同步"})
        except Exception as exc:
            sink.error(f"同步中断: {exc}")
            event_queue.put({"type": "done", "ok": False, "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        if cancel.is_set():
            try:
                while True:
                    item = event_queue.get_nowait()
                    if item is None:
                        return
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except queue.Empty:
                return
        try:
            item = event_queue.get(timeout=0.3)
        except queue.Empty:
            continue
        if item is None:
            break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


def run_copy_backtest(
    *,
    initial_capital: float = 1_000_000.0,
    max_stock_pct: float = 20.0,
    min_new_position_pct: float = 1.0,
    max_positions: int = 5,
    strategy_id: str = "route_f_partition_mimic",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    from xueqiu.domain.copy_strategies import (
        StrategyId,
        run_strategy,
        strategy_to_backtest_response,
    )

    sid = StrategyId(strategy_id)
    return strategy_to_backtest_response(
        run_strategy(sid, initial_capital=initial_capital, start_date=start_date, end_date=end_date)
    )


def list_backtest_strategies() -> list[dict[str, Any]]:
    from xueqiu.domain.copy_strategies import list_strategy_catalog

    return list_strategy_catalog()


def compare_backtest_strategies(
    *,
    strategy_ids: list[str],
    initial_capital: float = 1_000_000.0,
    start_date: str | None = None,
    end_date: str | None = None,
    entry_sweep_dates: list[str] | None = None,
) -> dict[str, Any]:
    from xueqiu.domain.copy_strategies import run_strategy_compare

    return run_strategy_compare(
        strategy_ids,
        initial_capital=initial_capital,
        start_date=start_date,
        end_date=end_date,
        entry_sweep_dates=entry_sweep_dates,
    )


def get_personal_account() -> dict[str, Any]:
    from xueqiu.domain.personal_account import build_personal_account_view

    return build_personal_account_view()


def update_personal_cash(cash: float) -> dict[str, Any]:
    from xueqiu.domain.personal_account import update_personal_cash as _update

    return _update(cash)


def update_personal_strategy(strategy_id: str) -> dict[str, Any]:
    from xueqiu.domain.personal_account import update_personal_strategy as _update

    return _update(strategy_id)


def execute_personal_trade(**kwargs: Any) -> dict[str, Any]:
    from xueqiu.domain.personal_account import execute_personal_trade as _trade

    return _trade(**kwargs)


def get_copy_rebalance_plan(strategy_id: str | None = None) -> dict[str, Any]:
    from xueqiu.domain.personal_account import compute_copy_rebalance_plan

    return compute_copy_rebalance_plan(strategy_id=strategy_id)
