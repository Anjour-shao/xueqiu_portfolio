# scripts 目录说明

在项目根目录执行时，请先 `cd backend`，再 `python ../scripts/xxx.py`（脚本会自动把 `backend` 加入 Python 路径）。

**开发时后端 API**：在 `backend/` 下运行 `python main.py`（默认端口 **8011**，见 `backend/.env`）。Vite 将 `/api` 代理到 8011。新增 API 路由后需重启后端；修改 `vite.config.ts` 后需重启 `npm run dev`。可通过 `GET /health` 的 `api_version: "0.5.0"` 确认是否已加载新版本。

| 脚本 | 作用 | 是否常用 |
|------|------|----------|
| `xueqiu_login.py` | 本地弹窗扫码登录，写入 `data/xueqiu_cookie.txt` | 首次 / Cookie 过期时 |
| `test_xueqiu_api.py` | 测试雪球 API（讨论、用户、组合调仓、官方净值） | 排查连通性 |
| `xueqiu_monitor.py` | 定时巡检组合调仓，钉钉推送 + DeepSeek 舆情 | 服务器 cron |
| `sync_cube_nav.py` | 拉取全部 ZH 组合官方日净值 | 看板 K 线数据 |
| `sync_quotes.py` | 同步持仓标的后复权行情 + 基准指数（含涨跌幅） | 看板 / 也可在前端「数据同步」页触发 |
| `sync_tushare_aux.py` | TuShare 复权因子与基准（需 Token，可选） | 新浪不够时手动 |
| `backtest_copy_portfolios.py` | 抄作业回测 CLI，结果输出到 `scripts/backtest_output/` | 命令行回测 |
| `preview_latest_rebalance.py` | 本地预览：真实组合最新调仓 + 持仓 → `digest_output/` PNG | Digest 排版调试 |

已删除的冗余脚本：`xueqiu_api_probe.py`（与 test 重复）、`sina_hfq_cli.py`（调试用）、`recompute_all.py`（看板每次请求会重算）。
