/*
  portfolio 库 — 精简建表脚本
  仅保留调仓分析所需 4 张表。
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `accounts`;
CREATE TABLE `accounts` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account_code` varchar(64) NOT NULL COMMENT '雪球组合 ID 或自定义账户代码',
  `account_name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_accounts_code` (`account_code`),
  KEY `idx_accounts_name` (`account_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `rebalance_trades`;
CREATE TABLE `rebalance_trades` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account_id` int NOT NULL,
  `trade_time` varchar(32) NOT NULL,
  `stock_name` varchar(255) NOT NULL,
  `ts_code` varchar(32) NOT NULL,
  `from_weight` double NOT NULL,
  `to_weight` double NOT NULL,
  `weight_delta` double NOT NULL,
  `action` varchar(32) NOT NULL,
  `price` double DEFAULT NULL COMMENT '雪球参考成交价（未复权）',
  `price_hfq` double DEFAULT NULL COMMENT '后复权成交价',
  `raw_block` text NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_rebalance_trade_identity` (`account_id`,`trade_time`,`ts_code`,`from_weight`,`to_weight`,`price`),
  KEY `idx_rebalance_account_time` (`account_id`,`trade_time`),
  KEY `idx_rebalance_code_time` (`ts_code`,`trade_time`),
  CONSTRAINT `fk_rebalance_trades_account` FOREIGN KEY (`account_id`) REFERENCES `accounts` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `quote_points`;
CREATE TABLE `quote_points` (
  `ts_code` varchar(16) NOT NULL COMMENT '雪球格式，如 SZ300476',
  `trade_date` varchar(8) NOT NULL COMMENT 'YYYYMMDD',
  `adj_factor` float NOT NULL DEFAULT 1 COMMENT 'TuShare 后复权因子（调仓日）',
  `close_hfq` float DEFAULT NULL COMMENT '新浪后复权收盘价',
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_quote_points_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `benchmark`;
CREATE TABLE `benchmark` (
  `ts_code` varchar(16) NOT NULL COMMENT 'TuShare 指数代码，如 000001.SH',
  `trade_date` varchar(8) NOT NULL COMMENT 'YYYYMMDD',
  `close` float NOT NULL COMMENT '指数收盘点位',
  `pct_chg` double DEFAULT NULL COMMENT '日涨跌幅 %',
  `cum_return_pct` double DEFAULT NULL COMMENT '相对同步序列首日的累计收益 %',
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_benchmark_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `cube_nav_points`;
CREATE TABLE `cube_nav_points` (
  `account_id` int NOT NULL,
  `trade_date` varchar(8) NOT NULL COMMENT 'YYYYMMDD',
  `nav_value` double NOT NULL COMMENT '雪球官方净值',
  `cum_return_pct` double NOT NULL COMMENT '累计收益率 %',
  `synced_at` datetime NOT NULL,
  PRIMARY KEY (`account_id`,`trade_date`),
  KEY `idx_cube_nav_account_date` (`account_id`,`trade_date`),
  CONSTRAINT `fk_cube_nav_account` FOREIGN KEY (`account_id`) REFERENCES `accounts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
