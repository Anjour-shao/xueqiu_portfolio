"""社交挖组合：从已入库组合主理人自选向外 BFS 发现候选。"""

from __future__ import annotations

import json
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from xueqiu.domain.codes import is_hk_us_or_non_a_share
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import (
    fetch_cube_nav_daily,
    fetch_rebalance_events,
    portfolio_has_non_a_share,
)
from xueqiu.integrations.xueqiu.watchlist import (
    CubeShowInfo,
    fetch_cube_show,
    fetch_user_cube_watchlist_meta_count,
    fetch_user_watchlist_cubes,
)
from xueqiu.integrations.xueqiu.social import fetch_stock_hot_users, fetch_user_following_page
from xueqiu.domain.discovery_symbol_pool import list_enabled_symbols, seed_symbol_pool_if_empty
from xueqiu.storage.db import (
    accounts_table,
    discovery_crawled_users_table,
    get_conn,
    init_db,
    mined_cubes_table,
)
from xueqiu.sync.sync_cancel import check_cancel
from xueqiu.sync.sync_log import LogSink

# 每个候选组合约 3 次雪球请求(show/nav/持仓)，需放慢避免 400 限流
_PAUSE_SEED = (0.9, 1.4)
_PAUSE_USER = (2.0, 3.5)
_PAUSE_CUBE = (1.2, 2.0)
_PAUSE_METRIC = (0.6, 1.1)
_BURST_FAIL_EXTRA = (6.0, 10.0)
_BURST_FAIL_THRESHOLD = 3
# 近 6 个月须有手动调仓（分红送配批次不计）；月均调仓 >3 次则过滤
_REBALANCE_LOOKBACK_DAYS = 183
_REBALANCE_LOOKBACK_MONTHS = 6
_MAX_REBALANCES_PER_MONTH = 3.0
_MIN_CUBE_AGE_YEARS = 8
_MIN_CUM_RETURN_10X_PCT = 900.0
_MAX_DISCOVERY_DEPTH = 5
_MAX_FOLLOWING_PAGES = 5
_CN_CUBE_MARKETS = frozenset({"cn", "zh", ""})
CRAWL_WATCHLIST = "watchlist"
CRAWL_FOLLOWING = "following"
CRAWL_STOCK_HOT = "stock_hot"
VALID_MINE_MODES = frozenset({CRAWL_WATCHLIST, CRAWL_FOLLOWING, CRAWL_STOCK_HOT})


@dataclass
class _QueueItem:
    uid: int
    depth: int
    pipeline: str
    source_account_code: str | None = None


@dataclass
class _MineContext:
    api: XueQiuApiClient
    log: LogSink
    cancel_event: Any | None
    max_depth: int
    in_db: set[str]
    mined_index: dict[str, dict[str, Any]]
    stats: MineStats
    crawled: dict[str, set[int]]
    processed: set[tuple[int, str]]
    queue: deque[_QueueItem]
    consecutive_show_fail: int = 0


@dataclass
class MineStats:
    seed_count: int = 0
    users_crawled: int = 0
    users_skipped: int = 0
    cubes_seen: int = 0
    cubes_new: int = 0
    cubes_updated: int = 0
    auto_pass_count: int = 0
    skipped_in_db: int = 0
    skipped_cached: int = 0
    show_fail: int = 0
    errors: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_account_codes() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute(select(accounts_table.c.account_code)).fetchall()
    return {str(r.account_code).strip().upper() for r in rows if r.account_code}


def _load_crawled_uids(crawl_kind: str) -> set[int]:
    with get_conn() as conn:
        rows = conn.execute(
            select(discovery_crawled_users_table.c.user_uid).where(
                discovery_crawled_users_table.c.crawl_kind == crawl_kind
            )
        ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


def _mark_user_crawled(uid: int, crawl_kind: str) -> None:
    now = _now()
    with get_conn() as conn:
        existing = conn.execute(
            select(discovery_crawled_users_table.c.user_uid).where(
                discovery_crawled_users_table.c.user_uid == uid,
                discovery_crawled_users_table.c.crawl_kind == crawl_kind,
            )
        ).fetchone()
        if existing is None:
            conn.execute(
                discovery_crawled_users_table.insert().values(
                    user_uid=uid,
                    crawl_kind=crawl_kind,
                    crawled_at=now,
                )
            )


def _decode_reasons(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _load_mined_index() -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            select(
                mined_cubes_table.c.account_code,
                mined_cubes_table.c.owner_uid,
                mined_cubes_table.c.reject_reasons,
                mined_cubes_table.c.cube_market,
            )
        ).fetchall()
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.account_code).strip().upper()
        index[code] = {
            "owner_uid": row.owner_uid,
            "reject_reasons": _decode_reasons(row.reject_reasons),
            "cube_market": row.cube_market,
        }
    return index


def _should_skip_show_fetch(code: str, index: dict[str, dict[str, Any]]) -> bool:
    """已成功拉过 show 的候选，重复挖掘时跳过 show（仍会重新跑初筛指标）。"""
    row = index.get(code)
    if not row:
        return False
    if "show_error" in row["reject_reasons"]:
        return False
    return row.get("owner_uid") is not None


def _market_is_non_cn(market: str | None) -> bool:
    if market is None:
        return False
    return market.strip().lower() not in _CN_CUBE_MARKETS


def _encode_reasons(reasons: list[str]) -> str | None:
    if not reasons:
        return None
    return json.dumps(reasons, ensure_ascii=False)


def _evaluate_cube(
    *,
    code: str,
    owner_uid: int | None,
    source_user_uid: int,
    in_db: set[str],
) -> tuple[list[str], bool]:
    reasons: list[str] = []
    if code in in_db:
        reasons.append("in_db")
    if owner_uid is not None and owner_uid == source_user_uid:
        reasons.append("self_created")
    return reasons, len(reasons) == 0


def _rebalance_too_frequent(event_count: int) -> bool:
    """近 6 个月手动调仓次数 / 6 个月 > 3 即视为调仓过频。"""
    return event_count / _REBALANCE_LOOKBACK_MONTHS > _MAX_REBALANCES_PER_MONTH


def _cube_founded_before_years(created_at_ms: int, years: int) -> bool:
    created = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    cutoff = _now() - timedelta(days=years * 365)
    return created <= cutoff


def _apply_old_low_return_filter(
    *,
    code: str,
    api: XueQiuApiClient,
    created_at_ms: int | None,
    cum_return_pct: float | None,
    reasons: list[str],
) -> None:
    """成立超过 8 年且累计收益未达 10 倍则排除。"""
    if cum_return_pct is None or cum_return_pct >= _MIN_CUM_RETURN_10X_PCT:
        return
    ms = created_at_ms
    if ms is None:
        try:
            show = fetch_cube_show(code, client=api, max_retries=2)
            ms = show.created_at_ms
        except Exception:
            return
    if ms is None:
        return
    if _cube_founded_before_years(ms, _MIN_CUBE_AGE_YEARS) and "old_low_return" not in reasons:
        reasons.append("old_low_return")


def _enrich_cube_metrics(
    code: str,
    api: XueQiuApiClient,
    *,
    cube_market: str | None,
) -> tuple[float | None, str | None, str | None, int | None, bool, list[str]]:
    extra_reasons: list[str] = []
    cum_return_pct: float | None = None
    nav_latest_date: str | None = None
    latest_rebalance_time: str | None = None
    rebalance_count_6m: int | None = None
    has_non_a = False

    if _market_is_non_cn(cube_market):
        extra_reasons.append("non_cn")

    time.sleep(random.uniform(*_PAUSE_METRIC))
    try:
        events, history_non_a = fetch_rebalance_events(
            code,
            client=api,
            lookback_days=_REBALANCE_LOOKBACK_DAYS,
            max_pages=5,
        )
        if history_non_a:
            has_non_a = True
            if "non_a" not in extra_reasons:
                extra_reasons.append("non_a")
        if events:
            rebalance_count_6m = len(events)
            latest_rebalance_time = str(events[0]["trade_time"])[:19]
            if _rebalance_too_frequent(rebalance_count_6m):
                extra_reasons.append("high_freq")
        else:
            rebalance_count_6m = 0
            extra_reasons.append("inactive_6m")
    except XueQiuApiError:
        extra_reasons.append("rebalance_error")
    except Exception:
        extra_reasons.append("rebalance_error")

    time.sleep(random.uniform(*_PAUSE_METRIC))
    try:
        _, points = fetch_cube_nav_daily(code, client=api)
        if points:
            last = points[-1]
            cum_return_pct = float(last.cum_return_pct)
            nav_latest_date = str(last.trade_date)
            if cum_return_pct < 0:
                extra_reasons.append("loss")
    except (XueQiuApiError, ValueError, RuntimeError):
        extra_reasons.append("nav_error")
    except Exception:
        extra_reasons.append("nav_error")

    time.sleep(random.uniform(*_PAUSE_METRIC))
    try:
        if portfolio_has_non_a_share(code, client=api):
            has_non_a = True
            if "non_a" not in extra_reasons:
                extra_reasons.append("non_a")
    except XueQiuApiError:
        extra_reasons.append("holdings_error")
    except Exception:
        extra_reasons.append("holdings_error")

    return cum_return_pct, nav_latest_date, latest_rebalance_time, rebalance_count_6m, has_non_a, extra_reasons


def _upsert_mined_cube(
    *,
    code: str,
    name: str,
    owner_uid: int | None,
    owner_name: str | None,
    source_user_uid: int,
    source_account_code: str | None,
    source_type: str,
    source_symbol: str | None,
    depth: int,
    cum_return_pct: float | None,
    nav_latest_date: str | None,
    latest_rebalance_time: str | None,
    rebalance_count_6m: int | None,
    cube_market: str | None,
    has_non_a_share: bool,
    auto_pass: bool,
    reject_reasons: list[str],
) -> tuple[bool, bool]:
    """返回 (is_new, was_updated)。"""
    now = _now()
    reasons_json = _encode_reasons(reject_reasons)
    with get_conn() as conn:
        existing = conn.execute(
            select(mined_cubes_table).where(mined_cubes_table.c.account_code == code)
        ).fetchone()
        if existing is None:
            conn.execute(
                mined_cubes_table.insert().values(
                    account_code=code,
                    account_name=name,
                    owner_uid=owner_uid,
                    owner_name=owner_name,
                    source_user_uid=source_user_uid,
                    source_account_code=source_account_code,
                    source_type=source_type,
                    source_symbol=source_symbol,
                    depth=depth,
                    cum_return_pct=cum_return_pct,
                    nav_latest_date=nav_latest_date,
                    latest_rebalance_time=latest_rebalance_time,
                    rebalance_count_6m=rebalance_count_6m,
                    cube_market=cube_market,
                    has_non_a_share=has_non_a_share,
                    auto_pass=auto_pass,
                    reject_reasons=reasons_json,
                    first_seen_at=now,
                    updated_at=now,
                )
            )
            return True, True

        conn.execute(
            mined_cubes_table.update()
            .where(mined_cubes_table.c.account_code == code)
            .values(
                account_name=name,
                owner_uid=owner_uid,
                owner_name=owner_name,
                source_user_uid=source_user_uid,
                source_account_code=source_account_code,
                source_type=source_type,
                source_symbol=source_symbol,
                depth=min(int(existing.depth or depth), depth),
                cum_return_pct=cum_return_pct,
                nav_latest_date=nav_latest_date,
                latest_rebalance_time=latest_rebalance_time,
                rebalance_count_6m=rebalance_count_6m,
                cube_market=cube_market,
                has_non_a_share=has_non_a_share,
                auto_pass=auto_pass,
                reject_reasons=reasons_json,
                updated_at=now,
            )
        )
        return False, True


def _normalize_modes(modes: list[str] | None) -> list[str]:
    if not modes:
        return [CRAWL_WATCHLIST]
    out: list[str] = []
    for raw in modes:
        m = str(raw).strip().lower()
        if m in VALID_MINE_MODES and m not in out:
            out.append(m)
    return out or [CRAWL_WATCHLIST]


def _pipeline_label(pipeline: str) -> str:
    if pipeline == CRAWL_FOLLOWING:
        return "关注链"
    if pipeline == CRAWL_STOCK_HOT:
        return "个股"
    return "自选链"


def _process_watchlist_for_user(
    ctx: _MineContext,
    *,
    uid: int,
    depth: int,
    pipeline: str,
    source_account_code: str | None,
    source_symbol: str | None = None,
) -> None:
    owner_label = str(uid)
    label = _pipeline_label(pipeline)
    log = ctx.log
    log.info(f"── [{label}] 深度 {depth}：用户 {owner_label} 自选组合…")

    try:
        watchlist = fetch_user_watchlist_cubes(uid, client=ctx.api)
        meta_count = fetch_user_cube_watchlist_meta_count(uid, client=ctx.api)
    except Exception as exc:
        ctx.stats.errors += 1
        log.warn(f"用户 {owner_label} 自选拉取失败: {exc}")
        return

    ctx.stats.users_crawled += 1
    ctx.crawled.setdefault(pipeline, set()).add(uid)
    _mark_user_crawled(uid, pipeline)

    if watchlist:
        log.info(f"用户 {owner_label} 自选 {len(watchlist)} 个组合")
    elif meta_count:
        log.info(
            f"用户 {owner_label} 自选 0 个组合"
            f"（元数据 {meta_count} 个，可能隐私不可见）"
        )
    else:
        log.info(f"用户 {owner_label} 自选 0 个组合")

    source_type = pipeline if pipeline != CRAWL_STOCK_HOT else CRAWL_STOCK_HOT

    for code, list_name in watchlist:
        check_cancel(ctx.cancel_event)
        ctx.stats.cubes_seen += 1
        try:
            if code in ctx.in_db:
                ctx.stats.skipped_in_db += 1
                continue

            if _should_skip_show_fetch(code, ctx.mined_index):
                ctx.stats.skipped_cached += 1
                cached = ctx.mined_index.get(code, {})
                show = CubeShowInfo(
                    account_code=code,
                    account_name=list_name,
                    owner_uid=cached.get("owner_uid"),
                    owner_name=None,
                    market=cached.get("cube_market"),
                )
            else:
                try:
                    show = fetch_cube_show(code, client=ctx.api)
                    ctx.consecutive_show_fail = 0
                except Exception as exc:
                    ctx.stats.errors += 1
                    ctx.stats.show_fail += 1
                    ctx.consecutive_show_fail += 1
                    if ctx.consecutive_show_fail >= _BURST_FAIL_THRESHOLD:
                        extra = random.uniform(*_BURST_FAIL_EXTRA)
                        log.warn(f"  连续失败，暂停 {extra:.0f}s…")
                        time.sleep(extra)
                        ctx.consecutive_show_fail = 0
                    log.warn(f"  {code} show 失败（将记入待补全）: {exc}")
                    is_new, _ = _upsert_mined_cube(
                        code=code,
                        name=list_name,
                        owner_uid=None,
                        owner_name=None,
                        source_user_uid=uid,
                        source_account_code=source_account_code,
                        source_type=source_type,
                        source_symbol=source_symbol,
                        depth=depth,
                        cum_return_pct=None,
                        nav_latest_date=None,
                        latest_rebalance_time=None,
                        rebalance_count_6m=None,
                        cube_market=None,
                        has_non_a_share=False,
                        auto_pass=False,
                        reject_reasons=["show_error"],
                    )
                    if is_new:
                        ctx.stats.cubes_new += 1
                    ctx.mined_index[code] = {"owner_uid": None, "reject_reasons": ["show_error"]}
                    time.sleep(random.uniform(*_PAUSE_CUBE))
                    continue

            name = show.account_name or list_name
            reasons, base_ok = _evaluate_cube(
                code=code,
                owner_uid=show.owner_uid,
                source_user_uid=uid,
                in_db=ctx.in_db,
            )
            if "in_db" in reasons:
                ctx.stats.skipped_in_db += 1
                continue

            cube_market = show.market
            cum_return_pct, nav_latest_date, latest_rebalance_time, rebalance_count_6m, has_non_a, metric_reasons = (
                _enrich_cube_metrics(code, ctx.api, cube_market=cube_market)
            )
            reasons.extend(metric_reasons)
            _apply_old_low_return_filter(
                code=code,
                api=ctx.api,
                created_at_ms=show.created_at_ms,
                cum_return_pct=cum_return_pct,
                reasons=reasons,
            )
            auto_pass = base_ok and not metric_reasons and "old_low_return" not in reasons

            is_new, _ = _upsert_mined_cube(
                code=code,
                name=name,
                owner_uid=show.owner_uid,
                owner_name=show.owner_name,
                source_user_uid=uid,
                source_account_code=source_account_code,
                source_type=source_type,
                source_symbol=source_symbol,
                depth=depth,
                cum_return_pct=cum_return_pct,
                nav_latest_date=nav_latest_date,
                latest_rebalance_time=latest_rebalance_time,
                rebalance_count_6m=rebalance_count_6m,
                cube_market=cube_market,
                has_non_a_share=has_non_a,
                auto_pass=auto_pass,
                reject_reasons=reasons,
            )
            if is_new:
                ctx.stats.cubes_new += 1
            else:
                ctx.stats.cubes_updated += 1
            if auto_pass:
                ctx.stats.auto_pass_count += 1
                if (
                    pipeline == CRAWL_WATCHLIST
                    and show.owner_uid is not None
                    and depth < ctx.max_depth
                ):
                    key = (show.owner_uid, CRAWL_WATCHLIST)
                    crawled = ctx.crawled.get(CRAWL_WATCHLIST, set())
                    if key not in ctx.processed and show.owner_uid not in crawled:
                        ctx.queue.append(
                            _QueueItem(
                                uid=show.owner_uid,
                                depth=depth + 1,
                                pipeline=CRAWL_WATCHLIST,
                                source_account_code=code,
                            )
                        )

            ctx.mined_index[code] = {
                "owner_uid": show.owner_uid,
                "reject_reasons": reasons,
                "cube_market": cube_market,
            }
            time.sleep(random.uniform(*_PAUSE_CUBE))
        except Exception as exc:
            ctx.stats.errors += 1
            log.warn(f"  {code} 处理失败（已跳过）: {exc}")
            time.sleep(random.uniform(*_PAUSE_CUBE))

    time.sleep(random.uniform(*_PAUSE_USER))


def _enqueue_following_children(ctx: _MineContext, item: _QueueItem) -> None:
    if item.pipeline != CRAWL_FOLLOWING or item.depth >= ctx.max_depth:
        return
    crawled = ctx.crawled.get(CRAWL_FOLLOWING, set())
    page = 1
    max_page = 1
    added = 0
    while page <= max_page and page <= _MAX_FOLLOWING_PAGES:
        try:
            users, mp = fetch_user_following_page(item.uid, page=page, client=ctx.api)
        except Exception as exc:
            ctx.stats.errors += 1
            ctx.log.warn(f"用户 {item.uid} 关注列表第 {page} 页失败: {exc}")
            break
        if mp:
            max_page = min(int(mp), _MAX_FOLLOWING_PAGES)
        for user in users:
            key = (user.uid, CRAWL_FOLLOWING)
            if key in ctx.processed or user.uid in crawled:
                continue
            ctx.queue.append(
                _QueueItem(
                    uid=user.uid,
                    depth=item.depth + 1,
                    pipeline=CRAWL_FOLLOWING,
                    source_account_code=item.source_account_code,
                )
            )
            added += 1
        if not users:
            break
        page += 1
        time.sleep(random.uniform(0.4, 0.9))
    if added:
        ctx.log.info(f"  关注链扩层 +{added} 人（深度 {item.depth + 1}）")


def _run_bfs_pipeline(ctx: _MineContext, pipeline: str) -> None:
    while ctx.queue:
        check_cancel(ctx.cancel_event)
        item = ctx.queue.popleft()
        if item.pipeline != pipeline or item.depth > ctx.max_depth:
            continue
        key = (item.uid, pipeline)
        if key in ctx.processed:
            ctx.stats.users_skipped += 1
            continue
        if item.uid in ctx.crawled.get(pipeline, set()):
            ctx.stats.users_skipped += 1
            continue

        ctx.processed.add(key)
        _process_watchlist_for_user(
            ctx,
            uid=item.uid,
            depth=item.depth,
            pipeline=pipeline,
            source_account_code=item.source_account_code,
        )
        if pipeline == CRAWL_FOLLOWING:
            _enqueue_following_children(ctx, item)


def _run_stock_hot(ctx: _MineContext, symbols: list[str]) -> None:
    crawled = ctx.crawled.setdefault(CRAWL_STOCK_HOT, set())
    from xueqiu.domain.discovery_hot_symbols import STOCK_HOT_USER_PAGE_SIZE

    for sym in symbols:
        check_cancel(ctx.cancel_event)
        ctx.log.info(f"── [个股] {sym} 活跃用户…")
        try:
            users = fetch_stock_hot_users(
                sym, start=0, count=STOCK_HOT_USER_PAGE_SIZE, client=ctx.api
            )
        except Exception as exc:
            ctx.stats.errors += 1
            ctx.log.warn(f"  {sym} hot user 失败: {exc}")
            continue
        ctx.log.info(f"  {sym} 命中 {len(users)} 个用户")
        for user in users:
            if user.uid in crawled:
                ctx.stats.users_skipped += 1
                continue
            crawled.add(user.uid)
            _process_watchlist_for_user(
                ctx,
                uid=user.uid,
                depth=1,
                pipeline=CRAWL_STOCK_HOT,
                source_account_code=None,
                source_symbol=sym,
            )
        time.sleep(random.uniform(*_PAUSE_USER))


def run_discovery_mine(
    *,
    max_depth: int = 1,
    modes: list[str] | None = None,
    sink: LogSink | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    init_db()
    seed_symbol_pool_if_empty()
    log = sink or LogSink()
    mode_list = _normalize_modes(modes)
    max_depth = max(1, min(int(max_depth), _MAX_DISCOVERY_DEPTH))
    from xueqiu.domain.discovery_hot_symbols import STOCK_HOT_USER_SAMPLE_SIZE

    hot_limit = STOCK_HOT_USER_SAMPLE_SIZE
    api = XueQiuApiClient()
    stats = MineStats()
    ctx = _MineContext(
        api=api,
        log=log,
        cancel_event=cancel_event,
        max_depth=max_depth,
        in_db=_load_account_codes(),
        mined_index=_load_mined_index(),
        stats=stats,
        crawled={m: _load_crawled_uids(m) for m in VALID_MINE_MODES},
        processed=set(),
        queue=deque(),
    )

    with get_conn() as conn:
        seeds = conn.execute(
            select(accounts_table.c.account_code, accounts_table.c.account_name).order_by(
                accounts_table.c.account_code.asc()
            )
        ).fetchall()

    if not seeds and CRAWL_STOCK_HOT not in mode_list:
        log.warn("accounts 表为空，无法挖掘")
        return {"ok": False, "message": "无种子组合", "stats": stats.__dict__}

    mode_labels = "、".join(_pipeline_label(m) for m in mode_list)
    log.info(f"▶ 开始挖组合：模式 [{mode_labels}]，最大深度 {max_depth}")
    log.info("初筛：非自建、累计收益≥0、A股组合、近6月有手动调仓且月均≤3次、持仓/历史无港美股")

    seed_owners: list[tuple[int, str]] = []
    for row in seeds:
        code = str(row.account_code).strip().upper()
        try:
            show = fetch_cube_show(code, client=api)
        except Exception as exc:
            stats.errors += 1
            log.warn(f"种子 {code} 主理人解析失败: {exc}")
            continue
        if show.owner_uid is None:
            stats.errors += 1
            log.warn(f"种子 {code} 无 owner_uid，跳过")
            continue
        stats.seed_count += 1
        seed_owners.append((show.owner_uid, code))
        time.sleep(random.uniform(*_PAUSE_SEED))

    if CRAWL_WATCHLIST in mode_list:
        for uid, code in seed_owners:
            ctx.queue.append(
                _QueueItem(uid=uid, depth=1, pipeline=CRAWL_WATCHLIST, source_account_code=code)
            )
        _run_bfs_pipeline(ctx, CRAWL_WATCHLIST)

    if CRAWL_FOLLOWING in mode_list:
        ctx.queue.clear()
        for uid, code in seed_owners:
            ctx.queue.append(
                _QueueItem(uid=uid, depth=1, pipeline=CRAWL_FOLLOWING, source_account_code=code)
            )
        _run_bfs_pipeline(ctx, CRAWL_FOLLOWING)

    if CRAWL_STOCK_HOT in mode_list:
        symbols = list_enabled_symbols()[:hot_limit]
        log.info(f"── [个股] 股票池启用 {len(list_enabled_symbols())} 只，本次跑 {len(symbols)} 只")
        _run_stock_hot(ctx, symbols)

    msg = (
        f"挖掘完成：爬用户 {stats.users_crawled}，见组合 {stats.cubes_seen}，"
        f"新增 {stats.cubes_new}，初筛通过 {stats.auto_pass_count}"
    )
    if stats.show_fail:
        msg += f"，show 限流待补全 {stats.show_fail}（可再次挖掘刷新）"
    if stats.skipped_cached:
        msg += f"，跳过已缓存 {stats.skipped_cached}"
    log.success(f"■ {msg}")
    if stats.show_fail:
        log.info("提示：HTTP 400 多为请求过快，已自动重试并放慢；失败项在「全部」里带 show_error 标签")
    return {
        "ok": True,
        "message": msg,
        "stats": {
            "seed_count": stats.seed_count,
            "users_crawled": stats.users_crawled,
            "users_skipped": stats.users_skipped,
            "cubes_seen": stats.cubes_seen,
            "cubes_new": stats.cubes_new,
            "cubes_updated": stats.cubes_updated,
            "auto_pass_count": stats.auto_pass_count,
            "skipped_in_db": stats.skipped_in_db,
            "skipped_cached": stats.skipped_cached,
            "show_fail": stats.show_fail,
            "errors": stats.errors,
        },
    }
