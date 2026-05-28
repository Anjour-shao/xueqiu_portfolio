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

## GitHub Actions（推荐首选）

Workflow：`.github/workflows/daily_digest.yml`

- 定时：每天 **21:00**（`Asia/Shanghai`，晚上 9 点）
- 手动：仓库 **Actions** → **Daily Portfolio Digest** → **Run workflow**

### Secrets（Settings → Secrets and variables → Actions）

| Name | 说明 |
|------|------|
| `XUEQIU_COOKIE` | 浏览器完整 Cookie（含 `xq_a_token`） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DINGTALK_WEBHOOK` | 钉钉机器人 Webhook |
| `DEEPSEEK_BASE_URL` | 可选，默认 `https://api.deepseek.com` |

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
