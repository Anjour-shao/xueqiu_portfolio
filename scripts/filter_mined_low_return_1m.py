#!/usr/bin/env python3
"""筛出「已初筛通过」列表里近一个月涨幅低于阈值的组合（默认 < 10%）。

用法（在 backend 目录下，需 Cookie 与 ACCOUNT_DASHBOARD_DATABASE_URL）：

  # 默认：待决定列表，预览近 1 月涨幅 < 10% 的组合
  python ../scripts/filter_mined_low_return_1m.py

  # 导出 CSV
  python ../scripts/filter_mined_low_return_1m.py --csv ../filter_1m_low.csv

  # 全部初筛通过（含已选中/已拒绝）
  python ../scripts/filter_mined_low_return_1m.py --scope pass

  # 写入数据库：auto_pass=0，reject_reasons 追加 low_return_1m
  python ../scripts/filter_mined_low_return_1m.py --apply

  # 试跑 20 条
  python ../scripts/filter_mined_low_return_1m.py --limit 20 --sleep 2

说明：
  · 近 1 月涨幅 = 最新净值相对约 30 个自然日前净值的变化（%）
  · 结果缓存到 mined_cube_return_1m，断点续跑时跳过已算过的
  · 含雪球限流退避，可随时 Ctrl+C 中断
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from xueqiu.config import COOKIE_FILE  # noqa: E402
from xueqiu.integrations.xueqiu.auth import (  # noqa: E402
    cookie_has_login_fields,
    cookie_user_id,
    load_cookie,
)
from xueqiu.integrations.xueqiu.client import (  # noqa: E402
    XueQiuApiClient,
    XueQiuApiError,
    _is_cookie_invalid_error,
)
from xueqiu.integrations.xueqiu.portfolio import (  # noqa: E402
    CubeNavPoint,
    fetch_cube_nav_daily,
    validate_portfolio_id,
)
from xueqiu.storage.db import get_conn, init_db  # noqa: E402

_LOOKBACK_DAYS = 30
_BURST_FAIL_THRESHOLD = 3
_BURST_FAIL_EXTRA = (15.0, 30.0)
_RATE_LIMIT_EXTRA = (25.0, 45.0)
_DEFAULT_BATCH_EVERY = 35
_DEFAULT_BATCH_REST = 30.0

CREATE_CACHE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mined_cube_return_1m (
  account_code   VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
  return_1m_pct  DOUBLE       NULL,
  base_nav_date  VARCHAR(8)   NULL,
  latest_nav_date VARCHAR(8)  NULL,
  cum_return_pct DOUBLE       NULL,
  fetched_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SCOPE_SQL = {
    "pending": """
        SELECT mc.account_code, mc.account_name, mc.cum_return_pct
        FROM mined_cubes mc
        WHERE mc.auto_pass = 1
          AND mc.selected IS NULL
          AND mc.imported_at IS NULL
        ORDER BY mc.cum_return_pct DESC, mc.account_code
    """,
    "pass": """
        SELECT mc.account_code, mc.account_name, mc.cum_return_pct
        FROM mined_cubes mc
        WHERE mc.auto_pass = 1
        ORDER BY mc.cum_return_pct DESC, mc.account_code
    """,
    "selected": """
        SELECT mc.account_code, mc.account_name, mc.cum_return_pct
        FROM mined_cubes mc
        WHERE mc.auto_pass = 1
          AND mc.selected = 1
          AND mc.imported_at IS NULL
        ORDER BY mc.cum_return_pct DESC, mc.account_code
    """,
}


def _is_rate_limited(exc: XueQiuApiError) -> bool:
    msg = str(exc)
    return "10026" in msg or "刷新页面" in msg


def _load_candidates(scope: str) -> list[dict]:
    sql = SCOPE_SQL.get(scope)
    if not sql:
        raise ValueError(f"未知 scope: {scope}")
    with get_conn() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return [
        {
            "account_code": str(r.account_code).strip().upper(),
            "account_name": str(r.account_name or ""),
            "cum_return_pct": float(r.cum_return_pct) if r.cum_return_pct is not None else None,
        }
        for r in rows
    ]


def _cached_returns(conn, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
    params = {f"c{i}": c for i, c in enumerate(codes)}
    rows = conn.execute(
        text(
            f"""
            SELECT account_code, return_1m_pct, base_nav_date, latest_nav_date, cum_return_pct
            FROM mined_cube_return_1m
            WHERE account_code IN ({placeholders})
            """
        ),
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.account_code).strip().upper()
        out[code] = {
            "return_1m_pct": float(r.return_1m_pct) if r.return_1m_pct is not None else None,
            "base_nav_date": r.base_nav_date,
            "latest_nav_date": r.latest_nav_date,
            "cum_return_pct": float(r.cum_return_pct) if r.cum_return_pct is not None else None,
        }
    return out


def _upsert_cache(
    conn,
    *,
    code: str,
    return_1m_pct: float | None,
    base_nav_date: str | None,
    latest_nav_date: str | None,
    cum_return_pct: float | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO mined_cube_return_1m
              (account_code, return_1m_pct, base_nav_date, latest_nav_date, cum_return_pct, fetched_at)
            VALUES (:code, :ret, :base_d, :latest_d, :cum, NOW())
            ON DUPLICATE KEY UPDATE
              return_1m_pct = VALUES(return_1m_pct),
              base_nav_date = VALUES(base_nav_date),
              latest_nav_date = VALUES(latest_nav_date),
              cum_return_pct = VALUES(cum_return_pct),
              fetched_at = NOW()
            """
        ),
        {
            "code": code,
            "ret": return_1m_pct,
            "base_d": base_nav_date,
            "latest_d": latest_nav_date,
            "cum": cum_return_pct,
        },
    )


def compute_return_1m(points: list[CubeNavPoint], *, lookback_days: int = _LOOKBACK_DAYS) -> tuple[float | None, str | None, str | None]:
    """返回 (近 N 日涨幅%, 基准日 yyyymmdd, 最新日 yyyymmdd)。"""
    if len(points) < 2:
        return None, None, None
    latest = points[-1]
    try:
        latest_dt = datetime.strptime(latest.trade_date, "%Y%m%d")
    except ValueError:
        return None, None, None
    cutoff = (latest_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

    base: CubeNavPoint | None = None
    for p in points:
        if p.trade_date <= cutoff:
            base = p
    if base is None:
        return None, None, None
    if base.trade_date >= latest.trade_date or base.nav_value <= 0:
        return None, None, None

    ret = (latest.nav_value / base.nav_value - 1.0) * 100.0
    return round(ret, 2), base.trade_date, latest.trade_date


def _fetch_return_1m(api: XueQiuApiClient, code: str) -> tuple[float | None, str | None, str | None, float | None]:
    """与 discovery_mine 相同：全量 nav_daily，本地算近 1 月涨幅。"""
    sym = validate_portfolio_id(code)
    _, points = fetch_cube_nav_daily(sym, client=api)
    ret, base_d, latest_d = compute_return_1m(points)
    cum = float(points[-1].cum_return_pct) if points else None
    return ret, base_d, latest_d, cum


def _fetch_with_retry(
    api: XueQiuApiClient,
    code: str,
    *,
    retries: int,
) -> tuple[float | None, str | None, str | None, float | None]:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return _fetch_return_1m(api, code)
        except XueQiuApiError as exc:
            last_exc = exc
            if _is_cookie_invalid_error(exc):
                raise
            if attempt > retries:
                break
            if _is_rate_limited(exc):
                wait = random.uniform(*_RATE_LIMIT_EXTRA) * attempt
                tag = "10026 限流"
            else:
                wait = random.uniform(3.0, 6.0) * attempt
                tag = "重试"
            print(f"  {code} {tag}，{wait:.0f}s 后重试 ({attempt}/{retries + 1}): {exc}")
            time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            if attempt > retries:
                break
            time.sleep(random.uniform(4.0, 8.0) * attempt)
    raise last_exc or RuntimeError(f"{code} 净值拉取失败")


def _apply_low_return_1m(codes: list[str]) -> int:
    if not codes:
        return 0
    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
    params = {f"c{i}": c for i, c in enumerate(codes)}
    sql = f"""
    UPDATE mined_cubes mc
    SET
      mc.auto_pass = 0,
      mc.reject_reasons = CASE
        WHEN mc.reject_reasons IS NULL OR TRIM(mc.reject_reasons) = '' THEN
          JSON_ARRAY('low_return_1m')
        WHEN JSON_VALID(mc.reject_reasons)
             AND JSON_CONTAINS(CAST(mc.reject_reasons AS JSON), '"low_return_1m"', '$') THEN
          mc.reject_reasons
        WHEN JSON_VALID(mc.reject_reasons) THEN
          JSON_ARRAY_APPEND(CAST(mc.reject_reasons AS JSON), '$', 'low_return_1m')
        ELSE
          JSON_ARRAY('low_return_1m')
      END,
      mc.updated_at = NOW()
    WHERE mc.account_code IN ({placeholders})
    """
    with get_conn() as conn:
        res = conn.execute(text(sql), params)
        return int(res.rowcount or 0)


def _pause(sleep_base: float) -> None:
    lo = max(0.5, sleep_base * 0.85)
    hi = max(lo + 0.3, sleep_base * 1.35)
    time.sleep(random.uniform(lo, hi))


def _print_cookie_banner() -> None:
    try:
        cookie = load_cookie()
    except RuntimeError as exc:
        print(f"Cookie 未配置: {exc}")
        print("请运行: cd backend && python ../scripts/xueqiu_login.py")
        raise SystemExit(2) from exc
    uid = cookie_user_id(cookie)
    ok = cookie_has_login_fields(cookie)
    print(f"Cookie: {COOKIE_FILE} · uid={uid or '—'} · 字段{'完整' if ok else '不完整'}")
    if not ok:
        print("Cookie 缺少 xq_a_token / xq_r_token，请重新登录")
        raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="筛出初筛列表里近 1 月涨幅低于阈值的组合",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scope",
        choices=("pending", "pass", "selected"),
        default="pending",
        help="候选范围：pending=待决定(默认), pass=全部初筛通过, selected=已选中未入库",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="涨幅阈值(%%)，近 1 月涨幅严格低于该值的组合会被列出（默认 10）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理条数，0=全部")
    parser.add_argument("--sleep", type=float, default=1.5, help="请求间隔基准秒数")
    parser.add_argument("--retries", type=int, default=5, help="单条失败重试次数")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存，重新拉净值")
    parser.add_argument("--csv", type=str, default="", help="导出 CSV 路径")
    parser.add_argument("--apply", action="store_true", help="将命中组合 auto_pass=0 并打 low_return_1m")
    parser.add_argument("--batch-every", type=int, default=_DEFAULT_BATCH_EVERY)
    parser.add_argument("--batch-rest", type=float, default=_DEFAULT_BATCH_REST)
    args = parser.parse_args()

    init_db()
    candidates = _load_candidates(args.scope)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    total = len(candidates)
    if total == 0:
        print("没有符合条件的候选组合")
        return 0

    print(f"范围={args.scope}，共 {total} 条，阈值=近1月涨幅 < {args.threshold}%")
    _print_cookie_banner()
    print("说明：Discover 页点选/拒绝只写数据库；本脚本拉净值需雪球 Cookie 有效")

    api = XueQiuApiClient()
    hits: list[dict] = []
    ok = skip = fail = 0
    consecutive_fail = 0
    consecutive_nav_denied = 0

    with get_conn() as conn:
        conn.execute(text(CREATE_CACHE_TABLE_SQL))
        cache = {} if args.refresh else _cached_returns(conn, [c["account_code"] for c in candidates])

        for i, row in enumerate(candidates, 1):
            code = row["account_code"]
            name = row["account_name"]
            cached = cache.get(code)

            if cached is not None and cached.get("return_1m_pct") is not None and not args.refresh:
                ret = cached["return_1m_pct"]
                base_d = cached["base_nav_date"]
                latest_d = cached["latest_nav_date"]
                cum = cached["cum_return_pct"] if cached.get("cum_return_pct") is not None else row["cum_return_pct"]
                skip += 1
            else:
                try:
                    ret, base_d, latest_d, cum = _fetch_with_retry(api, code, retries=args.retries)
                    _upsert_cache(
                        conn,
                        code=code,
                        return_1m_pct=ret,
                        base_nav_date=base_d,
                        latest_nav_date=latest_d,
                        cum_return_pct=cum if cum is not None else row["cum_return_pct"],
                    )
                    ok += 1
                    consecutive_fail = 0
                    consecutive_nav_denied = 0
                except XueQiuApiError as exc:
                    if _is_cookie_invalid_error(exc):
                        consecutive_nav_denied += 1
                        fail += 1
                        print(
                            f"[{i}/{total}] {code} 净值不可用（400016，"
                            f"可能私密/下架或需稍后再试）"
                        )
                        if consecutive_nav_denied >= 8 and ok == 0 and skip == 0:
                            print(
                                "连续多条净值 400016 且无成功记录。"
                                "常见于 Cookie 过期或频繁爬取后被雪球暂时封禁。"
                                "请运行: cd backend && python ../scripts/xueqiu_login.py"
                            )
                            return 2
                        if i < total:
                            _pause(args.sleep)
                        continue
                    consecutive_nav_denied = 0
                    fail += 1
                    consecutive_fail += 1
                    print(f"[{i}/{total}] {code} 失败: {exc}")
                    if consecutive_fail >= _BURST_FAIL_THRESHOLD:
                        extra = random.uniform(*_BURST_FAIL_EXTRA)
                        print(f"  连续失败 {consecutive_fail} 次，暂停 {extra:.0f}s …")
                        time.sleep(extra)
                        consecutive_fail = 0
                    if i < total:
                        _pause(args.sleep)
                    continue
                except Exception as exc:
                    fail += 1
                    consecutive_fail += 1
                    print(f"[{i}/{total}] {code} 异常: {exc}")
                    if i < total:
                        _pause(args.sleep)
                    continue

                if ok > 0 and args.batch_every > 0 and ok % args.batch_every == 0:
                    rest = random.uniform(args.batch_rest * 0.8, args.batch_rest * 1.3)
                    print(f"  已拉取 {ok} 条，批次休息 {rest:.0f}s …")
                    time.sleep(rest)

            if ret is None:
                if i % 50 == 0:
                    print(f"[{i}/{total}] 进度：命中 {len(hits)}，拉取 {ok}，缓存 {skip}，失败 {fail}")
            elif ret < args.threshold:
                hits.append(
                    {
                        "account_code": code,
                        "account_name": name,
                        "return_1m_pct": ret,
                        "cum_return_pct": cum,
                        "base_nav_date": base_d,
                        "latest_nav_date": latest_d,
                    }
                )

            if i % 50 == 0 or i == total:
                print(f"[{i}/{total}] 进度：命中 {len(hits)}，拉取 {ok}，缓存 {skip}，失败 {fail}")

            if i < total and (cached is None or args.refresh or cached.get("return_1m_pct") is None):
                _pause(args.sleep)

    hits.sort(key=lambda x: (x["return_1m_pct"], x["account_code"]))

    print("-" * 88)
    print(f"近 1 月涨幅 < {args.threshold}% 共 {len(hits)} 条")
    print("-" * 88)
    print(f"{'代码':<12} {'近1月%':>8} {'累计%':>10} {'基准日':>10} {'最新日':>10}  名称")
    for h in hits[:50]:
        cum_s = f"{h['cum_return_pct']:.2f}" if h["cum_return_pct"] is not None else "—"
        print(
            f"{h['account_code']:<12} {h['return_1m_pct']:>8.2f} {cum_s:>10} "
            f"{h['base_nav_date'] or '—':>10} {h['latest_nav_date'] or '—':>10}  {h['account_name']}"
        )
    if len(hits) > 50:
        print(f"... 共 {len(hits)} 条，仅显示前 50 条")

    if args.csv:
        out_path = Path(args.csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "account_code",
                    "account_name",
                    "return_1m_pct",
                    "cum_return_pct",
                    "base_nav_date",
                    "latest_nav_date",
                ],
            )
            writer.writeheader()
            writer.writerows(hits)
        print(f"已导出 {out_path}")

    if args.apply:
        codes = [h["account_code"] for h in hits]
        n = _apply_low_return_1m(codes)
        print(f"已更新 {n} 条：auto_pass=0，reject_reasons += low_return_1m")

    print(f"完成：拉取 {ok}，读缓存 {skip}，失败 {fail}，命中 {len(hits)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
