# 雪球组合 · 调仓分析与抄作业工具

个人使用的雪球（Xueqiu）组合分析项目：**录入/同步调仓 → 虚拟净值与收益统计 → 多组合对比 → 抄作业策略回测 → 挖组合 → 每日钉钉简报**。

技术栈：**FastAPI + MySQL + React (MUI)**。雪球数据抓取统一为 **requests + Cookie API**，适合本地与云端定时任务，无需浏览器自动化（登录除外）。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **总览** | 已入库 ZH 组合卡片、收益排行、快捷入口 |
| **我的持仓** | 维护实盘持仓/现金，选择抄作业策略；调仓方案仅在「有新组合调仓推送」时针对该批信号生成 |
| **挖组合** | 从雪球社交/自选等渠道扩展候选 ZH，预览、筛选、入库 |
| **数据同步** | 一键全量：调仓、后复权行情、官方净值 |
| **组合对比** | 多组合净值曲线同屏对比 |
| **抄作业回测** | 多策略 catalog（含「信念分级·师傅信用」等），合并跟单回测 |
| **每日 Digest** | 独立模块：关注组合调仓巡检 → HTML 渲染图片 → 阿里云 OSS → 钉钉推送（见 [daily_digest/README.md](daily_digest/README.md)） |

---

## 快速开始

### 1. 数据库

MySQL 库名建议 `portfolio`：

```bash
mysql -u root -p portfolio < sql/schema.sql
```

表会在后端启动时自动补齐迁移（如 `mined_cubes`、个人持仓 `personal_*` 等）。

### 2. 后端配置

```bash
cd backend
copy .env.example .env
pip install -r requirements.txt
```

`.env` 最少需要：

```bash
ACCOUNT_DASHBOARD_DATABASE_URL=mysql+pymysql://用户:密码@127.0.0.1:3306/portfolio?charset=utf8mb4
ACCOUNT_DASHBOARD_PORT=8011
```

可选：`TUSHARE_API_KEY`、`DINGTALK_WEBHOOK`、`DEEPSEEK_API_KEY`、OSS 图床变量（Digest 用）。

### 3. 雪球 Cookie

```bash
cd backend
pip install -e ".[login]"              # 仅本地登录需要 DrissionPage
python ../scripts/xueqiu_login.py      # 扫码 → data/xueqiu_cookie.txt
```

Cookie 过期后重新登录并更新 `data/xueqiu_cookie.txt`（已在 `.gitignore`，勿提交）。

### 4. 启动

```bash
# 终端 1
cd backend && python main.py

# 终端 2
cd frontend && npm install && npm run dev
```

- 前端：<http://localhost:5176>（Vite 代理 `/api` → 8011）
- 健康检查：<http://127.0.0.1:8011/health>

---

## 目录结构

```
xueqiu/
├── README.md
├── backend/                 # FastAPI 服务
│   ├── main.py
│   ├── requirements.txt
│   └── xueqiu/
│       ├── api/             # HTTP 路由
│       ├── domain/          # 净值、回测、挖组合、个人持仓
│       ├── storage/         # SQLAlchemy 表
│       ├── integrations/    # 雪球 / 新浪行情
│       └── sync/            # 批处理同步
├── frontend/                # React 看板
├── daily_digest/            # 每日钉钉简报（可独立 GHA 部署）
├── scripts/                 # 命令行运维（见 scripts/README.md）
├── sql/                     # 初始 schema
├── docs/                    # 策略/部署说明（供深入阅读）
└── data/
    └── xueqiu_cookie.txt    # 本地 Cookie（gitignore）
```

**不会提交到 Git 的生成物**（见 `.gitignore`）：`digest_output/`、`*__pycache__*`、`scripts/backtest_output/`、`frontend/dist/`、`.env`、Cookie 等。

---

## 常用运维

在项目根目录：

```bash
cd backend

python ../scripts/sync_quotes.py          # 后复权行情
python ../scripts/sync_cube_nav.py        # 官方净值
python ../scripts/xueqiu_monitor.py       # 调仓巡检 + 钉钉（旧版单脚本）
python ../scripts/backtest_copy_portfolios.py   # CLI 回测
```

前端「数据同步」页可替代大部分手动同步。每日简报推荐走 `daily_digest/daily_portfolio_digest.py` 或 GitHub Actions。

---

## API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 组合账户列表 |
| GET | `/api/dashboard/{code}` | 单组合看板 |
| GET | `/api/portfolios/overview-stats` | 总览统计 |
| POST | `/api/sync-xueqiu-all` | 全量抓取调仓 |
| POST | `/api/sync-quotes` | 同步行情 |
| GET | `/api/discovery/cubes` | 挖组合候选列表 |
| POST | `/api/backtest-copy` | 抄作业回测 |
| GET | `/api/personal-account` | 我的持仓 |
| POST | `/api/personal-account/trade` | 记录买卖 |

完整路由见 `backend/xueqiu/api/main.py`。

---

## 测试

单元测试在 `backend/tests/`，不含临时脚本：

```bash
cd backend
python -m pytest tests/ -q
```

---

## 延伸阅读

| 文档 | 内容 |
|------|------|
| [scripts/README.md](scripts/README.md) | 各运维脚本说明 |
| [daily_digest/README.md](daily_digest/README.md) | 钉钉简报、OSS 图床、GHA |
| [docs/DIGEST_GITHUB_SETUP.md](docs/DIGEST_GITHUB_SETUP.md) | Actions 部署 |
| [docs/conviction_copy_strategy_brief_for_ai.md](docs/conviction_copy_strategy_brief_for_ai.md) | 主推抄作业策略设计说明 |

---

## 说明

本项目为个人研究/自用工具，**不构成投资建议**。雪球 API 非官方公开接口，请控制请求频率并自行承担使用风险。

License: 见 [LICENSE](LICENSE)
