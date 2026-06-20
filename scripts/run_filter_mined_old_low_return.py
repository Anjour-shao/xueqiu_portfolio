#!/usr/bin/env python3
"""运行 filter_mined_old_low_return.sql：预览或执行 UPDATE。

用法（在 backend 目录下）：

  # ① 先回填待决定组合的成立日（见 backfill_mined_cube_created_at.py）
  python ../scripts/backfill_mined_cube_created_at.py

  # ② 预览将被「8年未10倍」规则打标的组合
  python ../scripts/run_filter_mined_old_low_return.py

  # ③ 确认后写入数据库（auto_pass=0 + old_low_return）
  python ../scripts/run_filter_mined_old_low_return.py --apply

也可直接在 MySQL 客户端执行 scripts/sql/filter_mined_old_low_return.sql 里的 SELECT。

注意：若已在 Discover 页重新跑挖掘，初筛已含该规则，通常不必再跑本脚本。
本脚本适合对「已有待决定列表」做一次性批量补标，无需重爬全量。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from xueqiu.storage.db import get_conn, init_db  # noqa: E402

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mined_cube_created_at (
  account_code VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
  created_at   DATETIME     NOT NULL,
  fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

PREVIEW_SQL = """
WITH cube_inception AS (
  SELECT
    mc.account_code,
    mc.account_name,
    mc.cum_return_pct,
    mc.auto_pass,
    mc.reject_reasons,
    COALESCE(
      ca.created_at,
      STR_TO_DATE(nav.first_nav_date, '%Y%m%d'),
      rb.first_rebalance
    ) AS inception_at,
    CASE
      WHEN ca.created_at IS NOT NULL THEN 'show_api'
      WHEN nav.first_nav_date IS NOT NULL THEN 'cube_nav_points'
      WHEN rb.first_rebalance IS NOT NULL THEN 'rebalance_trades'
      ELSE NULL
    END AS inception_source,
    TIMESTAMPDIFF(
      YEAR,
      COALESCE(
        ca.created_at,
        STR_TO_DATE(nav.first_nav_date, '%Y%m%d'),
        rb.first_rebalance
      ),
      CURDATE()
    ) AS age_years
  FROM mined_cubes mc
  LEFT JOIN mined_cube_created_at ca
    ON ca.account_code = mc.account_code
  LEFT JOIN accounts a
    ON a.account_code = mc.account_code
  LEFT JOIN (
    SELECT account_id, MIN(trade_date) AS first_nav_date
    FROM cube_nav_points
    GROUP BY account_id
  ) nav ON nav.account_id = a.id
  LEFT JOIN (
    SELECT
      account_id,
      MIN(STR_TO_DATE(LEFT(trade_time, 10), '%Y-%m-%d')) AS first_rebalance
    FROM rebalance_trades
    GROUP BY account_id
  ) rb ON rb.account_id = a.id
  WHERE mc.auto_pass = 1
    AND mc.selected IS NULL
    AND mc.imported_at IS NULL
)
SELECT
  account_code,
  account_name,
  inception_at,
  inception_source,
  age_years,
  cum_return_pct,
  ROUND(cum_return_pct / 100 + 1, 2) AS nav_multiple_approx,
  auto_pass,
  reject_reasons
FROM cube_inception
WHERE inception_at IS NOT NULL
  AND inception_at <= DATE_SUB(CURDATE(), INTERVAL 8 YEAR)
  AND cum_return_pct IS NOT NULL
  AND cum_return_pct < 900
ORDER BY cum_return_pct ASC, account_code
"""

MISSING_INCEPTION_SQL = """
SELECT COUNT(*) AS missing_inception_count
FROM mined_cubes mc
LEFT JOIN mined_cube_created_at ca ON ca.account_code = mc.account_code
LEFT JOIN accounts a ON a.account_code = mc.account_code
WHERE mc.auto_pass = 1
  AND mc.selected IS NULL
  AND mc.imported_at IS NULL
  AND ca.created_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM cube_nav_points cnp WHERE cnp.account_id = a.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM rebalance_trades rt WHERE rt.account_id = a.id
  )
"""

UPDATE_SQL = """
UPDATE mined_cubes mc
INNER JOIN (
  SELECT mc2.account_code
  FROM mined_cubes mc2
  LEFT JOIN mined_cube_created_at ca
    ON ca.account_code = mc2.account_code
  LEFT JOIN accounts a
    ON a.account_code = mc2.account_code
  LEFT JOIN (
    SELECT account_id, MIN(trade_date) AS first_nav_date
    FROM cube_nav_points
    GROUP BY account_id
  ) nav ON nav.account_id = a.id
  LEFT JOIN (
    SELECT
      account_id,
      MIN(STR_TO_DATE(LEFT(trade_time, 10), '%Y-%m-%d')) AS first_rebalance
    FROM rebalance_trades
    GROUP BY account_id
  ) rb ON rb.account_id = a.id
  WHERE COALESCE(
          ca.created_at,
          STR_TO_DATE(nav.first_nav_date, '%Y%m%d'),
          rb.first_rebalance
        ) IS NOT NULL
    AND COALESCE(
          ca.created_at,
          STR_TO_DATE(nav.first_nav_date, '%Y%m%d'),
          rb.first_rebalance
        ) <= DATE_SUB(CURDATE(), INTERVAL 8 YEAR)
    AND mc2.cum_return_pct IS NOT NULL
    AND mc2.cum_return_pct < 900
    AND mc2.auto_pass = 1
    AND mc2.selected IS NULL
    AND mc2.imported_at IS NULL
) hit ON hit.account_code = mc.account_code
SET
  mc.auto_pass = 0,
  mc.reject_reasons = CASE
    WHEN mc.reject_reasons IS NULL OR TRIM(mc.reject_reasons) = '' THEN
      JSON_ARRAY('old_low_return')
    WHEN JSON_VALID(mc.reject_reasons)
         AND JSON_CONTAINS(CAST(mc.reject_reasons AS JSON), '"old_low_return"', '$') THEN
      mc.reject_reasons
    WHEN JSON_VALID(mc.reject_reasons) THEN
      JSON_ARRAY_APPEND(CAST(mc.reject_reasons AS JSON), '$', 'old_low_return')
    ELSE
      JSON_ARRAY('old_low_return')
  END,
  mc.updated_at = NOW()
"""


def _print_rows(title: str, result) -> None:
    if not result.returns_rows:
        return
    rows = result.fetchall()
    cols = list(result.keys())
    print("-" * 72)
    print(title)
    print("-" * 72)
    print("\t".join(cols))
    for row in rows[:30]:
        print("\t".join(str(v) for v in row))
    if len(rows) > 30:
        print(f"... 共 {len(rows)} 行，仅显示前 30 行")
    elif not rows:
        print("(无匹配行)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="执行 UPDATE 排除（默认仅预览）")
    args = parser.parse_args()

    init_db()

    with get_conn() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        _print_rows("预览：待决定且「8年未10倍」将被排除", conn.execute(text(PREVIEW_SQL)))
        _print_rows("统计：尚无成立日、无法判断", conn.execute(text(MISSING_INCEPTION_SQL)))

        if args.apply:
            print("-" * 72)
            print("执行 UPDATE ...")
            res = conn.execute(text(UPDATE_SQL))
            print(f"已更新 {res.rowcount} 行")
        else:
            print("-" * 72)
            print("预览模式。确认无误后加 --apply 执行排除。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
