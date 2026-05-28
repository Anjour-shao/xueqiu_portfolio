# 每日组合 Digest — GitHub Actions 部署指南

## 一、仓库里已准备好的文件

| 文件 | 作用 |
|------|------|
| [daily_portfolio_digest.py](../daily_portfolio_digest.py) | 主脚本：12 个关注组合 + 股票账户持仓 |
| [daily_digest_requirements.txt](../daily_digest_requirements.txt) | Python 依赖 |
| [.github/workflows/daily_digest.yml](../.github/workflows/daily_digest.yml) | 每天北京时间 8:00 定时 + 手动触发 |
| [daily_digest_state.json](../daily_digest_state.json) | 本地 state（勿提交敏感信息；已在 .gitignore） |

## 二、本地先跑通（推荐顺序）

### 1. 配置 `backend/.env`

```env
DINGTALK_WEBHOOK=你的机器人Webhook
DINGTALK_KEYWORD=组合
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

雪球 Cookie：使用 [data/xueqiu_cookie.txt](../data/xueqiu_cookie.txt)（运行 `scripts/xueqiu_login.py` 生成）。

### 2. 单股测试（DeepSeek + 钉钉）

```bash
pip install -r daily_digest_requirements.txt
python daily_portfolio_digest.py --test-one
```

终端应出现 `>>> 调用 DeepSeek` 与 `钉钉消息推送成功（errcode=0）`。

### 3. 初始化组合 state（避免首次推送历史 10 批调仓）

```bash
python daily_portfolio_digest.py --init-state
```

会把 12 个组合**当前最新调仓时间**写入 `daily_digest_state.json`，之后每晚只报**新调仓**。

### 4. 完整试跑

```bash
python daily_portfolio_digest.py
```

## 三、推送到 GitHub

```bash
git add daily_portfolio_digest.py daily_digest_requirements.txt .github/workflows/daily_digest.yml docs/
git commit -m "Add daily portfolio digest workflow"
git push origin main
```

（`backend/.env`、`data/xueqiu_cookie.txt`、`daily_digest_state.json` **不要**提交。）

## 四、配置 GitHub Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 名称 | 内容 |
|-------------|------|
| `XUEQIU_COOKIE` | 浏览器复制完整 Cookie（含 `xq_a_token`） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 可选，默认 `https://api.deepseek.com` |
| `DINGTALK_WEBHOOK` | 钉钉机器人 Webhook 完整 URL |
| `DINGTALK_KEYWORD` | `组合`（与机器人安全设置一致） |

## 五、首次在云端运行

1. 打开 **Actions** → **Daily Portfolio Digest** → **Run workflow**。
2. 查看日志：持仓行情、组合巡检、DeepSeek、钉钉 `errcode=0`。
3. **首次**若未在本地执行 `--init-state`，可能推送较多历史调仓；可在本地跑 `--init-state` 后把生成的 `daily_digest_state.json` 交给 cache（或再手动 Run 一次后 cache 会自动带上）。

State 通过 Actions Cache（key: `daily-digest-state-v3`）在每日运行间保留。

## 六、定时说明

- Cron：`0 0 * * *`（UTC）≈ 北京时间 **8:00**
- 电脑关机不影响；由 GitHub 云端执行

## 七、持仓与盈亏逻辑

- **当日涨跌**：新浪现价 vs 昨收，按股数汇总为账户当日盈亏
- **持有盈亏**：配置中的 `cost_price` × `shares`，与雪球账本一致
- **后复权**：新浪后复权价换算成本口径，在推送中标注「后复权盈亏%」
- **总资产**：`MY_ACCOUNT.total_assets`（59092.58）与市值差额视为现金

修改持仓请编辑 `daily_portfolio_digest.py` 顶部 `MY_HOLDINGS` / `MY_ACCOUNT`。

## 八、若雪球在 GitHub 海外节点失败

日志出现 `返回 HTML` / 频繁失败时，将同一份脚本部署到**国内云函数**（见 [daily_digest_deploy.md](./daily_digest_deploy.md)），Secrets 同名即可。
