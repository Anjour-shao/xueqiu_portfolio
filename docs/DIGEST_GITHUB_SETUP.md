# 每日 Digest 上线清单

## 一、GitHub Secrets（必须齐全）

仓库 → **Settings** → **Secrets and variables** → **Actions**

| Secret | 说明 |
|--------|------|
| `XUEQIU_COOKIE` | 雪球 Cookie |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `DEEPSEEK_BASE_URL` | 建议 `https://api.deepseek.com` |
| `DINGTALK_WEBHOOK` | 钉钉 Webhook |
| `DINGTALK_KEYWORD` | `组合` |
| `IMG_BB_API_KEY` | 图床 Key（钉钉显示 PNG 必需） |

## 二、本地 state 已初始化

`daily_digest_state.json` 已通过 `--init-state` 同步过各组合最新调仓时间。  
该文件 **不要提交**（已在 `.gitignore`），GHA 用 Actions Cache 持久化。

## 三、推送代码（Cursor 图形化）

1. 左侧 **源代码管理**（`Ctrl+Shift+G`）
2. 在「更改」里勾选要提交的文件（**不要**勾选 `.env`、`xueqiu_cookie.txt`、`daily_digest_state.json`）
3. 上方输入提交说明，例如：`Digest 图片推送与项目整理`
4. 点 **提交**
5. 点 **同步更改**（或 **推送**）推到 GitHub

## 四、立即跑一次（八点前验证）

1. 浏览器打开 GitHub 仓库 → **Actions**
2. 左侧选 **Daily Portfolio Digest**
3. 右侧 **Run workflow** → **Run workflow**
4. 等约 3～8 分钟，日志应出现 `钉钉图片消息推送成功` 或 `errcode=0`
5. 钉钉群收到持仓简报图

## 五、定时（每天晚上 21:00 北京，即晚上 9 点）

- `timezone: Asia/Shanghai`，`cron: "0 21 * * *"`

### 一般会晚多久？

| 情况 | 说明 |
|------|------|
| **多数** | 准点～**15 分钟内** 开始跑 |
| **偶尔** | **30～60 分钟**（GitHub 负载高，尤其整点） |
| **很少** | **当天漏跑**（需第二天或手动 Run workflow） |

GitHub 官方：定时是 **best-effort**，不保证准点。  
**21:00** 设在整点，可能略慢；若常延迟可改成 **21:07**。

### 怎么确认跑没跑？

Actions 里出现 **Scheduled**（不是 Manually run），绿色勾 = 跑完。

## 六、本地预览调仓排版

Cursor → **运行和调试** → **预览：真实最新调仓** → F5  
结果在 `digest_output/`（不提交）。

## 七、改持仓 / 组合

编辑 `daily_portfolio_digest.py` 顶部 `MY_HOLDINGS`、`WATCH_PORTFOLIOS`、`MY_ACCOUNT`，提交推送即可。
