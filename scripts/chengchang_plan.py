"""铖昌科技 加仓/止盈/止损 操作计划计算器"""

low = 97.54       # 120日最低
high = 180.64     # 60日最高
current = 121.61  # 当前价
atr = 13.76       # 14日ATR

print("=" * 60)
print("  铖昌科技(001270) 操作计划")
print(f"  波段: {low} -> {high} -> {current}")
print(f"  总涨幅: {(high/low-1)*100:.1f}%  |  已回撤: {(1-current/high)*100:.1f}%")
print("=" * 60)

# ---- 斐波那契 ----
print("\n" + "=" * 30)
print("  斐波那契回撤位")
print("=" * 30)
for f, desc in [(0.236, "浅回调"), (0.382, ""), (0.500, "半分位"), (0.618, "黄金分割"), (0.786, "深回调"), (0.886, "极限回调")]:
    level = high - (high - low) * f
    dist = (level - current) / current * 100
    marker = " <<<" if abs(dist) < 3 else ""
    print(f"  {f*100:5.1f}%  {level:8.2f}  (距现价 {dist:+5.1f}%){marker}  {desc}")

# ---- 加仓网格 ----
print("\n" + "=" * 30)
print("  四批加仓计划 (假设每批等量)")
print("=" * 30)

grid = [
    (122, "现价区", "KDJ-J负数+超卖+机构120建仓痕迹", "第一批 25%"),
    (115, "斐波78.6%+布林下轨共振", "技术面强支撑叠加", "第二批 25%"),
    (107, "斐波88.6%+极端超卖", "2倍标准差外，恐慌极点", "第三批 25%"),
    (98,  "前低支撑(3/24低点)", "破此位则波段逻辑破坏", "第四批 25% (备选)"),
]

cum_weight = 0
cum_cost = 0
for price, zone, reason, batch in grid:
    cum_weight += 25
    cum_cost += price * 25
    dist = (price - current) / current * 100
    print(f"\n  [{batch}] {price:.2f} ({dist:+.1f}%) - {zone}")
    print(f"      依据: {reason}")

avg_cost = cum_cost / cum_weight
print(f"\n  >>> 四批全部成交后均价: {avg_cost:.2f}")

# ---- 假设用户成本 170 ----
print("\n" + "=" * 30)
print("  假设不同初始成本的摊薄效果")
print("=" * 30)

for init_cost in [160, 170, 180]:
    for add_pct in [0.5, 1.0, 1.5]:
        # 假设初始有 100 股，再加仓 add_pct*100 股
        init_shares = 100
        init_total = init_shares * init_cost
        add_shares = init_shares * add_pct
        # 加仓均价按四批均价 110.5 算
        add_avg = 110.5
        add_total = add_shares * add_avg
        new_avg = (init_total + add_total) / (init_shares + add_shares)
        print(f"  初始成本{init_cost}, 加仓{add_pct*100:.0f}%@{add_avg:.0f} -> 新均价: {new_avg:.2f} (回本需涨{new_avg/current-1:.1%})")

# ---- 止盈网格 ----
print("\n" + "=" * 30)
print("  三档止盈计划")
print("=" * 30)

tp_grid = [
    (135, "MA120 + 斐波50%", "减仓20%，回收部分现金", "反弹第一目标"),
    (150, "MA20/MA60 密集区", "减仓30%，成本大幅摊低", "均线密集压力区"),
    (165, "前高下方", "减仓30%，锁定利润", "接近前高，套牢盘密集"),
    (180, "前高突破", "清仓或留利润奔跑", "突破需放量确认"),
]

for price, tech_zone, action, note in tp_grid:
    dist = (price - current) / current * 100
    print(f"  {price:.2f} (+{dist:.1f}%) - {tech_zone}")
    print(f"      -> {action}  ({note})")

# ---- 止损/认输 ----
print("\n" + "=" * 30)
print("  止损/认输条件")
print("=" * 30)
print(f"  硬止损: 跌破 95.00 (-21.9%) -> 减仓50%，前低被有效跌破")
print(f"  时间止损: 若3个月内无法反弹至MA60(143+) -> 减仓30%")
print(f"  基本面止损: 若下季度业绩继续亏损/ST风险 -> 无条件清仓")

# ---- 仓位纪律 ----
print("\n" + "=" * 30)
print("  仓位纪律")
print("=" * 30)
print(f"  单只个股上限: 不超过总仓位的30%")
print(f"  加仓后该股仓位如果超过30%，须先减其他持仓")
print(f"  每批加仓之间至少间隔3个交易日")
print(f"  急跌日(跌幅>7%)不加仓，等次日缩量企稳再动手")
print(f"  加仓后均价应始终低于MA60(目前143)，否则停止加仓")
