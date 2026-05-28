# 雪球组合调仓分析

基于雪球组合调仓日志的录入、收益统计与组合监控工具。全部雪球数据抓取已统一为 **requests + Cookie API**，无需浏览器（云端友好）。

## 目录结构

```
xueqiu/
├── README.md
├── daily_portfolio_digest.py      # 每晚 Digest 入口（GHA）
├── daily_digest_requirements.txt
├── digest/                        # HTML → 图片 → 钉钉
├── sql/                           # 数据库脚本
├── scripts/                       # 命令行运维（见 scripts/README.md）
│   ├── xueqiu_login.py            # 扫码写 Cookie
│   ├── test_xueqiu_api.py         # API 连通性测试
│   ├── xueqiu_monitor.py          # 调仓巡检 + 钉钉 + DeepSeek
│   ├── sync_cube_nav.py           # 官方日净值同步
│   ├── sync_quotes.py             # 后复权行情同步
│   ├── sync_tushare_aux.py        # 基准指数（TuShare，可选）
│   └── backtest_copy_portfolios.py
├── backend/
│   ├── main.py                    # 启动 API
│   └── xueqiu/
│       ├── config.py              # 环境变量
│       ├── api/                   # FastAPI 路由与业务编排
│       ├── domain/                # 核心业务（净值、回测、代码转换）
│       ├── storage/               # 数据库
│       ├── integrations/          # 外部数据源
│       │   ├── xueqiu/            # 雪球 HTTP API（调仓、讨论、用户发言）
│       │   └── sina/              # 新浪后复权价 / 指数
│       └── sync/                  # 批处理同步任务
├── data/
│   └── xueqiu_cookie.txt          # 雪球登录凭证（gitignore）
└── frontend/                      # React 看板
```

## 分层说明

| 层 | 路径 | 职责 |
|---|---|---|
| **api** | `backend/xueqiu/api/` | HTTP 接口、看板、导入、同步编排 |
| **domain** | `backend/xueqiu/domain/` | 虚拟净值法、抄作业回测、代码格式转换 |
| **storage** | `backend/xueqiu/storage/` | MySQL 表定义与连接 |
| **integrations** | `backend/xueqiu/integrations/` | 雪球 API、新浪行情等外部 I/O |
| **sync** | `backend/xueqiu/sync/` | 行情批量同步逻辑 |
| **scripts** | `scripts/` | 命令行运维工具 |

## 数据库

MySQL 库名 `portfolio`，4 张表：`accounts`、`rebalance_trades`、`quote_points`、`benchmark`。

```bash
mysql -u root -p portfolio < sql/schema.sql
```

## 配置

```bash
cd backend
copy .env.example .env
```

```bash
ACCOUNT_DASHBOARD_DATABASE_URL=mysql+pymysql://用户名:密码@127.0.0.1:3306/portfolio?charset=utf8mb4
TUSHARE_API_KEY=你的TUSHARE_TOKEN          # 可选
BENCHMARK_TS_CODE=000001.SH
DINGTALK_WEBHOOK=                          # 可选，monitor 钉钉推送
DEEPSEEK_API_KEY=                          # 可选，monitor AI 分析
```

## 雪球认证（Cookie）

**本地首次登录：**

```bash
cd backend
pip install -e ".[login]"          # 仅本地需要，含 DrissionPage
python ../scripts/xueqiu_login.py  # 弹窗扫码，写入 data/xueqiu_cookie.txt
```

**云端部署：** 上传 `data/xueqiu_cookie.txt`，或设置环境变量 `XUEQIU_COOKIE`。生产环境 `pip install -e .` 即可，不需要 DrissionPage。

Cookie 过期后重新在本地运行 `xueqiu_login.py` 并上传新文件。

## 启动

**后端：**

```bash
cd backend
pip install -r requirements.txt    # 或 pip install -e .
python main.py
```

**前端：**

```bash
cd frontend
npm install
npm run dev
```

- 前端：http://localhost:5176（API 代理到 **8011**）
- 后端：http://localhost:8011（`backend/.env` 中 `ACCOUNT_DASHBOARD_PORT=8011`）

若仍出现 API 404：8010 上可能残留旧进程。请只保留一个后端（`cd backend && python main.py`），并**重启**前端 `npm run dev` 使代理生效。`/health` 应含 `api_version: "0.5.0"`。

## 常用命令

```bash
cd backend

# API 连通性测试（个股讨论 + 用户发言）
python ../scripts/test_xueqiu_api.py
python ../scripts/test_xueqiu_api.py --portfolio ZH3207026

# 调仓巡检
python ../scripts/xueqiu_monitor.py

# 行情同步（个股 HFQ）
python ../scripts/sync_quotes.py

# 官方净值同步（看板 K 线）
python ../scripts/sync_cube_nav.py

# 抄作业回测 CLI
python ../scripts/backtest_copy_portfolios.py
```

## 数据流

```
雪球 API（调仓 / 讨论）
        ↓
  rebalance_trades
        ↓
 sync/sync_quotes.py（新浪后复权价）
        ↓
 domain/nav_engine.py（虚拟净值法）
        ↓
  看板 API → 累计/基准/超额收益
```

## API 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/accounts` | 账户列表 |
| GET | `/api/dashboard/{account_key}` | 看板数据 |
| POST | `/api/sync-xueqiu/{account_key}` | 雪球 API 抓取单个组合最新调仓 |
| POST | `/api/sync-xueqiu-all` | 雪球全量抓取 |
| POST | `/api/import-logs` | 导入调仓日志 |
| POST | `/api/recompute/{account_key}` | 重算收益 |

## 每日组合 Digest（GitHub Actions）

每晚 8 点推送关注组合调仓 + 个人持仓行情（HTML 渲染为图片 → 钉钉）。

```
daily_portfolio_digest.py      # 入口（GHA 调用）
daily_digest_requirements.txt
digest/
  render.py                    # HTML → PNG → 图床 → 钉钉
  templates/report.html
scripts/preview_latest_rebalance.py   # 本地预览真实调仓排版
docs/DIGEST_GITHUB_SETUP.md    # 部署与 Secrets 清单
.github/workflows/daily_digest.yml
```

配置与上线步骤见 [docs/DIGEST_GITHUB_SETUP.md](docs/DIGEST_GITHUB_SETUP.md)。
