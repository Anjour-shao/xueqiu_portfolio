"""铖昌科技 价格监控 + 钉钉推送。

用法:
    cd backend
    python ../scripts/price_alert.py          # 跑一次
    python ../scripts/price_alert.py --loop   # 持续监控，每5分钟查一次（适合后台挂机）

在 .env 里配好 DINGTALK_WEBHOOK，价格触发时自动推送。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.config import DINGTALK_WEBHOOK, DINGTALK_KEYWORD

# ---------- 配置 ----------
STOCK_CODE = "SZ001270"
STOCK_NAME = "铖昌科技"
SINA_SYMBOL = "sz001270"

# 两个加仓目标价
TARGET_1 = 105.0   # 第一批加仓
TARGET_2 = 96.0    # 第二批加仓

# 只要价格 <= 任一目标价就推送
TARGETS = [
    (TARGET_1, "第一批加仓 (105元)"),
    (TARGET_2, "第二批加仓 (96元)"),
]

CHECK_INTERVAL = 300  # 5 分钟

_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


# ---------- 获取现价 ----------
def get_current_price() -> float | None:
    """新浪实时行情，返回现价"""
    url = f"https://hq.sinajs.cn/list={SINA_SYMBOL}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text
        # 格式: var hq_str_sz001270="铖昌科技,121.61,..."
        eq = text.find("=")
        if eq < 0:
            return None
        payload = text[eq + 1:].strip().strip('";')
        parts = payload.split(",")
        if len(parts) < 4:
            return None
        # parts[1]: 昨收, parts[2]: 开盘, parts[3]: 现价(实际是新价格)
        # 准确位置: parts[3] 就是当前价格
        price = float(parts[3])
        if price <= 0:
            return None
        # parts[1] 是昨收，可以用来算涨跌幅
        pre_close = float(parts[2]) if len(parts) > 2 else 0
        return price
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] 获取行情失败: {e}")
        return None


# ---------- 钉钉推送 ----------
def send_dingtalk(alert_msg: str) -> bool:
    if not DINGTALK_WEBHOOK:
        print("  (未配置 DINGTALK_WEBHOOK，跳过推送)")
        return False

    keyword = DINGTALK_KEYWORD or "铖昌科技"
    md = (
        f"### 🔔 {keyword} 价格提醒\n\n"
        f"{alert_msg}\n\n"
        f"> 推送时间: {datetime.now():%Y-%m-%d %H:%M:%S}"
    )

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": f"{keyword} 价格提醒", "text": md},
    }
    try:
        resp = requests.post(
            DINGTALK_WEBHOOK,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=10,
        )
        if resp.status_code == 200:
            print("  -> 钉钉推送成功")
            return True
        else:
            print(f"  -> 钉钉推送失败: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  -> 钉钉推送异常: {e}")
        return False


# ---------- 检查 & 推送 ----------
def check_and_alert(price: float, alert_state: dict) -> None:
    now = datetime.now()
    print(f"\n[{now:%Y-%m-%d %H:%M:%S}] {STOCK_NAME} 现价: {price:.2f}")

    for target, label in TARGETS:
        if price <= target:
            # 防止重复推送同一档
            target_key = str(target)
            if not alert_state.get(target_key):
                pct_from = (price - target) / target * 100
                msg = (
                    f"**{STOCK_NAME}** 已触及 **{label}**\n\n"
                    f"- 目标价: **{target:.2f}**\n"
                    f"- 当前价: **{price:.2f}** (偏离 {pct_from:+.1f}%)\n"
                    f"- 距前低(97.54): {(price-97.54)/97.54*100:+.1f}%\n\n"
                    f"⏰ 请评估是否执行加仓操作。"
                )
                send_dingtalk(msg)
                alert_state[target_key] = True
                print(f"  !!! 触发 {label} !!!")


# ---------- 主入口 ----------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="铖昌科技价格监控")
    parser.add_argument("--loop", action="store_true", help="持续监控模式")
    parser.add_argument("--interval", type=int, default=300, help="轮询间隔(秒)，默认300")
    args = parser.parse_args()

    print(f"=== {STOCK_NAME}({STOCK_CODE}) 价格监控 ===")
    print(f"目标价: {TARGET_1:.2f} (第一批) / {TARGET_2:.2f} (第二批)")
    print(f"当前时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"钉钉推送: {'已配置' if DINGTALK_WEBHOOK else '未配置'}")
    print()

    alert_state = {}  # 记录已推送的档位，避免重复

    if not args.loop:
        price = get_current_price()
        if price is not None:
            check_and_alert(price, alert_state)
        else:
            print("获取行情失败")
        return

    # 持续监控
    print(f"进入持续监控模式，每 {args.interval} 秒检查一次...")
    print(f"(按 Ctrl+C 退出)\n")

    try:
        while True:
            price = get_current_price()
            if price is not None:
                check_and_alert(price, alert_state)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n监控已停止。")


if __name__ == "__main__":
    main()
