-- =============================================================================
-- 挖组合附加筛选：成立超过 8 年 且 累计收益未达 10 倍 → 排除
--
-- 规则说明
--   · 成立日：优先 mined_cube_created_at（雪球 show.created_at）
--             其次已入库组合的 cube_nav_points 最早净值日 / rebalance_trades 最早调仓日
--   · 10 倍收益：cum_return_pct >= 900（雪球 nav percent，1→10 约为 +900%）
--   · 排除动作：auto_pass=0，reject_reasons 追加 "old_low_return"
--
-- 使用前请先回填「待决定」组合的成立日（初筛通过且未标记选中/拒绝）：
--   cd backend && python ../scripts/backfill_mined_cube_created_at.py
--
-- 执行顺序：① 预览 SELECT  →  ② 确认无误后执行 UPDATE
-- =============================================================================

USE portfolio;

-- 成立日辅助表（由 backfill 脚本写入，也可手工维护）
CREATE TABLE IF NOT EXISTS mined_cube_created_at (
  account_code VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
  created_at   DATETIME     NOT NULL COMMENT '雪球 cubes/show.json created_at',
  fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- ① 预览：将被本规则排除的组合
-- ---------------------------------------------------------------------------
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
ORDER BY cum_return_pct ASC, account_code;


-- ---------------------------------------------------------------------------
-- ② 统计：尚无成立日、无法套用本规则的条数
-- ---------------------------------------------------------------------------
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
  );


-- ---------------------------------------------------------------------------
-- ③ 执行排除（确认预览结果后再取消注释运行）
-- ---------------------------------------------------------------------------
/*
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
  mc.updated_at = NOW();
*/
