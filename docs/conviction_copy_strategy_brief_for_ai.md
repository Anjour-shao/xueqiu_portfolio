# 雪球「抄作业」信念分级·师傅信用策略 — 完整说明（供外部 AI 评审）

> 本文档描述 xueqiu 项目中 **route_g_conviction_trust（信念分级·师傅信用）** 抄作业回测策略的完整设计、关键代码、已知缺陷与用户观察到的 -1.98% 现象。  
> 项目：FastAPI 后端 + React 前端，数据来自本地 MySQL 中多个 ZH 组合的调仓记录。

---

## 0. 请评审者回答的核心问题

1. **max_positions=8** 与 **9 个师傅合并跟单、单票目标 15%~40%** 是否本质上不兼容？
2. **腾位卖出**（为开新仓卖掉浮盈最差票）是否应与「跟师傅重仓」哲学冲突？应如何改？
3. 大量 **-1.98%** 的「股票累计收益」是计算 bug 还是策略结构性摩擦？如何量化对总收益的伤害？
4. **20% 重仓门槛** 是否会错过师傅「小仓试盘→逐步加码」的路径？
5. **师傅信用** 基于师傅自身 leg 胜率，而非我们跟单 leg 胜率，是否合理？
6. 与对照策略 **分仓模仿·20%** 相比，本策略的超额在扣除 friction 后是否仍成立？

---

## 1. 业务背景

用户跟踪多个雪球 ZH 组合（「师傅」），希望模拟 **抄作业**：合并全部师傅的调仓信号，在单一账户里回测跟单效果。

当前 catalog 中的 **主推策略** 是：

| 字段 | 值 |
|------|-----|
| strategy_id | `route_g_conviction_trust` |
| 显示名 | 信念分级·师傅信用 |
| 描述 | ≥20% 才跟加仓；试探 15% / 信念 30% / 强信念 38%；共识加成；师傅重仓胜率调上限 |

**设计意图（设计者自述）**：
- 不抄师傅全部持仓（师傅常有 20+ 只含大量 3%~10% 轻仓）
- 只跟师傅 **真正下重注**（目标仓位 ≥ 20%）的信号
- 多师傅共识时加码；师傅历史表现差时减码
- 集中持仓（最多 8 只），单票可至 40% NAV

**用户观察到的异常**：
- 最终持仓约 **8 只**，其他对照策略约 **20+ 只**（符合 max_positions=8 设计）
- 回测结果「股票」Tab 出现 **大量 -1.98%**，平仓=1、胜率=0% 的「过客」股票极多
- 用户怀疑策略有 **结构性缺陷**，而非单纯 UI 问题

---

## 2. 数据来源与回测全局假设

### 2.1 输入

- `load_portfolio_trades()`：从 DB 读取 **所有已配置 ZH 组合** 的全部调仓记录，按 `trade_time` + `id` 排序
- 每条记录：`account_code, stock_name, ts_code, from_weight, to_weight, price, trade_time`
- 同一 `trade_time` 的多条记录视为 **一批（batch）** 同时处理

### 2.2 摩擦与规则（全局，所有抄作业策略共用）

文件：`backend/xueqiu/domain/copy_backtest.py`

```python
INITIAL_CAPITAL = 100_000.0
SLIPPAGE_BUY = 1.01      # 买入价 = 原价 × 1.01
SLIPPAGE_SELL = 0.99     # 卖出价 = 原价 × 0.99
MAIN_LOT_SIZE = 100      # 主板整手
STAR_LOT_SIZE = 200      # 688 整手
STAR_UNLOCK_PROFIT = 500_000.0  # 累计盈利达 50 万前禁止买 688

def _slippage_price(raw_px: float, is_buy: bool) -> float:
    return raw_px * SLIPPAGE_BUY if is_buy else raw_px * SLIPPAGE_SELL
```

- **成交 / 现金 / 市值**：未复权价 + 滑点
- **累计收益率曲线**：持仓按后复权价盯市
- **入场点** `start_date`：该日之前调仓全部跳过，从该日起 **空仓** 开始跟

---

## 3. route_g 策略专用参数

文件：`backend/xueqiu/domain/copy_strategies.py` → `run_strategy()` 中 `ROUTE_G_CONVICTION_TRUST` 分支

```python
elif strategy_id == StrategyId.ROUTE_G_CONVICTION_TRUST:
    ctx.min_consensus_count = 1
    ctx.open_on_signal = True
    ctx.conviction_tier_mode = True
    ctx.max_stock_pct = 0.40
    ctx.belief_cap_pct = 0.40
    ctx.max_positions = 8
    ctx.heavy_leg_events = build_heavy_leg_events(all_trades, adj_map)
    cb._RUN_CFG = BacktestConfig(
        initial_capital=initial_capital,
        max_stock_pct=0.40,
        max_positions=8,
        min_new_position_pct=1.0,
    )
```

| 参数 | 值 | 含义 |
|------|-----|------|
| conviction_tier_mode | true | 启用信念分级目标仓位 |
| conviction_min_master_pct | 20% | 师傅 to_weight < 20% 的 **开仓/加仓信号跳过** |
| open_on_signal | true | 有信号即尝试开仓（非共识池模式） |
| max_positions | **8** | 最多 8 只股票；满员时 **腾位** |
| max_stock_pct / belief_cap_pct | 40% | 单票硬顶 |
| skip_orphan_reduce | true（默认） | 师傅减仓时若我们无对应 slice → **不跟减** |
| consensus_boost | **false** | 不启用「双共识固定加至 15%」 |
| heavy_leg_events | 预计算 | 全历史师傅重仓 leg，供信用查询 |

### 3.1 与对照策略对比

| 策略 id | 跟单门槛 | 单票上限 | 持仓数上限 | 子账户 |
|---------|----------|----------|------------|--------|
| route_g_conviction_trust | ≥20% 才开仓 | 40% | **8** | 无（单池合并） |
| route_f_partition_mimic | 全部信号 | 20% | 无 | 每师傅 20% 预算 |
| route_b_merged_boost | 全部信号 | 20% | 无 | 无 |
| route_e_dual_pool_boost | 全部信号 | 20% | 无 | 活跃/稳健双池各 50% |

---

## 4. 信念分级 + 师傅信用（核心公式）

文件：`backend/xueqiu/domain/copy_conviction.py`

### 4.1 常量

```python
HEAVY_HOLDER_PCT = 20.0
TIER_TRIAL_CAP = 0.15      # 试探：占我们 NAV 15%
TIER_BELIEF_CAP = 0.30     # 信念：30%
TIER_STRONG_CAP = 0.38     # 强信念：38%
CONSENSUS_BONUS_PCT = 0.05 # 每多 1 个重仓师傅 +5% 乘数
TRUST_FLOOR = 0.75
TRUST_MIN_LEGS = 3         # 至少 3 笔重仓 leg 才启用信用折扣
```

### 4.2 目标仓位 conviction_cap_pct()

逻辑摘要：

1. 收集 mirror 中各师傅对该票的权重 + 当前信号权重
2. 若 `max(权重) < 20%` → **返回 0，不开仓**
3. 定 base tier：
   - **38%**：任一师傅 ≥50%，或 ≥3 个师傅 ≥20%
   - **30%**：任一 ≥35%，或 ≥2 个师傅 ≥20%
   - **15%**：仅 1 个师傅 ≥20%
4. 若 heavy_count ≥ 2：`base *= 1 + 0.05 × (heavy_count - 1)`
5. `base *= trust`（trust ∈ [0.75, 1.0]）
6. `return min(base, 0.40)`

完整代码：

```python
def conviction_cap_pct(
    master_to_weight: float,
    mirror: dict[tuple[str, str], float],
    code: str,
    trust: float,
    *,
    hard_cap: float = 0.40,
    heavy_pct: float = HEAVY_HOLDER_PCT,
) -> float:
    holders = _holders_for_code(mirror, code)
    weights = set(holders.values())
    weights.add(float(master_to_weight))
    max_master = max(weights, default=0.0)
    heavy_count = sum(1 for w in weights if w >= heavy_pct)

    if max_master < heavy_pct:
        return 0.0

    if max_master >= 50.0 or heavy_count >= 3:
        base = TIER_STRONG_CAP
    elif max_master >= 35.0 or heavy_count >= 2:
        base = TIER_BELIEF_CAP
    else:
        base = TIER_TRIAL_CAP

    if heavy_count >= 2:
        base *= 1.0 + CONSENSUS_BONUS_PCT * (heavy_count - 1)

    base *= max(TRUST_FLOOR, min(1.0, trust))
    return min(base, hard_cap)
```

### 4.3 师傅信用 portfolio_trust_at()

- 回放 **该师傅** 全部调仓（VirtualFund），收集 `from_weight ≥ 20%` 且 **减仓/清仓** 时的 leg 收益
- 查询时只用 `trade_time < 当前时刻` 的事件（无未来函数）
- leg 数 < 3 → trust = **1.0**（中性，不折扣）
- 胜率 ≥85% → 1.0；≥75% → 0.92；≥65% → 0.85；否则 → **0.75**
- 多师傅共识时 `consensus_trust_for_code()` 取 **最低 trust**

```python
def portfolio_trust_at(events, account_code, as_of_time, *, min_legs=3, threshold=20.0):
    legs = [e for e in events
            if e.account_code == account_code
            and e.trade_time < as_of_time
            and e.from_weight >= threshold]
    if len(legs) < min_legs:
        return 1.0
    win_rate = sum(1 for e in legs if e.leg_return_pct >= 0) / len(legs)
    if win_rate >= 0.85: return 1.0
    if win_rate >= 0.75: return 0.92
    if win_rate >= 0.65: return 0.85
    return TRUST_FLOOR  # 0.75
```

**注意**：信用基于 **师傅组合自身** 的 leg 收益，不是 **我们跟单账户** 的 leg 收益。

---

## 5. 记账模型：SliceLedger

文件：`backend/xueqiu/domain/copy_backtest.py`

- **物理仓** `holdings[code]`：该股总 qty / vwap
- **逻辑 slice** `slices[(portfolio, code)]`：每个师傅对该票的独立 qty / vwap
- 跟减只动对应师傅的 slice；物理仓为 slice 之和
- 已实现 leg 收益：`(sell_price / slice_vwap - 1) × 100`，sell_price 含 0.99 滑点，vwap 含 1.01 买入成本

---

## 6. 每个调仓批次的执行顺序

```
1. _run_owners_batch()     — 逐条处理师傅信号（减仓/加仓/开仓）
2. _update_mirror()        — 更新师傅持仓镜像
3. _apply_conviction_consensus_align() — 双师傅重仓票再对齐目标（仅 route_g）
4. enforce_stock_cap()     — 单票超 40% 强制 trim
5. 记录净值点
```

### 6.1 师傅减仓

- 有 `(portfolio, code)` slice → 按师傅 from/to 比例卖
- **无 slice 且 skip_orphan_reduce=true** → 跳过（日志「无slice」）
- 师傅清仓 → 卖光该 slice

### 6.2 师傅加仓 / 开仓（conviction_tier_mode）

关键代码：`copy_strategies.py` `_run_owners_batch()`

```python
elif trade.to_weight > trade.from_weight + 1e-9:
    # ① 门槛：师傅目标仓位必须 ≥ 20%
    if ctx.conviction_tier_mode and float(trade.to_weight) < ctx.conviction_min_master_pct:
        continue
    # ② 688 未 unlock 则跳过
    ...
    if sl.qty > 1e-12:
        # 已有 slice：按比例加仓，受 40% cap 约束
        result = fund.apply_increase_existing_slice(...)
    elif ctx.open_on_signal:
        # ③ 计算信念目标仓位
        if ctx.conviction_tier_mode:
            target = _conviction_target_for_open(ctx, mirror, acct_code, code, trade.to_weight, trade_time)
            if target <= 0:
                continue
        # ④ 满 8 只则腾位
        _ensure_position_slot(fund, ctx, raw_prices, trade_logs, trade_time, trade.ts_code)
        # ⑤ 买到 target 占 NAV
        bought = _try_buy_to_target(fund, code, raw_px, target, nav_pre, {acct_code: ...}, star_unlocked)
```

### 6.3 信念共识加仓（批后）

`_apply_conviction_consensus_align()`：
- mirror 中 **≥2 个师傅** 对同一票 ≥20%
- 再算 consensus trust + cap，买到目标
- 若尚未持仓，同样触发 **腾位**

### 6.4 腾位逻辑（**核心缺陷源**）

```python
def _ensure_position_slot(fund, ctx, raw_prices, trade_logs, trade_time, new_code):
    if ctx.max_positions <= 0:
        return
    while True:
        held = [c for c in _held_codes(fund) if c != new_code]
        if len(held) < ctx.max_positions:
            return
        if not _sell_weakest_position(fund, raw_prices, trade_logs, trade_time):
            return

def _sell_weakest_position(fund, raw_prices, trade_logs, trade_time, *, trigger="换仓腾位"):
    # 找 float P&L 最差的持仓：ret = px / holding.vwap - 1  （px 为未滑点原价）
    ...
    sell_px = _slippage_price(px, False)  # × 0.99
    sold = fund.liquidate_all_slices(worst_code, sell_px, nav)  # 清空该股 **全部** slice
    # 日志：action=腾位卖出, trigger=换仓腾位
```

**关键行为**：
- **不检查** mirror 中师傅是否仍重仓该股
- `liquidate_all_slices` 清掉 **所有师傅** 在该票上的 slice
- 腾位依据是 **我们的短期 float P&L**，不是师傅减码信号

---

## 7. 股票 Tab「累计收益」与 -1.98% 现象

### 7.1 计算公式

```python
def _record_sell_leg(self, code, sell_qty, sell_price, vwap, nav_pre):
    leg_return_pct = round((sell_price / vwap - 1.0) * 100, 2)

def stock_cum_return_pct(self, code, mark_hfq):
    # 有卖出 leg → 按 weight_sold 加权平均 leg 收益
    # 仍持仓 → 混入未实现收益（后复权 mark vs 成本）
```

`grouped_stats` 包含 **所有历史碰过的股票**（`trade_counts` ∪ 当前持仓），不只是最终 8 只。

### 7.2 -1.98% 的数学含义

```
leg_return = (0.99 / 1.01 - 1) × 100 ≈ -1.98%
```

= 买入 1% 滑点 + 卖出 1% 滑点，**股价几乎不变**时的固定往返损耗。

### 7.3 用户看到的典型模式

| 股票 | 平仓次数 | 胜率 | 累计收益 | 解读 |
|------|----------|------|----------|------|
| 天华新能 | 1 | 0% | -1.98% | 买后很快被腾位卖掉，纯摩擦 |
| 精测电子 | 1 | 0% | -1.98% | 同上 |
| （约 30+ 只） | 1 | 0% | -1.98% | 同上 |
| 淳中科技 | 5 | 60% | +489% | 真正拿住的大赢家 |
| 东山精密 | 0 | 0% | +167% | 仍持仓，未平仓，浮盈 |

**结论**：-1.98% **不是计算公式写错**，而是 **max_positions=8 + 腾位 + 短持** 导致的 **结构性 churn（摩擦税）**。

### 7.4 缺陷链条

```
max_positions=8
  → 新重仓信号频繁到来
  → _ensure_position_slot 腾位
  → 卖 float 最弱票（不看师傅是否仍重仓）
  → liquidate_all_slices 清掉所有师傅 slice
  → 短持往返 -1.98%
  → grouped_stats 大量负收益「过客」
  → 总净值被摩擦蚕食
```

同时：**试探 tier 15% × 8 槽 = 120% 理论仓位需求**，必然高现金或频繁 cap/腾位。

---

## 8. 完整缺陷清单

### A. 结构性 / 逻辑缺陷（影响真实回测表现）

| # | 缺陷 | 说明 |
|---|------|------|
| 1 | 腾位与跟单哲学冲突 | 为凑 8 名额强制卖票，无视师傅 mirror 仍重仓；可能卖掉师傅核心仓 |
| 2 | 摩擦循环 | 同一票可反复「买入→腾位→再买入」，每次约 -2%，alpha=0 仍亏 |
| 3 | 8 槽 vs 9 师傅 | 信号密度远高于槽位，策略退化为不断换仓交摩擦税 |
| 4 | 双重标准 | 开仓看师傅 ≥20% 信念；腾位看我们 float P&L |
| 5 | 单池合并 | 多师傅重仓目标叠加在同一 NAV，必然 cap + 腾位；分仓模仿用子预算规避 |
| 6 | 信用指标错位 | trust 用师傅 leg，非我们跟单 leg；师傅卖在高点 ≠ 我们能同样卖 |
| 7 | skip_orphan_reduce 不对称 | 没跟上的开仓不会跟减；腾位清 slice 后师傅仍持有，我们 orphaned |
| 8 | 688 解锁 | 盈利 50 万前禁买 688，可能错过部分师傅主战场 |

### B. 参数 / 设计选择

| # | 问题 |
|---|------|
| 9 | 试探 15% 对 8 槽位过大 |
| 10 | 无 consensus_boost（对照策略有双共识加至 15%） |
| 11 | trust 样本 <3 时默认 1.0，新师傅反而满信用 |

### C. 展示 / 统计（影响解读）

| # | 问题 |
|---|------|
| 12 | grouped_stats 混入大量已清掉的历史票，与最终 8 只持仓脱节 |
| 13 | 策略回测路径未写 stock_last_trade_time，UI 显示 `-` |
| 14 | UI 未区分「滑点摩擦 -1.98%」vs「选股亏损」 |

---

## 9. 单元测试覆盖范围

文件：`backend/tests/test_copy_conviction.py`

- 仅测试 `conviction_cap_pct()` 分级与 `portfolio_trust_at()` 无未来数据
- **无** 腾位、max_positions、摩擦 churn 的集成测试

---

## 10. 关键源文件索引

| 路径 | 职责 |
|------|------|
| backend/xueqiu/domain/copy_conviction.py | 信念 tier + 师傅信用公式 |
| backend/xueqiu/domain/copy_strategies.py | 策略编排、腾位、共识对齐、run_strategy |
| backend/xueqiu/domain/copy_backtest.py | SliceLedger、滑点、grouped_stats、load_portfolio_trades |
| backend/tests/test_copy_conviction.py | tier/trust 单元测试 |
| frontend/src/pages/BacktestPage.tsx | 回测对比 UI（含入场日期选择） |

---

## 11. 若需改进 — 优先级建议（供评审参考）

1. **腾位规则**：仅当所有重仓师傅均已减至 <20% 或清仓时才允许卖；或取消腾位、只 cap 权重
2. **槽位 / tier**：提高 max_positions 至 15~20，或 tier 随槽位数缩放 `target = tier_cap / max_positions`
3. **分仓**：改为每师傅子账户（类似 route_f_partition_mimic）+ 信念分级
4. **统计**：grouped_stats 默认只展示当前持仓；标注「腾位摩擦」类交易
5. **测试**：集成测试「开仓后下一批即腾位」，断言 friction 次数上限

---

## 12. 附录：用户提供的回测股票收益样例（节选）

```
淳中科技    平仓5   胜率60%   累计+489.02%
东山精密    平仓0   胜率0%    累计+167.00%   （仍持仓）
...
天华新能    平仓1   胜率0%    累计-1.98%
精测电子    平仓1   胜率0%    累计-1.98%
思特奇      平仓1   胜率0%    累计-1.98%
（约30+只） 平仓1   胜率0%    累计-1.98%
...
赣锋锂业    平仓3   胜率0%    累计-23.70%
```

---

*文档生成自 xueqiu 项目代码审查，版本以仓库当前 main/工作区为准。*
