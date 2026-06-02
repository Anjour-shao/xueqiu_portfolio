"""社交挖组合：从已入库组合主理人自选向外 BFS 发现候选。"""

from __future__ import annotations

import json
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import distinct, select

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
from xueqiu.storage.db import accounts_table, get_conn, init_db, mined_cubes_table
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
_MAX_DISCOVERY_DEPTH = 5
_CN_CUBE_MARKETS = frozenset({"cn", "zh", ""})


@dataclass
class _QueueItem:
    uid: int
    depth: int
    source_account_code: str | None = None


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


def _load_crawled_source_uids() -> set[int]:
    with get_conn() as conn:
        rows = conn.execute(
            select(distinct(mined_cubes_table.c.source_user_uid)).where(
                mined_cubes_table.c.source_user_uid.is_not(None)
            )
        ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


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


def run_discovery_mine(
    *,
    max_depth: int = 1,
    sink: LogSink | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    init_db()
    log = sink or LogSink()
    max_depth = max(1, min(int(max_depth), _MAX_DISCOVERY_DEPTH))
    api = XueQiuApiClient()
    in_db = _load_account_codes()
    db_crawled_uids = _load_crawled_source_uids()
    mined_index = _load_mined_index()
    processed_run: set[int] = set()
    stats = MineStats()
    consecutive_show_fail = 0

    queue: deque[_QueueItem] = deque()
    with get_conn() as conn:
        seeds = conn.execute(
            select(accounts_table.c.account_code, accounts_table.c.account_name).order_by(
                accounts_table.c.account_code.asc()
            )
        ).fetchall()

    if not seeds:
        log.warn("accounts 表为空，无法挖掘")
        return {"ok": False, "message": "无种子组合", "stats": stats.__dict__}

    log.info(
        f"▶ 开始社交挖组合：种子 {len(seeds)} 个，最大深度 {max_depth} "
        f"（深度1=种子主理人自选；深度2=初筛通过候选的主理人再扩一层自选，非二次规则筛选）"
    )
    log.info(
        "初筛：非自建、累计收益≥0、A股组合、近6月有手动调仓且月均≤3次、持仓/历史无港美股"
    )
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
        queue.append(_QueueItem(uid=show.owner_uid, depth=1, source_account_code=code))
        time.sleep(random.uniform(*_PAUSE_SEED))

    while queue:
        check_cancel(cancel_event)
        item = queue.popleft()
        if item.depth > max_depth:
            continue
        if item.uid in db_crawled_uids or item.uid in processed_run:
            stats.users_skipped += 1
            continue

        processed_run.add(item.uid)
        owner_label = str(item.uid)
        log.info(f"── 深度 {item.depth}：拉取用户 {owner_label} 自选组合…")

        try:
            watchlist = fetch_user_watchlist_cubes(item.uid, client=api)
            meta_count = fetch_user_cube_watchlist_meta_count(item.uid, client=api)
        except Exception as exc:
            stats.errors += 1
            log.warn(f"用户 {owner_label} 自选组合拉取失败: {exc}")
            continue

        stats.users_crawled += 1
        db_crawled_uids.add(item.uid)
        if watchlist:
            log.info(f"用户 {owner_label} 自选 {len(watchlist)} 个组合")
        elif meta_count:
            log.info(
                f"用户 {owner_label} 自选 0 个组合"
                f"（元数据显示 {meta_count} 个，可能因隐私设置不可见；已排除管理组合）"
            )
        else:
            log.info(f"用户 {owner_label} 自选 0 个组合")

        consecutive_show_fail = 0
        for code, list_name in watchlist:
            check_cancel(cancel_event)
            stats.cubes_seen += 1
            try:
                if code in in_db:
                    stats.skipped_in_db += 1
                    continue

                if _should_skip_show_fetch(code, mined_index):
                    stats.skipped_cached += 1
                    cached = mined_index.get(code, {})
                    show = CubeShowInfo(
                        account_code=code,
                        account_name=list_name,
                        owner_uid=cached.get("owner_uid"),
                        owner_name=None,
                        market=cached.get("cube_market"),
                    )
                else:
                    try:
                        show = fetch_cube_show(code, client=api)
                        consecutive_show_fail = 0
                    except Exception as exc:
                        stats.errors += 1
                        stats.show_fail += 1
                        consecutive_show_fail += 1
                        if consecutive_show_fail >= _BURST_FAIL_THRESHOLD:
                            extra = random.uniform(*_BURST_FAIL_EXTRA)
                            log.warn(f"  连续失败，暂停 {extra:.0f}s…")
                            time.sleep(extra)
                            consecutive_show_fail = 0
                        log.warn(f"  {code} show 失败（将记入待补全）: {exc}")
                        is_new, _ = _upsert_mined_cube(
                            code=code,
                            name=list_name,
                            owner_uid=None,
                            owner_name=None,
                            source_user_uid=item.uid,
                            source_account_code=item.source_account_code,
                            depth=item.depth,
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
                            stats.cubes_new += 1
                        mined_index[code] = {"owner_uid": None, "reject_reasons": ["show_error"]}
                        time.sleep(random.uniform(*_PAUSE_CUBE))
                        continue

                name = show.account_name or list_name
                reasons, base_ok = _evaluate_cube(
                    code=code,
                    owner_uid=show.owner_uid,
                    source_user_uid=item.uid,
                    in_db=in_db,
                )
                if "in_db" in reasons:
                    stats.skipped_in_db += 1
                    continue

                cube_market = show.market
                cum_return_pct, nav_latest_date, latest_rebalance_time, rebalance_count_6m, has_non_a, metric_reasons = (
                    _enrich_cube_metrics(code, api, cube_market=cube_market)
                )
                reasons.extend(metric_reasons)
                auto_pass = base_ok and not metric_reasons

                is_new, _ = _upsert_mined_cube(
                    code=code,
                    name=name,
                    owner_uid=show.owner_uid,
                    owner_name=show.owner_name,
                    source_user_uid=item.uid,
                    source_account_code=item.source_account_code,
                    depth=item.depth,
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
                    stats.cubes_new += 1
                else:
                    stats.cubes_updated += 1
                if auto_pass:
                    stats.auto_pass_count += 1
                    if (
                        show.owner_uid is not None
                        and item.depth < max_depth
                        and show.owner_uid not in db_crawled_uids
                        and show.owner_uid not in processed_run
                    ):
                        queue.append(
                            _QueueItem(
                                uid=show.owner_uid,
                                depth=item.depth + 1,
                                source_account_code=code,
                            )
                        )

                mined_index[code] = {
                    "owner_uid": show.owner_uid,
                    "reject_reasons": reasons,
                    "cube_market": cube_market,
                }
                time.sleep(random.uniform(*_PAUSE_CUBE))
            except Exception as exc:
                stats.errors += 1
                log.warn(f"  {code} 处理失败（已跳过）: {exc}")
                time.sleep(random.uniform(*_PAUSE_CUBE))
                continue

        time.sleep(random.uniform(*_PAUSE_USER))

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
