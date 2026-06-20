# scripts 目录说明

在项目根目录执行时，请先 `cd backend`，再 `python ../scripts/xxx.py`（脚本会自动把 `backend` 加入 Python 路径）。

**开发时后端**：`cd backend && python main.py`（默认 **8011**，见 `backend/.env`）。Vite 将 `/api` 代理到 8011。

---

## 日常运维

| 脚本 | 作用 |
|------|------|
| `xueqiu_login.py` | 本地扫码登录，写入 `data/xueqiu_cookie.txt` |
| `sync_quotes.py` | 同步持仓标的后复权 + 基准指数 |
| `sync_cube_nav.py` | 拉取全部 ZH 官方日净值 |
| `xueqiu_monitor.py` | 调仓巡检 + 钉钉 + DeepSeek（cron 用） |
| `backtest_copy_portfolios.py` | 抄作业回测 CLI |

也可在前端 **数据同步** 页一键全量同步。

---

## 挖组合 / 数据维护

| 脚本 | 作用 |
|------|------|
| `generate_discovery_hot_symbols.py` | 生成/更新挖组合热门股池 |
| `backfill_mined_cube_created_at.py` | 回填候选组合创建时间 |
| `filter_mined_low_return_1m.py` | 按近 1 月收益筛选候选 |
| `run_filter_mined_old_low_return.py` | 批量执行低收益过滤 |
| `migrate_personal_holding_codes.py` | 一次性：个人持仓 6 位代码 → SH/SZ 前缀 |

---

## 分析（按需）

| 脚本 | 作用 |
|------|------|
| `compare_copy_strategies.py` | 多策略对比输出 |
| `analyze_portfolio_overlap.py` | 组合持仓重叠分析 |
| `analyze_heavy_position_winrate.py` | 师傅重仓 leg 胜率分析 |
| `_fetch_volume_top100.py` | 拉取成交额 Top 标的（辅助股池） |

---

## 每日 Digest

云端简报、预览、GHA 触发见 [`daily_digest/`](../daily_digest/README.md)，入口脚本：

```bash
python daily_digest/daily_portfolio_digest.py
python daily_digest/preview_latest_rebalance.py   # 本地预览排版，不写 state
```

---

## 输出目录

`scripts/backtest_output/`、`digest_output/` 为**本地生成物**，已在根目录 `.gitignore` 中忽略，无需提交。

---

## 单元测试

自动化测试在 `backend/tests/`，勿与 `scripts/` 下临时脚本混淆：

```bash
cd backend && python -m pytest tests/ -q
```
