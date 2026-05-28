# 每日组合 Digest（云端模块）

每晚推送关注组合调仓 + 个人持仓行情（HTML → PNG → 钉钉）。在 **GitHub Actions** 运行，与看板主应用分离；复用 `backend/xueqiu` 的雪球 API 与配置。

## 目录

```
daily_digest/
├── daily_portfolio_digest.py   # 主入口（GHA 调用）
├── requirements.txt
├── digest/                     # HTML 渲染与钉钉图片推送
├── preview_latest_rebalance.py # 本地预览排版（不写 state）
├── trigger_github_workflow.ps1 # 手动/定时触发 GHA
├── trigger_github_workflow.sh
└── install_digest_scheduled_task.ps1  # Windows 计划任务
```

## 本地运行

1. 在 `daily_portfolio_digest.py` 顶部填写 `WATCH_PORTFOLIOS`、`MY_HOLDINGS`。
2. 在 `backend/.env` 填写（与主项目共用 `xueqiu.config`）：
   - `DINGTALK_WEBHOOK`
   - `DEEPSEEK_API_KEY`
   - `DEEPSEEK_BASE_URL`（可选）
   - 雪球 Cookie：`data/xueqiu_cookie.txt` 或环境变量 `XUEQIU_COOKIE`
3. 安装依赖并执行：

```bash
pip install -r daily_digest/requirements.txt
playwright install --with-deps chromium
python daily_digest/daily_portfolio_digest.py
```

状态文件 `daily_digest/daily_digest_state.json`（v3）勿提交 Git。

### 本地预览调仓排版

```bash
cd daily_digest
python preview_latest_rebalance.py
```

预览图输出到 `daily_digest/digest_output/`（不提交）。

## GitHub Actions

Workflow：[`.github/workflows/daily_digest.yml`](../.github/workflows/daily_digest.yml)

- **触发**：手动 Run、`repository_dispatch`、或外部 cron 调 API（见下文）
- **状态**：Actions Cache 持久化 `daily_digest/daily_digest_state.json`

### Secrets（Settings → Secrets and variables → Actions）

| Secret | 说明 |
|--------|------|
| `XUEQIU_COOKIE` | 雪球 Cookie |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `DEEPSEEK_BASE_URL` | 建议 `https://api.deepseek.com` |
| `DINGTALK_WEBHOOK` | 钉钉 Webhook |
| `DINGTALK_KEYWORD` | 钉钉安全关键词（如 `组合`） |
| `IMG_BB_API_KEY` | 图床 Key（钉钉 PNG 必需） |

### 立即验证

1. GitHub 仓库 → **Actions** → **Daily Portfolio Digest**
2. **Run workflow**
3. 日志应出现 `钉钉图片消息推送成功` 或 `errcode=0`

### 定时触发（推荐 cron-job.org）

不依赖 GHA 内置 `schedule` 时，可用免费 cron 服务每天 **21:00**（Asia/Shanghai）POST：

```
https://api.github.com/repos/Anjour-shao/xueqiu_portfolio/actions/workflows/daily_digest.yml/dispatches
```

Body：`{"ref":"main"}`  
Headers：`Authorization: Bearer ghp_...`、`Accept: application/vnd.github+json`、`X-GitHub-Api-Version: 2022-11-28`

### 本地触发 GHA（可选）

```powershell
$env:GITHUB_TOKEN = "ghp_..."
.\daily_digest\trigger_github_workflow.ps1
```

```bash
export GITHUB_TOKEN=ghp_...
./daily_digest/trigger_github_workflow.sh
```

### Windows 计划任务（可选）

```powershell
$env:GITHUB_TOKEN = "ghp_..."
.\daily_digest\install_digest_scheduled_task.ps1
```

## 失败排查

| 日志现象 | 处理 |
|----------|------|
| Playwright / chromium 报错 | workflow 已 `playwright install --with-deps chromium` |
| 图片失败 | 补全 `IMG_BB_API_KEY`，或回退 Markdown |
| `返回 HTML` / `400016` | Cookie 过期，本地 `scripts/xueqiu_login.py` 后更新 Secret |
| 调仓成功、评论失败 | 讨论区 API 域名不同，可忽略 |

## 改持仓 / 关注组合

编辑 `daily_portfolio_digest.py` 顶部 `MY_HOLDINGS`、`WATCH_PORTFOLIOS`，提交推送即可。
