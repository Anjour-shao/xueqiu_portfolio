# 每日组合 Digest 部署说明

## 本地运行

1. 在 `daily_portfolio_digest.py` 顶部填写 `WATCH_PORTFOLIOS`、`MY_HOLDINGS`。
2. 在 `backend/.env` 填写（脚本与主项目共用 `xueqiu.config`）：
   - `DINGTALK_WEBHOOK`
   - `DEEPSEEK_API_KEY`
   - `DEEPSEEK_BASE_URL`（可选）
   - 雪球 Cookie：`data/xueqiu_cookie.txt` 或环境变量 `XUEQIU_COOKIE`
3. 安装依赖并执行：

```bash
pip install -r daily_digest_requirements.txt
playwright install --with-deps chromium
python daily_portfolio_digest.py
```

状态文件 `daily_digest_state.json`（v3）勿提交 Git（已在 `.gitignore`）。

---

## 自动跑 Digest：推荐方案（免费、不用本机、不用买云服务器）

**思路**：Digest 仍在 **GitHub Actions** 里跑（你已验证手动 Run 可用）；**不用** GHA 内置 `schedule`（对你仓库不触发）；用 **cron-job.org** 等免费服务，每天定时 `POST` GitHub API 触发 `workflow_dispatch`。

| 对比 | cron-job.org + GHA | 轻量云 VPS | 本机计划任务 |
|------|-------------------|------------|--------------|
| 费用 | 免费档通常够用 | 约 30～80 元/月 | 0 |
| 本机要开机 | 否 | 否 | 是 |
| 运维 | 极低 | 中 | 低 |

### 1. GitHub PAT（一次性）

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Generate new token (classic)**
2. 勾选 **repo**（或 Fine-grained：仓库 `xueqiu_portfolio` → **Actions: Read and write**）
3. 复制 `ghp_...`，**只填在 cron-job.org**，不要写进仓库

### 2. cron-job.org 配置

1. 注册 [cron-job.org](https://cron-job.org)（免费）
2. **Create cronjob** → 类型选能发 **HTTP POST** 的（或 Advanced → Custom request）
3. 填写：

| 项 | 值 |
|----|-----|
| URL | `https://api.github.com/repos/Anjour-shao/xueqiu_portfolio/actions/workflows/daily_digest.yml/dispatches` |
| Method | `POST` |
| Schedule | 每天 **21:00**，时区 **Asia/Shanghai** |
| Body | `{"ref":"main"}` |
| Content-Type | `application/json` |

4. **Headers**（每条单独添加）：

```
Authorization: Bearer ghp_你的token
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

5. 保存后点 **Test run**，再到仓库 [Actions](https://github.com/Anjour-shao/xueqiu_portfolio/actions) 看是否出现新的 **Daily Portfolio Digest**（事件为 `workflow_dispatch`）。

### 3. 本地单次测试（可选）

```powershell
$env:GITHUB_TOKEN = "ghp_..."
.\scripts\trigger_github_workflow.ps1
```

---

## GitHub Actions（执行环境）

Workflow：[`.github/workflows/daily_digest.yml`](../.github/workflows/daily_digest.yml)

- **触发**：手动 Run、`repository_dispatch`、或上方 **cron-job.org** 调 API
- **不要依赖** `on.schedule`（对你仓库未生效）
- **状态**：`actions/cache` 自动保存 `daily_digest_state.json`（作业结束且文件存在时写入，无需单独 save 步骤）

### Secrets（Settings → Secrets and variables → Actions）

| Name | 说明 |
|------|------|
| `XUEQIU_COOKIE` | 浏览器完整 Cookie（含 `xq_a_token`） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DINGTALK_WEBHOOK` | 钉钉机器人 Webhook |
| `DINGTALK_KEYWORD` | 钉钉安全设置关键词（若有） |
| `IMG_BB_API_KEY` | 图床 Key（图片推送需要） |
| `DEEPSEEK_BASE_URL` | 可选 |

### 失败排查

| 日志现象 | 处理 |
|----------|------|
| `ModuleNotFoundError: digest.state_store` | 已修复；拉最新 `main` |
| `Cache save failed` / path 不存在 | 多为 Digest 中途崩溃未写 state；看 **Run daily digest** 真正报错；已改为 `finally` 尽量保存 state |
| Playwright / chromium 报错 | workflow 已用 `playwright install --with-deps chromium` |
| 图片失败 | 日志会回退 Markdown；或 Secrets 补全 `IMG_BB_API_KEY` |
| `返回 HTML` / `400016` | Cookie 过期，本地 `xueqiu_login.py` 后更新 Secret `XUEQIU_COOKIE` |

讨论区 API 走 `api.xueqiu.com`，与组合调仓域名不同；可能出现「调仓成功、评论失败」。

---

## 备选：极轻量云服务器（仅当不想用 cron-job.org）

若不想用第三方 cron 网站，可买 **最轻量** 实例（1 核 1G，按量/包月最低价），**只跑一条 cron**，无需高配：

```bash
# Ubuntu，每晚 21:00
0 21 * * * cd /opt/xueqiu && /usr/bin/python3 daily_portfolio_digest.py >> /var/log/xueqiu-digest.log 2>&1
```

需自行上传 `backend/.env`、`data/xueqiu_cookie.txt`（勿进 Git）。与 GHA 的 state **不共享**，勿双线同时自动跑。

---

## 备选：腾讯云 SCF / 阿里云 FC

海外 GHA 访问雪球不稳定时再考虑；部署见下文历史说明，运维高于 cron-job.org。

### 打包最小集

- `daily_portfolio_digest.py`
- `daily_digest_requirements.txt`
- `backend/xueqiu/integrations/`、`backend/xueqiu/domain/codes.py`、`backend/xueqiu/config.py`
- `digest/`

环境变量与 GitHub Secrets 相同；超时建议 ≥ 300 秒。

---

## 附录：本机计划任务（可选）

仅当不想用 cron-job.org、也不想买服务器时：

```powershell
$env:GITHUB_TOKEN = "ghp_..."
.\scripts\install_digest_scheduled_task.ps1
```

或直接本机跑 Python：`python daily_portfolio_digest.py`（需每晚开机）。
