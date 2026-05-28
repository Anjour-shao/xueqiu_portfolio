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
python daily_portfolio_digest.py
```

状态文件 `daily_digest_state.json`（v2）结构示例：

```json
{
  "version": 2,
  "last_digest_at": "2026-05-28 20:00:00",
  "portfolios": {
    "ZH1350829": { "last_notified_rebalance_time": "2026-05-18 14:08:02" }
  },
  "holdings": {
    "SH600519": {
      "last_price": 1900.5,
      "unrealized_pnl_pct": 11.2,
      "updated_at": "2026-05-28 20:00:00"
    }
  }
}
```

- **每晚 8 点**跑一次；只推送「自 `last_notified_rebalance_time` 以来」的调仓批次
- 同一天组合调仓多次：当晚合并推送，按时间列出各批（非实时跟单）
- 评论始终拉**最新**约 50 条，与模拟日期无关
- 测试：设 `TEST_SIMULATE_NOW = "2026-05-18 20:00:00"`，测完改回 `None`；可把 `last_notified` 调早或删 state 以触发推送

勿提交 state 到 Git。

## GitHub Actions

Workflow：`.github/workflows/daily_digest.yml`

- **手动**：仓库 **Actions** → **Daily Portfolio Digest** → **Run workflow**
- **自动（推荐）**：本机 **Windows 计划任务** 每天 21:00 调 API 触发（见下方「schedule 不可用时的做法」）
- **GHA 内置 schedule**：仓库若从未出现 **Scheduled** 运行，不要依赖 `cron`（见下方说明）

### Secrets（Settings → Secrets and variables → Actions）

| Name | 说明 |
|------|------|
| `XUEQIU_COOKIE` | 浏览器完整 Cookie（含 `xq_a_token`） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DINGTALK_WEBHOOK` | 钉钉机器人 Webhook |
| `DEEPSEEK_BASE_URL` | 可选，默认 `https://api.deepseek.com` |

### 若 schedule 从不触发（只能手动 Run workflow）

GitHub 对**私有库**可能**不跑 schedule**（工作流被暂停、账户/组织策略、或整点队列被丢弃）。表现：Smoke Test 推送后 15+ 分钟仍无 **Scheduled** 记录。

**请逐项检查（浏览器）：**

1. 打开  
   `https://github.com/Anjour-shao/xueqiu_portfolio/actions/workflows/schedule_smoke_test.yml`  
   - 顶部黄色条 **「此计划任务工作流已被禁用」** → 点 **Enable workflow**
2. 左侧 **Daily Portfolio Digest** → 右侧 `⋯` → 必须是 **Enable workflow**（不能是灰色 Disable）
3. **Settings** → **Actions** → **General** → **Actions permissions** 选 **Allow all actions**

**Schedule Smoke Test**（`.github/workflows/schedule_smoke_test.yml`）：

- `push` 到 main 后会立刻有一条 **push** 触发（证明 workflow 在仓库里）
- `schedule` 在 **3/13/23/33/43/53 分** 尝试触发；若仍无 **Scheduled**，可认定平台 schedule 对你仓库不可用

**可靠替代：本机/外部定时调 API（推荐）**

不依赖 GitHub schedule，用 PAT 调 `workflow_dispatch`：

```powershell
$env:GITHUB_TOKEN = "ghp_你的token"
.\scripts\trigger_github_workflow.ps1
```

Windows **任务计划程序**：每天 **21:00** 运行上述脚本。  
Digest 已支持 `repository_dispatch`（`run_digest`），与 API 触发等效。

验证完请 **删除** `schedule_smoke_test.yml`，避免占 Actions 分钟数。

### schedule 不可用时的做法（已确认 push 能跑、Scheduled 从不出现时）

说明：**脚本在 GitHub 上能跑**，只是 **GitHub 不会按 cron 自动触发**。改公开库也**不一定**解决，需先在 Actions 里 Enable workflow；若仍无 Scheduled，用下面方案。

**一次性准备**

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Generate new token (classic)**  
   - 勾选 **repo**（或 Fine-grained：该仓库 **Actions: Read and write**）
2. Windows：**设置** → **系统** → **关于** → **高级系统设置** → **环境变量** → 用户变量 **新建**  
   - 名称 `GITHUB_TOKEN`，值 `ghp_...`

**注册每天 21:00 自动触发（推荐）**

```powershell
cd C:\Users\邵俊杰\Desktop\web\xueqiu
.\scripts\install_digest_scheduled_task.ps1
# 立即试跑：
Start-ScheduledTask -TaskName XueqiuDailyDigest
```

然后打开 [Actions](https://github.com/Anjour-shao/xueqiu_portfolio/actions)，应出现新的 **Daily Portfolio Digest**（由 API 触发，事件可能显示为 `workflow_dispatch`）。

仅单次测试、不装计划任务时：

```powershell
$env:GITHUB_TOKEN = "ghp_..."
.\scripts\trigger_github_workflow.ps1
```

### 私有库 vs 公开库（schedule）

| | 私有库 | 公开库 |
|---|--------|--------|
| 能否用 `schedule` | 可以 | 可以 |
| 常见拦路虎 | Actions 里 **定时工作流被禁用**、组织策略 | 同上；另 **60 天无提交** 会暂停公开库的 schedule |
| 与手动 Run 关系 | 手动能跑 ≠ schedule 已启用 | 相同 |

**改公开库通常不能自动修好 schedule**，仍需在 Actions 里 **Enable workflow**。公开的主要差别是部分账户/组织策略更宽松，但不是万能药。

**改公开前必读：** [`daily_portfolio_digest.py`](../daily_portfolio_digest.py) 里硬编码了 **持仓、成本、总资产、关注组合**（已在 Git 历史里）。公开后全世界可见；Secrets（Cookie、钉钉）仍在 GitHub Secrets 里，不会随仓库公开。若要坚持公开，应先把持仓/账户迁到私有配置或环境变量，并避免把真实数据写进仓库。

### 验证雪球在海外节点是否可用

1. 配置好 Secrets 后，手动触发 **workflow_dispatch**。
2. 查看 **Run daily digest** 步骤日志：
   - 成功：出现 `获取 N 条近期讨论`、无 `返回 HTML` / `400016`。
   - 失败：日志含 `返回 HTML 而非 JSON`、`认证失败`、`HTTP 429` 等。

若持续失败，说明 GitHub Runner 海外 IP 被雪球拦截，请改用下方国内云函数，**无需改业务脚本**。

### 讨论区与组合调仓不是同一个域名

- 组合调仓：`https://xueqiu.com/cubes/rebalancing/history.json`
- 个股讨论：`https://api.xueqiu.com/query/v1/symbol/search/status`（主站同路径会触发 WAF 返回 HTML）

因此可能出现「调仓成功、舆情失败」——不一定是 Cookie 失效。

### 状态持久化

使用 `actions/cache` 保存 `daily_digest_state.json`。首次运行所有组合会视为「有变化」并可能触发 AI；之后仅在实际调仓时间变化时分析。

## 备选：腾讯云 SCF / 阿里云 FC

当 GitHub Actions 无法稳定访问雪球时使用。

### 打包内容（最小集）

- `daily_portfolio_digest.py`
- `daily_digest_requirements.txt`
- `backend/xueqiu/integrations/`（整个目录）
- `backend/xueqiu/domain/codes.py`

### 环境变量

与 GitHub Secrets 相同。

### 定时触发器

- 腾讯云 SCF：`0 0 8 * * * *`（cron 表达式，时区选 **Asia/Shanghai**）
- 阿里云 FC：每天 8:00 北京时间

### 状态文件

云函数实例无持久磁盘时，可选：

1. 将 `daily_digest_state.json` 存到对象存储（COS/OSS），每次读写；
2. 或使用云数据库一行 JSON；
3. 或依赖 SCF 实例复用时的 `/tmp`（不可靠，仅作测试）。

### 入口

处理函数内执行：

```bash
python daily_portfolio_digest.py
```

超时建议 ≥ 300 秒（多组合 + DeepSeek 可能较慢）。
