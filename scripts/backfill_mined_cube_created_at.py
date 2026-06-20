#!/usr/bin/env python3
"""回填「待决定」候选组合的成立日（show.created_at），供 filter SQL 使用。

用法（在 backend 目录下，需已配置 ACCOUNT_DASHBOARD_DATABASE_URL 与 Cookie）：

  # 推荐：慢速全量，断点续跑（已写入的会自动跳过）
  python ../scripts/backfill_mined_cube_created_at.py

  # 先试跑 20 条
  python ../scripts/backfill_mined_cube_created_at.py --limit 20

  # 更保守（连续 10026 时可加大间隔）
  python ../scripts/backfill_mined_cube_created_at.py --sleep 2.5 --batch-every 25 --batch-rest 60

说明：
  · 只处理 auto_pass=1 且 selected 为空、未入库的「待决定」组合
  · 遇到 error_code=10026 会退避重试；连续失败会长暂停
  · 可随时 Ctrl+C 中断，下次运行从未回填的继续
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from xueqiu.integrations.xueqiu.client import (  # noqa: E402
    XueQiuApiClient,
    XueQiuApiError,
    _is_cookie_invalid_error,
)
from xueqiu.integrations.xueqiu.portfolio import validate_portfolio_id  # noqa: E402
from xueqiu.storage.db import get_conn, init_db  # noqa: E402

CUBE_SHOW_URL = "https://xueqiu.com/cubes/show.json"

# 与 discovery_mine 同量级，略保守（单接口连续刷容易 10026）
_PAUSE = (1.2, 2.0)
_BURST_FAIL_THRESHOLD = 3
_BURST_FAIL_EXTRA = (15.0, 30.0)
_RATE_LIMIT_EXTRA = (25.0, 45.0)
_DEFAULT_BATCH_EVERY = 35
_DEFAULT_BATCH_REST = (25.0, 40.0)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mined_cube_created_at (
  account_code VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
  created_at   DATETIME     NOT NULL,
  fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _missing_codes(conn) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT mc.account_code
            FROM mined_cubes mc
            LEFT JOIN mined_cube_created_at ca ON ca.account_code = mc.account_code
            WHERE ca.account_code IS NULL
              AND mc.auto_pass = 1
              AND mc.selected IS NULL
              AND mc.imported_at IS NULL
            ORDER BY mc.account_code
            """
        )
    ).fetchall()
    return [str(r.account_code).strip().upper() for r in rows]


def _upsert_created_at(conn, code: str, created_at: datetime) -> None:
    conn.execute(
        text(
            """
            INSERT INTO mined_cube_created_at (account_code, created_at, fetched_at)
            VALUES (:code, :created_at, NOW())
            ON DUPLICATE KEY UPDATE
              created_at = VALUES(created_at),
              fetched_at = NOW()
            """
        ),
        {"code": code, "created_at": created_at.replace(tzinfo=None)},
    )


def _is_rate_limited(exc: XueQiuApiError) -> bool:
    msg = str(exc)
    return "10026" in msg or "刷新页面" in msg


def _fetch_created_at_ms(api: XueQiuApiClient, sym: str) -> int | None:
    data = api.get_json_with_retry(
        CUBE_SHOW_URL,
        params={"symbol": sym},
        referer=f"https://xueqiu.com/P/{sym}",
        warm_symbol=sym,
        max_retries=5,
        delay=(2.0, 3.5),
    )
    if not isinstance(data, dict):
        return None
    raw = data.get("created_at")
    if raw is None:
        return None
    return int(raw)


def _pause_between_requests(sleep_base: float) -> None:
    lo = max(0.5, sleep_base * 0.85)
    hi = max(lo + 0.3, sleep_base * 1.35)
    time.sleep(random.uniform(lo, hi))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="回填「待决定」候选组合的成立日（带雪球限流退避）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="中断后可重新运行，已成功的不会重复请求。",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理条数，0=全部剩余")
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="请求间隔基准秒数，实际会在附近随机抖动（默认 1.5）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="单条组合失败后的额外重试次数（默认 5）",
    )
    parser.add_argument(
        "--batch-every",
        type=int,
        default=_DEFAULT_BATCH_EVERY,
        help=f"每成功 N 条额外休息一次（默认 {_DEFAULT_BATCH_EVERY}，0=关闭）",
    )
    parser.add_argument(
        "--batch-rest",
        type=float,
        default=30.0,
        help="批次休息秒数（默认 30，实际随机 0.8~1.3 倍）",
    )
    args = parser.parse_args()

    init_db()
    api = XueQiuApiClient()
    ok = fail = skip = 0
    consecutive_fail = 0

    with get_conn() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        codes = _missing_codes(conn)
        if args.limit > 0:
            codes = codes[: args.limit]

        total = len(codes)
        if total == 0:
            print("没有待回填的组合（待决定且 mined_cube_created_at 中尚无记录）")
            return 0

        print(f"待回填 {total} 条（间隔约 {args.sleep}s，batch 每 {args.batch_every} 条休息）")

        for i, code in enumerate(codes, 1):
            sym = validate_portfolio_id(code)
            saved = False
            last_exc: Exception | None = None

            for attempt in range(1, args.retries + 2):
                try:
                    ms = _fetch_created_at_ms(api, sym)
                    if ms is None:
                        print(f"[{i}/{total}] {code} 无 created_at，跳过")
                        skip += 1
                        saved = True
                        break
                    created = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                    _upsert_created_at(conn, code, created)
                    ok += 1
                    consecutive_fail = 0
                    saved = True
                    if i % 20 == 0 or i == total:
                        print(f"[{i}/{total}] 进度：成功 {ok}，失败 {fail}，跳过 {skip}")
                    break
                except XueQiuApiError as exc:
                    last_exc = exc
                    if _is_cookie_invalid_error(exc):
                        print(f"Cookie 失效，请先运行: python ../scripts/xueqiu_login.py")
                        print(f"  最后错误: {exc}")
                        return 2
                    if attempt > args.retries:
                        break
                    if _is_rate_limited(exc):
                        wait = random.uniform(*_RATE_LIMIT_EXTRA) * attempt
                        tag = "10026 限流"
                    elif consecutive_fail + 1 >= _BURST_FAIL_THRESHOLD:
                        wait = random.uniform(*_BURST_FAIL_EXTRA)
                        tag = "连续失败"
                    else:
                        wait = random.uniform(3.0, 6.0) * attempt
                        tag = "重试"
                    print(
                        f"[{i}/{total}] {code} {tag}，"
                        f"{wait:.0f}s 后重试 ({attempt}/{args.retries + 1}): {exc}"
                    )
                    time.sleep(wait)
                except (ValueError, TypeError) as exc:
                    last_exc = exc
                    print(f"[{i}/{total}] {code} 数据异常: {exc}")
                    skip += 1
                    saved = True
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt > args.retries:
                        break
                    wait = random.uniform(4.0, 8.0) * attempt
                    print(f"[{i}/{total}] {code} 异常，{wait:.0f}s 后重试: {exc}")
                    time.sleep(wait)

            if not saved:
                fail += 1
                consecutive_fail += 1
                print(f"[{i}/{total}] {code} 最终失败: {last_exc}")
                if consecutive_fail >= _BURST_FAIL_THRESHOLD:
                    extra = random.uniform(*_BURST_FAIL_EXTRA)
                    print(f"  连续失败 {consecutive_fail} 次，暂停 {extra:.0f}s …")
                    time.sleep(extra)
                    consecutive_fail = 0
            elif ok > 0 and args.batch_every > 0 and ok % args.batch_every == 0:
                rest = random.uniform(args.batch_rest * 0.8, args.batch_rest * 1.3)
                print(f"  已成功 {ok} 条，批次休息 {rest:.0f}s …")
                time.sleep(rest)

            if i < total:
                _pause_between_requests(args.sleep)

    print(f"完成：成功 {ok}，失败 {fail}，跳过 {skip}")
    if fail:
        print("仍有失败时可加大 --sleep / --batch-rest 后重新运行（只会补剩余）")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
