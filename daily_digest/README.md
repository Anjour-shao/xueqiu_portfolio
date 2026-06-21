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

1. 在 `daily_portfolio_digest.py` 顶部填写 `WATCH_PORTFOLIOS`；个人持仓优先从数据库读（见前端「我的持仓」），否则回退脚本内 `MY_HOLDINGS`。
2. 在 `backend/.env` 填写（与主项目共用 `xueqiu.config`）：
   - `DINGTALK_WEBHOOK`
   - `DEEPSEEK_API_KEY`
   - `DEEPSEEK_BASE_URL`（可选）
   - OSS 图床变量（推荐，见下表）
   - 雪球 Cookie：**本地**用 `data/xueqiu_cookie.txt`；**GHA** 用 Secret `XUEQIU_COOKIE`（见下文）
3. 安装依赖：

```bash
pip install -r daily_digest/requirements.txt -r backend/requirements.txt
playwright install --with-deps chromium
```

状态文件 **`daily_digest/daily_digest_state.json`**（v3，已 gitignore）记录各组合「上次已推送的调仓时间」。

### 基准 state 文件在哪

| 环境 | 路径 |
|------|------|
| **本地** | `daily_digest/daily_digest_state.json` |
| **GitHub Actions** | 同路径，由 Actions Cache `daily-digest-state-v5` 持久化（不在仓库里） |

可复制示例再改：

```bash
cp daily_digest/daily_digest_state.example.json daily_digest/daily_digest_state.json
```

### 个人持仓与 GHA 抄作业（无需手动 sync）

- 在前端 **「我的持仓」** 改现金 / 买卖 / 策略 → 后端**自动**写入 `daily_digest/holdings_snapshot.json` 并上传 **OSS**（`digest/holdings_snapshot.json`）
- **GitHub Actions** 每晚从 OSS 拉快照算「抄作业建议仓位」，**不用 commit、不用云 MySQL**
- 本地跑 Digest 时优先读 MySQL，读不到则用 OSS/本地快照

首次部署或 OSS 为空时可手动补一次：

```bash
python scripts/sync_digest_holdings.py
```

`daily_digest/daily_portfolio_digest.py` 里的 `MY_HOLDINGS` 仅作兜底，不必再手改。

核心字段是各组合的 `last_notified_rebalance_time`——Digest 只推送**晚于该时间**、且在**回溯窗口内**的新调仓：

```json
{
  "version": 3,
  "portfolios": {
    "ZH3393223": {
      "last_notified_rebalance_time": "2026-02-25 00:00:00"
    }
  }
}
```

**测试步骤（本地）：**

1. 把目标组合的 `last_notified_rebalance_time` 改到**你想重推的那批调仓之前**（如该组合 2/26 调仓，就改成 `2026-02-25 00:00:00`）
2. 设置回溯天数（默认 2 天，测旧调仓需放大）：

   PowerShell:
   ```powershell
   $env:DIGEST_LOOKBACK_DAYS=365
   python daily_digest/daily_portfolio_digest.py
   ```
3. 跑完应推送并自动把 `last_notified` 更新到最新已推批次

**GHA 测试：**

- Run workflow 时填 **lookback_days** = `365`（workflow 输入框）
- 若要重置云端 state：Actions → **Caches** → 删除 `daily-digest-state-v5`，再跑一轮（未推送过的组合会走「首次巡检建基准」）
- 或本地改好 `daily_digest_state.json` 后，暂时 bump workflow 里 cache key 让它用你提交的文件（一般不推荐提交真实 state）

**不改 state 的快速预览**（不写 state、不依赖基准）：

```bash
pip install -r daily_digest/requirements.txt -r backend/requirements.txt
playwright install chromium
python daily_digest/preview_latest_rebalance.py --portfolio ZH3393223 --push
```

`--push` 可选，会真发钉钉；默认只生成本地 PNG 到 `daily_digest/digest_output/`。

### 常用命令

```bash
# 首次部署：把所有组合基准同步到「当前最新调仓」，不推送
python daily_digest/daily_portfolio_digest.py --init-state

# 正常每晚巡检
python daily_digest/daily_portfolio_digest.py
```

状态文件 `daily_digest/daily_digest_state.json`（v3）勿提交 Git。

### 本地预览调仓排版

```bash
python daily_digest/preview_latest_rebalance.py --portfolio ZH3393223
python daily_digest/preview_latest_rebalance.py --portfolio ZH3393223 --push
```

预览图输出到 `daily_digest/digest_output/`（不提交）。详见上文「手动改基准做测试」。

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
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 阿里云 OSS 图床（推荐） |
| `OSS_ENDPOINT` / `OSS_BUCKET_NAME` / `OSS_CUSTOM_DOMAIN` | OSS 桶与访问域名 |
| `IMG_BB_API_KEY` | 图床备选（OSS 不可用时回退） |
| `ACCOUNT_DASHBOARD_DATABASE_URL` | 可选；仅本地/自建服务器有 MySQL 时需要；**GHA 用 OSS 持仓快照，不必配** |
| `DIGEST_LOOKBACK_DAYS` | 可选；调仓回溯日历天数，默认 `2`；测试旧调仓可设 `365` |

### Cookie 更新（本地 vs Actions）

| 运行环境 | Cookie 存放位置 | 更新步骤 |
|----------|-----------------|----------|
| **本地** | `data/xueqiu_cookie.txt` 或 `backend/.env` 的 `XUEQIU_COOKIE` | `python scripts/xueqiu_login.py` → 覆盖文件 → 重启后端（如在跑） |
| **GitHub Actions** | 仓库 Secret **`XUEQIU_COOKIE`** | 复制 `xueqiu_cookie.txt` 全文 → Settings → Secrets → 编辑保存 → 手动 Run workflow |

加载优先级：`XUEQIU_COOKIE` 环境变量 **高于** `data/xueqiu_cookie.txt`。

Cookie 失效时简报仍会推送，标题为 **「Cookie 过期 · 请更新」**，正文含上述步骤。

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
| 图片失败 | 检查 OSS 配置或 `IMG_BB_API_KEY` 回退，或 `--push-markdown` |
| `返回 HTML` / `400016` | Cookie 过期：本地更新 `data/xueqiu_cookie.txt`；GHA 更新 Secret `XUEQIU_COOKIE` |
| 钉钉收到「Cookie 过期 · 请更新」 | 同上，更新后手动 Run workflow 验证 |
| 调仓成功、评论失败 | 讨论区 API 域名不同，可忽略 |

## 改持仓 / 关注组合

编辑 `daily_portfolio_digest.py` 顶部 `MY_HOLDINGS`、`WATCH_PORTFOLIOS`，提交推送即可。
