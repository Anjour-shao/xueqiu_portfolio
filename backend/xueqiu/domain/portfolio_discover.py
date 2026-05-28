"""从 cube_catalog 或（旧）ZH 号段抽样，硬门槛 + 画像匹配进候选。"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import (
    fetch_cube_nav_daily,
    fetch_portfolio_rebalance,
    fetch_rebalance_events,
    portfolio_has_non_a_share,
    validate_portfolio_id,
)
from xueqiu.storage.db import accounts_table, get_conn
from xueqiu.sync.sync_cube_catalog import (
    fetch_catalog_batch_sequential,
    mark_catalog_discovered,
)

ZH_NUM_RE = re.compile(r"^ZH(\d+)$", re.IGNORECASE)
MAX_SCAN_LIMIT = 500
NAV_LIGHT_DAYS = 60
DEFAULT_ZH_LO = 1_000_000
DEFAULT_ZH_HI = 3_565_914
MATURE_MIN_YEARS = 5.0
NAV_AT_5Y_DEFAULT = 6.0
NAV_AT_10Y_DEFAULT = 40.0
YOUNG_MIN_CUM_PCT_DEFAULT = 300.0
MAX_REBALANCE_PER_MONTH_DEFAULT = 4
PROFILE_MATURE = "mature_scaled"
PROFILE_YOUNG = "young_high_return"
DEFAULT_PROFILES = (PROFILE_MATURE, PROFILE_YOUNG)
# 雪球 nav_daily 连续请求易 400，默认放慢间隔
DEFAULT_SLEEP = (1.8, 3.0)
BACKOFF_SECONDS = (8.0, 15.0, 25.0)
CONSECUTIVE_FAIL_WARN = 8
EXTRA_SLEEP_AFTER_HTTP_FAIL = (2.0, 4.0)


def format_zh(num: int) -> str:
    return f"ZH{num}"


def parse_zh_num(code: str) -> int:
    match = ZH_NUM_RE.match(code.strip().upper())
    if not match:
        raise ValueError(f"无效组合号: {code}")
    return int(match.group(1))


def iter_zh_codes(num_min: int, num_max: int, *, step: int = 1) -> list[str]:
    if num_min > num_max:
        num_min, num_max = num_max, num_min
    step = max(1, step)
    return [format_zh(n) for n in range(num_min, num_max + 1, step)]


def iter_zh_batch(
    num_start: int,
    batch_size: int,
    *,
    step: int = 1,
    end_goal: int | None = None,
) -> tuple[list[str], int, int]:
    step = max(1, step)
    batch_size = max(1, batch_size)
    nums: list[int] = []
    n = num_start
    while len(nums) < batch_size:
        if end_goal is not None and n > end_goal:
            break
        nums.append(n)
        n += step
    if not nums:
        return [], num_start, num_start - step
    return [format_zh(x) for x in nums], nums[0], nums[-1]


def draw_random_zh_batch(
    lo: int,
    hi: int,
    batch_size: int,
    *,
    exclude: set[str] | None = None,
) -> list[str]:
    lo, hi = min(lo, hi), max(lo, hi)
    exclude = {c.strip().upper() for c in (exclude or set())}
    batch_size = max(1, min(batch_size, MAX_SCAN_LIMIT))
    codes: list[str] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = batch_size * 30
    while len(codes) < batch_size and attempts < max_attempts:
        attempts += 1
        num = random.randint(lo, hi)
        code = format_zh(num)
        if code in exclude or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def required_nav_for_tenure(
    years: float,
    *,
    nav_at_5y: float = NAV_AT_5Y_DEFAULT,
    nav_at_10y: float = NAV_AT_10Y_DEFAULT,
) -> float:
    if years < MATURE_MIN_YEARS:
        return float("inf")
    if years >= 10:
        return nav_at_10y
    return nav_at_5y + (nav_at_10y - nav_at_5y) * (years - MATURE_MIN_YEARS) / 5.0


def _parse_nav_point_date(raw: str) -> datetime | None:
    text = str(raw).strip()
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return None
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _events_in_last_days(events: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for ev in events:
        try:
            dt = datetime.strptime(str(ev.get("trade_time", ""))[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if dt >= cutoff:
            out.append(ev)
    return out


def probe_discover_metrics(code: str, client: XueQiuApiClient) -> dict[str, Any]:
    pid = validate_portfolio_id(code)
    name, points = fetch_cube_nav_daily(pid, client=client)
    if not points:
        raise XueQiuApiError("无净值数据")

    latest = points[-1]
    first = points[0]
    first_dt = _parse_nav_point_date(str(first.trade_date))
    inception_days = max(0, (datetime.now() - first_dt).days) if first_dt else 0
    inception_years = inception_days / 365.25

    events, history_has_non_a = fetch_rebalance_events(pid, client=client, lookback_days=120)
    events_3m = _events_in_last_days(events, 90)
    latest_trade = events[0]["trade_time"] if events else None
    has_non_a_share = history_has_non_a or portfolio_has_non_a_share(pid, client=client)

    return {
        "account_code": pid,
        "account_name": name,
        "latest_nav": round(float(latest.nav_value), 4),
        "cum_return_pct": round(float(latest.cum_return_pct), 2),
        "latest_nav_date": _display_date(str(latest.trade_date)),
        "latest_trade_time": latest_trade,
        "inception_days": inception_days,
        "inception_years": round(inception_years, 2),
        "rebalance_events": events,
        "rebalance_events_3m": events_3m,
        "has_non_a_share": has_non_a_share,
    }


def passes_hard_gates(
    metrics: dict[str, Any],
    *,
    max_inactive_days: int | None = 90,
    max_rebalance_per_month: int = MAX_REBALANCE_PER_MONTH_DEFAULT,
) -> tuple[bool, str]:
    if metrics.get("has_non_a_share"):
        return False, "[硬] 含港美股或非A股标的"
    days = _days_since_trade(metrics.get("latest_trade_time"))
    if days is None:
        return False, "[硬] 近90天无手动调仓（分红送配不计入）"
    if max_inactive_days is not None and days > max_inactive_days:
        return False, f"[硬] 最近调仓 {days} 天前（超过 {max_inactive_days} 天）"

    by_month: dict[str, int] = {}
    for ev in metrics.get("rebalance_events_3m") or []:
        mk = str(ev.get("month_key") or "")
        if not mk:
            continue
        by_month[mk] = by_month.get(mk, 0) + 1
    for mk, cnt in sorted(by_month.items()):
        if cnt > max_rebalance_per_month:
            return False, f"[硬] {mk} 调仓 {cnt} 次（上限 {max_rebalance_per_month}/月）"
    return True, ""


def match_archetypes(
    metrics: dict[str, Any],
    profiles: list[str],
    *,
    nav_at_5y: float = NAV_AT_5Y_DEFAULT,
    nav_at_10y: float = NAV_AT_10Y_DEFAULT,
    young_min_cum_pct: float = YOUNG_MIN_CUM_PCT_DEFAULT,
) -> tuple[list[str], float | None]:
    enabled = {p.strip() for p in profiles if p and p.strip()}
    years = float(metrics.get("inception_years") or 0)
    nav = float(metrics.get("latest_nav") or 0)
    cum = float(metrics.get("cum_return_pct") or 0)
    matched: list[str] = []
    req_nav: float | None = None

    if PROFILE_MATURE in enabled and years >= MATURE_MIN_YEARS:
        req_nav = required_nav_for_tenure(years, nav_at_5y=nav_at_5y, nav_at_10y=nav_at_10y)
        if nav >= req_nav:
            matched.append(PROFILE_MATURE)
    if PROFILE_YOUNG in enabled and years < MATURE_MIN_YEARS:
        if cum >= young_min_cum_pct:
            matched.append(PROFILE_YOUNG)
    return matched, req_nav


def resolve_scan_plan(
    *,
    zh_num_start: int | None = None,
    batch_size: int | None = None,
    zh_num_min: int | None = None,
    zh_num_max: int | None = None,
    step: int = 1,
    max_scan: int | None = None,
    zh_num_end_goal: int | None = None,
) -> tuple[list[str], int, int, int]:
    step = max(1, step)
    if zh_num_start is not None:
        size = batch_size if batch_size is not None else (max_scan or 80)
        codes, batch_start, batch_end = iter_zh_batch(
            zh_num_start, size, step=step, end_goal=zh_num_end_goal
        )
        next_checkpoint = batch_end + step if codes else zh_num_start
        return codes, batch_start, batch_end, next_checkpoint

    if zh_num_min is None or zh_num_max is None:
        raise ValueError("请提供 zh_num_start（顺序爬取）或 zh_num_min/zh_num_max（区间模式）")

    codes = iter_zh_codes(zh_num_min, zh_num_max, step=step)
    if max_scan and max_scan > 0:
        codes = codes[:max_scan]
    if not codes:
        raise ValueError("扫描列表为空")
    nums = [parse_zh_num(c) for c in codes]
    return codes, nums[0], nums[-1], nums[-1] + step


def _fetch_followed_codes() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute(select(accounts_table.c.account_code)).fetchall()
    return {str(row.account_code).strip().upper() for row in rows if row.account_code}


def _display_date(raw: str) -> str:
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _days_since_trade(trade_time: str | None) -> int | None:
    if not trade_time:
        return None
    try:
        dt = datetime.strptime(trade_time[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(trade_time[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return (datetime.now() - dt).days


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, XueQiuApiError):
        msg = str(exc)
        return any(
            token in msg
            for token in ("400", "401", "403", "429", "502", "503", "限流", "认证", "服务暂不可用")
        )
    return False


def _format_probe_skip_reason(exc: BaseException) -> str:
    text = str(exc)
    if "400" in text:
        return "HTTP 400（多为请求过快/短暂限流，网页上可能有该组合，建议放慢或稍后重试）"
    if "429" in text or "限流" in text:
        return "HTTP 429 限流，请暂停一会儿或加大请求间隔"
    if "认证" in text or "401" in text or "403" in text:
        return "Cookie 失效或无权访问，请重新登录导出 Cookie"
    if "无净值数据" in text:
        return "无净值数据（组合可能不存在或已关停）"
    return f"请求失败: {text[:200]}"


def _call_with_backoff(fn: Callable[[], Any], *, emit: Callable[[dict[str, Any]], None] | None = None) -> Any:
    last_exc: BaseException | None = None
    for attempt, wait in enumerate(BACKOFF_SECONDS):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_error(exc) or attempt >= len(BACKOFF_SECONDS) - 1:
                raise
            if emit:
                emit(
                    {
                        "type": "log",
                        "level": "warn",
                        "message": f"接口异常，{wait:.0f}s 后重试 ({attempt + 1}/{len(BACKOFF_SECONDS)}): {exc}",
                    }
                )
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError("retry exhausted")


def probe_light(code: str, client: XueQiuApiClient, *, nav_days: int = NAV_LIGHT_DAYS) -> dict[str, Any]:
    """L1：仅 nav_daily（短窗口）。"""
    pid = validate_portfolio_id(code)
    since_ms = int((datetime.now() - timedelta(days=nav_days)).timestamp() * 1000)
    name, points = fetch_cube_nav_daily(pid, client=client, since_ms=since_ms)
    if not points:
        raise XueQiuApiError("无净值数据")

    latest = points[-1]
    return {
        "account_code": pid,
        "account_name": name,
        "latest_nav": round(float(latest.nav_value), 4),
        "cum_return_pct": round(float(latest.cum_return_pct), 2),
        "latest_nav_date": _display_date(str(latest.trade_date)),
        "latest_trade_time": None,
        "trade_count_hint": len(points),
    }


def attach_rebalance(item: dict[str, Any], client: XueQiuApiClient) -> dict[str, Any]:
    """L2：补充最近调仓时间。"""
    pid = item["account_code"]
    reb = fetch_portfolio_rebalance(pid, client=client)
    item = {**item}
    item["latest_trade_time"] = str(reb.get("rebalance_time") or "")
    return item


def probe_portfolio(code: str, client: XueQiuApiClient) -> dict[str, Any]:
    """完整探测（关注时用）。"""
    item = probe_light(code, client, nav_days=400)
    try:
        return attach_rebalance(item, client)
    except Exception:
        return item


def passes_nav_filters(
    item: dict[str, Any],
    *,
    min_nav: float | None,
    min_cum_return_pct: float | None,
    max_cum_return_pct: float | None,
) -> tuple[bool, str]:
    nav = float(item.get("latest_nav") or 0)
    if min_nav is not None and nav < min_nav:
        return False, f"[L1] 净值 {nav:.4f} < {min_nav}"

    cum = item.get("cum_return_pct")
    if min_cum_return_pct is not None and cum is not None and float(cum) < min_cum_return_pct:
        return False, f"[L1] 累计收益 {cum}% < {min_cum_return_pct}%"
    if max_cum_return_pct is not None and cum is not None and float(cum) > max_cum_return_pct:
        return False, f"[L1] 累计收益 {cum}% > {max_cum_return_pct}%"

    return True, ""


def passes_rebalance_filters(
    item: dict[str, Any],
    *,
    max_inactive_days: int | None,
) -> tuple[bool, str]:
    if max_inactive_days is None:
        return True, ""

    days = _days_since_trade(item.get("latest_trade_time"))
    if days is None:
        return False, "[L2] 无调仓记录"
    if days > max_inactive_days:
        return False, f"[L2] 最近调仓 {days} 天前（超过 {max_inactive_days} 天）"
    return True, ""


def passes_filters(
    item: dict[str, Any],
    *,
    min_nav: float | None,
    min_cum_return_pct: float | None,
    max_cum_return_pct: float | None,
    max_inactive_days: int | None,
) -> tuple[bool, str]:
    ok, reason = passes_nav_filters(
        item,
        min_nav=min_nav,
        min_cum_return_pct=min_cum_return_pct,
        max_cum_return_pct=max_cum_return_pct,
    )
    if not ok:
        return ok, reason
    return passes_rebalance_filters(item, max_inactive_days=max_inactive_days)


def _sleep_between(sleep_seconds: tuple[float, float]) -> None:
    if sleep_seconds[1] > 0:
        time.sleep(random.uniform(*sleep_seconds))


def _mark_catalog_dug(code: str, scan_mode: str) -> None:
    if scan_mode == "catalog":
        mark_catalog_discovered([code])


def run_portfolio_discover(
    *,
    scan_mode: str = "catalog",
    zh_num_lo: int = DEFAULT_ZH_LO,
    zh_num_hi: int = DEFAULT_ZH_HI,
    num_min: int | None = None,
    num_max: int | None = None,
    num_start: int | None = None,
    batch_size: int | None = None,
    end_goal: int | None = None,
    step: int = 1,
    max_scan: int = 100,
    profiles: list[str] | None = None,
    nav_at_5y: float = NAV_AT_5Y_DEFAULT,
    nav_at_10y: float = NAV_AT_10Y_DEFAULT,
    young_min_cum_pct: float = YOUNG_MIN_CUM_PCT_DEFAULT,
    max_rebalance_per_month: int = MAX_REBALANCE_PER_MONTH_DEFAULT,
    min_nav: float | None = None,
    min_cum_return_pct: float | None = None,
    max_cum_return_pct: float | None = None,
    max_inactive_days: int | None = 90,
    exclude_followed: bool = True,
    sleep_seconds: tuple[float, float] = DEFAULT_SLEEP,
    emit: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    enabled_profiles = list(profiles) if profiles else list(DEFAULT_PROFILES)
    size = batch_size if batch_size is not None else 30
    catalog_pool: int | None = None
    catalog_discovered: int | None = None
    catalog_remaining: int | None = None

    if scan_mode == "sequential" and num_start is not None:
        codes, batch_start, batch_end, planned_next = resolve_scan_plan(
            zh_num_start=num_start,
            batch_size=size,
            zh_num_min=num_min,
            zh_num_max=num_max,
            step=step,
            max_scan=max_scan if num_start is None else None,
            zh_num_end_goal=end_goal,
        )
    elif scan_mode == "random":
        lo = zh_num_lo if num_min is None else num_min
        hi = zh_num_hi if num_max is None else num_max
        followed_pre = _fetch_followed_codes() if exclude_followed else set()
        codes = draw_random_zh_batch(lo, hi, size, exclude=followed_pre)
        batch_start = lo
        batch_end = hi
        planned_next = None
    else:
        codes, catalog_pool, catalog_discovered, catalog_remaining = fetch_catalog_batch_sequential(size)
        batch_start = catalog_discovered
        batch_end = catalog_pool
        planned_next = None
        if catalog_pool == 0:
            raise ValueError("cube_catalog 为空，请先在「同步数据」页执行「同步榜单组合」")
        if not codes and catalog_remaining == 0:
            raise ValueError("cube_catalog 已全部挖过，可在挖组合页重置「已挖过」标记后重扫")

    if len(codes) > MAX_SCAN_LIMIT:
        raise ValueError(f"本批数量 {len(codes)} 超过上限 {MAX_SCAN_LIMIT}，请减小 batch_size")
    if not codes:
        return {
            "scanned": 0,
            "matched_count": 0,
            "not_found": 0,
            "filtered_out": 0,
            "items": [],
            "scan_mode": scan_mode,
            "catalog_pool_size": catalog_pool,
            "catalog_discovered_count": catalog_discovered,
            "catalog_remaining_count": catalog_remaining,
            "batch_start": batch_start,
            "batch_end": batch_end,
            "next_checkpoint": planned_next,
            "last_scanned_num": None,
        }

    followed = _fetch_followed_codes() if exclude_followed else set()
    api = XueQiuApiClient()
    matched: list[dict[str, Any]] = []
    scanned = 0
    not_found = 0
    filtered_out = 0
    consecutive_fail = 0

    total = len(codes)
    last_scanned_num: int | None = None
    for index, code in enumerate(codes, start=1):
        if cancel_check and cancel_check():
            if emit:
                emit({"type": "log", "level": "warn", "message": "■ 用户停止扫描"})
            break
        try:
            last_scanned_num = parse_zh_num(code)
        except ValueError:
            pass
        scanned += 1
        if emit:
            emit({"type": "progress", "current": index, "total": total, "code": code})

        if code in followed:
            filtered_out += 1
            if emit:
                emit({"type": "skip", "code": code, "reason": "已在关注列表"})
            _mark_catalog_dug(code, scan_mode)
            _sleep_between(sleep_seconds)
            continue

        try:
            metrics = _call_with_backoff(
                lambda c=code: probe_discover_metrics(c, api),
                emit=emit,
            )
            consecutive_fail = 0
        except Exception as exc:
            not_found += 1
            consecutive_fail += 1
            if emit:
                emit({"type": "skip", "code": code, "reason": _format_probe_skip_reason(exc)})
            if consecutive_fail >= CONSECUTIVE_FAIL_WARN and emit:
                emit(
                    {
                        "type": "log",
                        "level": "warn",
                        "message": (
                            f"连续 {consecutive_fail} 次请求失败，多为限流：建议点停止，"
                            f"等待 1～2 分钟后再扫，或加大间隔"
                        ),
                    }
                )
            _sleep_between(sleep_seconds)
            if _is_retryable_error(exc):
                time.sleep(random.uniform(*EXTRA_SLEEP_AFTER_HTTP_FAIL))
            _mark_catalog_dug(code, scan_mode)
            continue

        ok, reason = passes_hard_gates(
            metrics,
            max_inactive_days=max_inactive_days,
            max_rebalance_per_month=max_rebalance_per_month,
        )
        if not ok:
            filtered_out += 1
            if emit:
                emit({"type": "skip", "code": code, "reason": reason, "preview": metrics})
            _mark_catalog_dug(code, scan_mode)
            _sleep_between(sleep_seconds)
            continue

        matched_profiles, req_nav = match_archetypes(
            metrics,
            enabled_profiles,
            nav_at_5y=nav_at_5y,
            nav_at_10y=nav_at_10y,
            young_min_cum_pct=young_min_cum_pct,
        )
        if not matched_profiles:
            filtered_out += 1
            years = float(metrics.get("inception_years") or 0)
            if years >= MATURE_MIN_YEARS:
                detail = f"[画像] 成熟型：净值 {metrics.get('latest_nav')} < 门槛 {req_nav:.2f}"
            else:
                detail = f"[画像] 新锐型：累计 {metrics.get('cum_return_pct')}% < {young_min_cum_pct}%"
            if emit:
                emit({"type": "skip", "code": code, "reason": detail, "preview": metrics})
            _mark_catalog_dug(code, scan_mode)
            _sleep_between(sleep_seconds)
            continue

        item = {
            "account_code": metrics["account_code"],
            "account_name": metrics["account_name"],
            "latest_nav": metrics["latest_nav"],
            "cum_return_pct": metrics["cum_return_pct"],
            "latest_nav_date": metrics.get("latest_nav_date"),
            "latest_trade_time": metrics.get("latest_trade_time"),
            "inception_days": metrics.get("inception_days"),
            "inception_years": metrics.get("inception_years"),
            "matched_profiles": matched_profiles,
            "required_nav_threshold": req_nav,
            "already_followed": False,
        }
        matched.append(item)
        if emit:
            emit({"type": "hit", "item": item})
            emit(
                {
                    "type": "log",
                    "level": "success",
                    "message": (
                        f"★ 候选 {code} {item.get('account_name')} "
                        f"nav={item.get('latest_nav')} 画像={','.join(matched_profiles)}"
                    ),
                }
            )

        _mark_catalog_dug(code, scan_mode)
        _sleep_between(sleep_seconds)

    next_checkpoint = (
        (last_scanned_num + max(1, step)) if scan_mode == "sequential" and last_scanned_num else None
    )
    if scan_mode == "catalog" and catalog_discovered is not None and catalog_pool is not None:
        catalog_discovered = min(catalog_pool, catalog_discovered + scanned)
        catalog_remaining = max(0, catalog_pool - catalog_discovered)
    return {
        "scanned": scanned,
        "matched_count": len(matched),
        "not_found": not_found,
        "filtered_out": filtered_out,
        "items": matched,
        "scan_mode": scan_mode,
        "catalog_pool_size": catalog_pool,
        "catalog_discovered_count": catalog_discovered,
        "catalog_remaining_count": catalog_remaining,
        "batch_start": batch_start,
        "batch_end": batch_end,
        "last_scanned_num": last_scanned_num,
        "next_checkpoint": next_checkpoint if scan_mode == "sequential" else planned_next,
    }
