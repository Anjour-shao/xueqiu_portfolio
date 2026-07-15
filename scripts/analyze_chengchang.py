"""铖昌科技(001270) 综合走势分析 —— 技术面 + 雪球情绪。

纯本地技术计算 + 新浪行情 + 雪球评论，不依赖 tushare。

用法:
    cd backend
    python ../scripts/analyze_chengchang.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from math import sqrt
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# 路径 & 编码
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
STOCK_CODE = "SZ001270"
STOCK_NAME = "铖昌科技"
SINA_SYMBOL = "sz001270"  # 新浪格式

_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ===================================================================
# 1. 新浪日K线数据（复权）
# ===================================================================

def fetch_sina_daily_kline(sina_symbol: str, days: int = 250) -> list[dict]:
    """从新浪日K线接口获取 OHLCV 数据。

    新浪日K接口: https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getPageDailyKLine
    返回最近约 2 年的日线数据（前复权）。
    """
    # 新浪 stock2finance 日K接口（更稳定）
    url = (
        f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php"
        f"/var%20_{sina_symbol}_da_da={int(time.time() * 1000)}"
        f"/InnerFuturesNewService.getDailyKLine"
        f"?symbol={sina_symbol}"
    )

    # 先尝试 hq.sinajs.cn 的日K API（最稳定）
    all_data = []

    # 方案A: money.finance.sina.com.cn 的日K接口
    try:
        url_a = (
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData"
            f"?symbol={sina_symbol}&scale=240&ma=no&datalen={days}"
        )
        resp = requests.get(url_a, headers=_HEADERS, timeout=15)
        resp.encoding = "gbk"
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            all_data = data
    except Exception:
        pass

    # 如果方案A失败，尝试方案B: 腾讯接口
    if not all_data:
        try:
            # 腾讯日K接口
            tencent_code = "sz001270"
            url_b = (
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={tencent_code},day,,,{days},qfq"
            )
            resp = requests.get(url_b, headers=_HEADERS, timeout=15)
            raw = resp.json()
            klines = (
                raw.get("data", {})
                .get(tencent_code, {})
                .get("qfqday", [])
                or raw.get("data", {})
                .get(tencent_code, {})
                .get("day", [])
            )
            for k in klines:
                all_data.append({
                    "day": k[0],
                    "open": k[1],
                    "close": k[2],
                    "high": k[3],
                    "low": k[4],
                    "volume": k[5],
                })
        except Exception:
            pass

    # 方案C: 新浪 houfuquan 后复权接口 (已有项目代码)
    if not all_data:
        try:
            # 这里用最简单的 soup 方式
            from xueqiu.integrations.sina.hfq import fetch_sina_hfq_series
            series = fetch_sina_hfq_series(STOCK_CODE)
            if series:
                for d, price in sorted(series.items()):
                    all_data.append({
                        "day": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                        "close": str(price),
                    })
        except Exception:
            pass

    return all_data


# ===================================================================
# 2. 雪球评论抓取
# ===================================================================


def fetch_xueqiu_posts_simple(symbol: str, max_pages: int = 3) -> list[dict]:
    """简单抓取雪球个股讨论帖（无需 cookie，热门帖子接口）。"""
    posts = []
    try:
        from xueqiu.integrations.xueqiu.client import XueQiuApiClient
        from xueqiu.integrations.xueqiu.posts import fetch_stock_posts
        client = XueQiuApiClient()
        raw_posts = fetch_stock_posts(client, symbol, max_pages=max_pages, page_size=20)
        for p in raw_posts:
            posts.append({
                "id": p.id,
                "created_at": p.created_at,
                "text": p.text[:500],
                "user_name": p.user_name,
                "reply_count": p.reply_count,
                "like_count": p.like_count,
            })
    except Exception as exc:
        print(f"  ⚠ 雪球帖子抓取失败: {exc}")
    return posts


# ===================================================================
# 3. 技术指标计算
# ===================================================================


def calc_sma(data: list[float], period: int) -> list[float | None]:
    """简单移动平均"""
    result = [None] * len(data)
    if len(data) < period:
        return result
    window_sum = sum(data[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(data)):
        window_sum += data[i] - data[i - period]
        result[i] = window_sum / period
    return result


def calc_ema(data: list[float], period: int) -> list[float | None]:
    """指数移动平均"""
    result = [None] * len(data)
    if len(data) < period:
        return result
    k = 2.0 / (period + 1)
    # 用第一个 period 的 SMA 作为初始值
    sma = sum(data[:period]) / period
    result[period - 1] = sma
    for i in range(period, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result


def calc_macd(
    data: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float | None]]:
    """MACD 指标"""
    ema_fast = calc_ema(data, fast)
    ema_slow = calc_ema(data, slow)
    dif = [None] * len(data)
    dea = [None] * len(data)
    bar = [None] * len(data)

    for i in range(len(data)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif[i] = ema_fast[i] - ema_slow[i]

    # DEA = EMA of DIF
    valid_dif = [(i, v) for i, v in enumerate(dif) if v is not None]
    if len(valid_dif) >= signal:
        dif_vals = [v for _, v in valid_dif]
        dif_ema = calc_ema(dif_vals, signal)
        for idx, (orig_i, _) in enumerate(valid_dif):
            if dif_ema[idx] is not None:
                dea[orig_i] = dif_ema[idx]
                bar[orig_i] = 2 * (dif[orig_i] - dif_ema[idx])

    return {"dif": dif, "dea": dea, "bar": bar}


def calc_rsi(data: list[float], period: int = 14) -> list[float | None]:
    """RSI 指标 (Wilder's smoothing)"""
    result = [None] * len(data)
    if len(data) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(data)):
        diff = data[i] - data[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return result


def calc_bollinger(
    data: list[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> dict[str, list[float | None]]:
    """布林带"""
    ma = calc_sma(data, period)
    upper = [None] * len(data)
    lower = [None] * len(data)
    width = [None] * len(data)  # 带宽百分比

    for i in range(period - 1, len(data)):
        window = data[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = sqrt(variance)
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std
        if ma[i] and ma[i] > 0:
            width[i] = (upper[i] - lower[i]) / ma[i] * 100

    return {"ma": ma, "upper": upper, "lower": lower, "width": width}


def calc_kdj(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 9,
) -> dict[str, list[float | None]]:
    """KDJ 指标"""
    n = len(close)
    K = [None] * n
    D = [None] * n
    J = [None] * n

    for i in range(period - 1, n):
        hh = max(high[i - period + 1 : i + 1])
        ll = min(low[i - period + 1 : i + 1])
        rsv = (close[i] - ll) / (hh - ll) * 100 if hh != ll else 50

        if K[i - 1] is not None:
            K[i] = 2 / 3 * K[i - 1] + 1 / 3 * rsv
        else:
            K[i] = rsv

        if D[i - 1] is not None:
            D[i] = 2 / 3 * D[i - 1] + 1 / 3 * K[i]
        else:
            D[i] = K[i]

        J[i] = 3 * K[i] - 2 * D[i]

    return {"K": K, "D": D, "J": J}


def calc_atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float | None]:
    """Average True Range"""
    n = len(close)
    tr = [None] * n
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    atr = [None] * n
    if n < period:
        return atr
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def calc_volume_ratio(volumes: list[float], period: int = 5) -> list[float | None]:
    """量比：当日成交量 / 前N日均量"""
    n = len(volumes)
    vr = [None] * n
    for i in range(period, n):
        avg_vol = sum(volumes[i - period : i]) / period
        if avg_vol > 0:
            vr[i] = volumes[i] / avg_vol
    return vr


# ===================================================================
# 4. 偏离度 & 买卖点判断
# ===================================================================


def analyze_position(
    close: list[float],
    high: list[float],
    low: list[float],
    volumes: list[float],
    dates: list[str],
    latest_price: float,
) -> dict:
    """综合技术面分析，判断当前位置。"""
    n = len(close)
    result = {
        "latest_price": latest_price,
        "latest_date": dates[-1] if dates else "",
        "signals": [],
        "warnings": [],
        "levels": {},
        "summary": "",
    }

    # ---- 均线 ----
    ma5 = calc_sma(close, 5)
    ma10 = calc_sma(close, 10)
    ma20 = calc_sma(close, 20)
    ma60 = calc_sma(close, 60)
    ma120 = calc_sma(close, 120)

    # 均线排列
    if all(x is not None for x in [ma5[-1], ma10[-1], ma20[-1]]):
        if ma5[-1] > ma10[-1] > ma20[-1]:
            result["signals"].append("均线多头排列(MA5>MA10>MA20)，趋势向上")
        elif ma5[-1] < ma10[-1] < ma20[-1]:
            result["warnings"].append("均线空头排列(MA5<MA10<MA20)，趋势向下")

    # 价格 vs 各均线偏离度
    ma_deviation = {}
    for name, ma in [("MA5", ma5), ("MA10", ma10), ("MA20", ma20), ("MA60", ma60), ("MA120", ma120)]:
        if ma[-1] is not None and ma[-1] > 0:
            dev = (latest_price - ma[-1]) / ma[-1] * 100
            ma_deviation[name] = {
                "value": round(ma[-1], 2),
                "deviation_pct": round(dev, 2),
            }
    result["ma_deviation"] = ma_deviation

    # 极大偏离位检测
    if ma20[-1] is not None and ma20[-1] > 0:
        dev_20 = (latest_price - ma20[-1]) / ma20[-1] * 100
        if abs(dev_20) > 15:
            direction = "上方" if dev_20 > 0 else "下方"
            result["warnings"].append(
                f"⚠ 价格大幅偏离MA20均线({dev_20:+.1f}%)，处于{direction}极端位，注意回归风险"
            )
        elif abs(dev_20) > 10:
            direction = "上方" if dev_20 > 0 else "下方"
            result["signals"].append(
                f"价格偏离MA20均线{dev_20:+.1f}%，偏离度较高"
            )

    # ---- 近期高低点 ----
    lookback = min(120, n)
    recent_high = max(high[-lookback:])
    recent_low = min(low[-lookback:])
    high_date = dates[-lookback:][high[-lookback:].index(recent_high)]
    low_date = dates[-lookback:][low[-lookback:].index(recent_low)]

    from_high = (latest_price - recent_high) / recent_high * 100
    from_low = (latest_price - recent_low) / recent_low * 100

    result["levels"]["recent_high"] = {"price": round(recent_high, 2), "date": high_date, "pct_from": round(from_high, 1)}
    result["levels"]["recent_low"] = {"price": round(recent_low, 2), "date": low_date, "pct_from": round(from_low, 1)}

    # ---- 波段高低点 (60日) ----
    lookback_60 = min(60, n)
    highest_60 = max(high[-lookback_60:])
    lowest_60 = min(low[-lookback_60:])
    pct_from_60h = (latest_price - highest_60) / highest_60 * 100
    pct_from_60l = (latest_price - lowest_60) / lowest_60 * 100
    result["levels"]["60d_high"] = {"price": round(highest_60, 2), "pct_from": round(pct_from_60h, 1)}
    result["levels"]["60d_low"] = {"price": round(lowest_60, 2), "pct_from": round(pct_from_60l, 1)}

    # ---- 支撑/压力位 ----
    supports = []
    resistances = []
    for name, ma in [("MA20", ma20), ("MA60", ma60), ("MA120", ma120)]:
        if ma[-1] is not None and ma[-1] > 0:
            if ma[-1] < latest_price:
                supports.append((name, round(ma[-1], 2)))
            else:
                resistances.append((name, round(ma[-1], 2)))
    # 近期低点、前低
    supports.append((f"前低({low_date})", round(recent_low, 2)))
    resistances.append((f"前高({high_date})", round(recent_high, 2)))

    supports.sort(key=lambda x: x[1], reverse=True)
    resistances.sort(key=lambda x: x[1])
    result["supports"] = supports[:4]
    result["resistances"] = resistances[:4]

    # ---- MACD ----
    macd = calc_macd(close)
    if macd["dif"][-1] is not None and macd["dea"][-1] is not None:
        result["macd"] = {
            "dif": round(macd["dif"][-1], 4),
            "dea": round(macd["dea"][-1], 4),
            "bar": round(macd["bar"][-1], 4),
        }
        # 金叉/死叉检测
        if n >= 2 and macd["dif"][-2] is not None and macd["dea"][-2] is not None:
            if macd["dif"][-2] <= macd["dea"][-2] and macd["dif"][-1] > macd["dea"][-1]:
                result["signals"].append("✅ MACD 刚金叉，短期看涨信号")
            elif macd["dif"][-2] >= macd["dea"][-2] and macd["dif"][-1] < macd["dea"][-1]:
                result["warnings"].append("❌ MACD 刚死叉，短期看跌信号")
        if macd["bar"][-1] > 0:
            result["signals"].append("MACD 红柱（多头区域）")
        else:
            result["warnings"].append("MACD 绿柱（空头区域）")

    # ---- RSI ----
    rsi = calc_rsi(close, 14)
    if rsi[-1] is not None:
        result["rsi"] = round(rsi[-1], 1)
        if rsi[-1] < 30:
            result["signals"].append(f"🔥 RSI={rsi[-1]:.0f}，进入超卖区，反弹概率较高")
        elif rsi[-1] < 20:
            result["signals"].append(f"🔥🔥 RSI={rsi[-1]:.0f}，极度超卖，历史级别买点区域")
        elif rsi[-1] > 70:
            result["warnings"].append(f"⚠ RSI={rsi[-1]:.0f}，进入超买区，注意回调风险")
        elif rsi[-1] > 85:
            result["warnings"].append(f"⚠⚠ RSI={rsi[-1]:.0f}，极度超买，建议止盈")

    # ---- KDJ ----
    kdj = calc_kdj(high, low, close)
    if kdj["K"][-1] is not None:
        result["kdj"] = {
            "K": round(kdj["K"][-1], 1),
            "D": round(kdj["D"][-1], 1),
            "J": round(kdj["J"][-1], 1),
        }
        if kdj["J"][-1] < 0:
            result["signals"].append("KDJ的J值<0，超卖信号")
        elif kdj["J"][-1] > 100:
            result["warnings"].append("KDJ的J值>100，超买信号")
        if n >= 2 and kdj["K"][-2] is not None and kdj["D"][-2] is not None:
            if kdj["K"][-2] <= kdj["D"][-2] and kdj["K"][-1] > kdj["D"][-1]:
                result["signals"].append("KDJ 金叉")
            elif kdj["K"][-2] >= kdj["D"][-2] and kdj["K"][-1] < kdj["D"][-1]:
                result["warnings"].append("KDJ 死叉")

    # ---- 布林带 ----
    boll = calc_bollinger(close, 20, 2.0)
    if boll["upper"][-1] is not None:
        result["bollinger"] = {
            "upper": round(boll["upper"][-1], 2),
            "ma": round(boll["ma"][-1], 2),
            "lower": round(boll["lower"][-1], 2),
            "width": round(boll["width"][-1], 1) if boll["width"][-1] is not None else None,
        }
        # 布林带位置
        bb_range = boll["upper"][-1] - boll["lower"][-1]
        if bb_range > 0:
            bb_pos = (latest_price - boll["lower"][-1]) / bb_range
            if bb_pos > 0.95:
                result["warnings"].append("价格触及布林上轨，超买")
            elif bb_pos < 0.05:
                result["signals"].append("价格触及布林下轨，超卖反弹机会")
        # 带宽
        if boll["width"][-1] is not None and boll["width"][-1] < 5:
            result["signals"].append("布林带收窄，可能即将变盘")

    # ---- 成交量分析 ----
    vol_ratio = calc_volume_ratio(volumes, 5)
    if vol_ratio[-1] is not None:
        result["volume_ratio"] = round(vol_ratio[-1], 2)
        if vol_ratio[-1] > 2.0:
            result["signals"].append(f"📊 放量！量比{vol_ratio[-1]:.1f}，关注方向")
        elif vol_ratio[-1] < 0.5:
            result["signals"].append(f"缩量明显，量比{vol_ratio[-1]:.2f}，变盘前兆")

    # ---- ATR (波动率) ----
    atr = calc_atr(high, low, close)
    if atr[-1] is not None and latest_price > 0:
        atr_pct = atr[-1] / latest_price * 100
        result["atr"] = {"value": round(atr[-1], 2), "pct": round(atr_pct, 2)}

    # ---- 综合判断 ----
    bullish = len(result["signals"])
    bearish = len(result["warnings"])

    if bearish >= 3:
        result["summary"] = "⚠ 空头信号较多，当前位置风险较大，建议观望或减仓"
    elif bullish >= 3:
        result["summary"] = "✅ 多头信号占优，技术面偏积极"
    elif bullish > bearish:
        result["summary"] = "偏多，但信号不够强烈，可谨慎持有"
    elif bearish > bullish:
        result["summary"] = "偏空，注意控制仓位"
    else:
        result["summary"] = "信号中性，方向不明朗，等待更明确的技术信号"

    return result


# ===================================================================
# 5. 打印报告
# ===================================================================


def print_report(
    klines: list[dict],
    analysis: dict,
    xueqiu_posts: list[dict],
) -> None:
    """打印完整的分析报告"""
    print()
    print("=" * 70)
    print(f"  {STOCK_NAME}({STOCK_CODE}) 综合走势分析报告")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ---- 基本信息 ----
    latest = klines[-1] if klines else {}
    price = analysis["latest_price"]
    print(f"\n{'─' * 50}")
    print(f"  📌 最新价: {price:.2f} 元")
    print(f"  📅 数据日期: {analysis['latest_date']}")
    print(f"  📊 获取K线: {len(klines)} 根")
    print(f"{'─' * 50}")

    # ---- 均线偏离 ----
    print(f"\n  【均线偏离度】")
    for name, info in analysis.get("ma_deviation", {}).items():
        dev = info["deviation_pct"]
        bar = "🟢" if dev > 0 else "🔴"
        print(f"    {name}: {info['value']:.2f}  |  偏离: {bar} {dev:+.1f}%")

    # ---- 关键技术指标 ----
    print(f"\n  【关键技术指标】")
    if "rsi" in analysis:
        rsi = analysis["rsi"]
        tag = "超卖" if rsi < 30 else ("超买" if rsi > 70 else "正常")
        print(f"    RSI(14): {rsi:.1f}  [{tag}]")
    if "macd" in analysis:
        m = analysis["macd"]
        bar_tag = "红柱▲" if m["bar"] > 0 else "绿柱▼"
        print(f"    MACD: DIF={m['dif']:.3f}  DEA={m['dea']:.3f}  BAR={m['bar']:.3f}  [{bar_tag}]")
    if "kdj" in analysis:
        k = analysis["kdj"]
        print(f"    KDJ: K={k['K']:.1f}  D={k['D']:.1f}  J={k['J']:.1f}")
    if "bollinger" in analysis:
        b = analysis["bollinger"]
        print(f"    布林带: 上轨={b['upper']:.2f}  中轨={b['ma']:.2f}  下轨={b['lower']:.2f}  带宽={b['width']}%")
    if "atr" in analysis:
        print(f"    ATR(14): {analysis['atr']['value']:.2f} ({analysis['atr']['pct']:.1f}%)")
    if "volume_ratio" in analysis:
        print(f"    量比(5): {analysis['volume_ratio']:.2f}")

    # ---- 关键价位 ----
    print(f"\n  【关键价位参考】")
    levels = analysis.get("levels", {})
    h60 = levels.get("60d_high", {})
    l60 = levels.get("60d_low", {})
    print(f"    60日最高: {h60.get('price', 'N/A')}  (当前距高点 {h60.get('pct_from', 'N/A')}%)")
    print(f"    60日最低: {l60.get('price', 'N/A')}  (当前距低点 +{l60.get('pct_from', 'N/A')}%)")

    print(f"\n    支撑位(下方):")
    for name, val in analysis.get("supports", []):
        dist = (price - val) / price * 100
        print(f"      {name}: {val:.2f}  (距离 {dist:.1f}%)")

    print(f"    压力位(上方):")
    for name, val in analysis.get("resistances", []):
        dist = (val - price) / price * 100
        print(f"      {name}: {val:.2f}  (距离 {dist:.1f}%)")

    # ---- 信号 ----
    print(f"\n  【技术信号】")
    if analysis["signals"]:
        for s in analysis["signals"]:
            print(f"    {s}")
    else:
        print(f"    （无明显看多信号）")

    if analysis["warnings"]:
        print(f"\n  【警示信号】")
        for w in analysis["warnings"]:
            print(f"    {w}")

    # ---- 综合判断 ----
    print(f"\n  {'=' * 50}")
    print(f"  📋 综合判断: {analysis['summary']}")
    print(f"  {'=' * 50}")

    # ---- 雪球情绪 ----
    print(f"\n  【雪球社区近期讨论】({len(xueqiu_posts)} 条)")
    if xueqiu_posts:
        for i, p in enumerate(xueqiu_posts[:10], 1):
            text_preview = p["text"][:100].replace("\n", " ")
            print(f"    {i}. [{p.get('created_at', '?')[:10]}] {p['user_name']}: {text_preview}...")
    else:
        print(f"    （未能获取雪球讨论数据，可能需要配置 Cookie）")

    # ---- 止盈止损建议 ----
    print(f"\n  【止盈止损参考（基于ATR和技术位）】")
    atr_val = analysis.get("atr", {}).get("value", price * 0.03)
    supports_vals = [v for _, v in analysis.get("supports", [])]
    resistances_vals = [v for _, v in analysis.get("resistances", [])]

    # 止损: 最近支撑位下方 或 2*ATR
    nearest_support = supports_vals[0] if supports_vals else price * 0.93
    stop_loss_tech = max(nearest_support - atr_val * 0.5, price * 0.9)  # 技术止损
    stop_loss_tight = price - atr_val * 2  # 紧止损
    print(f"    紧止损 (2×ATR):        {stop_loss_tight:.2f} ({(stop_loss_tight/price - 1)*100:+.1f}%)")
    print(f"    技术止损 (支撑下方):   {stop_loss_tech:.2f} ({(stop_loss_tech/price - 1)*100:+.1f}%)")

    nearest_resistance = resistances_vals[0] if resistances_vals else price * 1.07
    print(f"    第一止盈 (近压力位):   {nearest_resistance:.2f} ({(nearest_resistance/price - 1)*100:+.1f}%)")
    if len(resistances_vals) > 1:
        print(f"    第二止盈 (远压力位):   {resistances_vals[1]:.2f} ({(resistances_vals[1]/price - 1)*100:+.1f}%)")

    # ---- DeepSeek AI 分析 ----
    print(f"\n  【AI 辅助分析】")
    _run_deepseek_analysis(price, analysis, xueqiu_posts)

    print(f"\n{'=' * 70}")
    print(f"  免责声明: 以上分析仅供参考，不构成投资建议。股市有风险，投资需谨慎。")
    print(f"{'=' * 70}\n")


def _run_deepseek_analysis(price: float, analysis: dict, posts: list[dict]) -> None:
    """使用 DeepSeek 进行综合研判"""
    try:
        from xueqiu.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
        from openai import OpenAI

        if not DEEPSEEK_API_KEY:
            print("    (未配置 DEEPSEEK_API_KEY，跳过 AI 分析)")
            return
    except Exception:
        print("    (无法加载 DeepSeek 配置)")
        return

    # 构建技术面摘要
    tech_summary = json.dumps({
        "price": price,
        "rsi": analysis.get("rsi"),
        "macd": analysis.get("macd"),
        "kdj": analysis.get("kdj"),
        "bollinger": analysis.get("bollinger"),
        "ma_deviation": analysis.get("ma_deviation"),
        "signals": analysis.get("signals"),
        "warnings": analysis.get("warnings"),
        "levels": analysis.get("levels"),
    }, ensure_ascii=False)

    # 构建雪球讨论摘要
    posts_text = ""
    for p in posts[:8]:
        posts_text += f"- [{p.get('created_at', '?')[:10]}] {p['user_name']}: {p['text'][:300]}\n"
    if not posts_text:
        posts_text = "（暂无雪球讨论数据）"

    prompt = f"""你是一个经验丰富的A股技术分析师。请基于以下数据，对{STOCK_NAME}({STOCK_CODE})进行分析：

## 技术面数据
{tech_summary}

## 雪球社区近期讨论
{posts_text}

## 分析要求
1. **当前位置判断**: 该股目前处于什么位置（底部区域/上涨中继/顶部区域/下跌中继）？
2. **关键点位**: 给出具体的买入参考位、止盈位、止损位
3. **操作建议**: 如果有持仓，应该怎么操作？如果没有，现在是否适合介入？
4. **风险提示**: 需要注意的风险因素
5. **综合评分**: 1-10分，当前价位的入场性价比评分

请用简洁的要点形式输出，每条建议注明依据。控制在400字以内。"""

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个严谨的A股技术分析师，擅长结合技术指标和市场情绪给出客观判断。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        result = resp.choices[0].message.content.strip()
        print(f"    {result}")
    except Exception as exc:
        print(f"    DeepSeek 调用失败: {exc}")
        print(f"    (将以纯技术指标为准)")


# ===================================================================
# Main
# ===================================================================


def main():
    print(f"\n🔍 正在分析 {STOCK_NAME}({STOCK_CODE})...")

    # ---- Step 1: 获取K线数据 ----
    print("  [1/3] 获取日K线数据...")
    klines_raw = fetch_sina_daily_kline(SINA_SYMBOL, days=250)
    if not klines_raw:
        print("  ❌ 无法获取K线数据，请检查网络连接")
        return

    # 解析为结构化数据
    klines = []
    for k in klines_raw:
        try:
            klines.append({
                "date": k["day"],
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k["volume"]),
            })
        except (KeyError, ValueError):
            continue

    if len(klines) < 30:
        print(f"  ❌ 数据不足 (仅 {len(klines)} 根K线)，至少需要30根")
        return

    print(f"  ✅ 获取到 {len(klines)} 根日K线 ({klines[0]['date']} ~ {klines[-1]['date']})")

    # ---- Step 2: 计算技术指标 ----
    print("  [2/3] 计算技术指标...")
    close = [k["close"] for k in klines]
    high = [k["high"] for k in klines]
    low = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]
    dates = [k["date"] for k in klines]
    latest_price = close[-1]

    analysis = analyze_position(close, high, low, volumes, dates, latest_price)
    print(f"  ✅ 技术指标计算完成 (信号: {len(analysis['signals'])}多, 警示: {len(analysis['warnings'])}空)")

    # ---- Step 3: 雪球情绪 ----
    print("  [3/3] 抓取雪球社区讨论...")
    xueqiu_posts = fetch_xueqiu_posts_simple(STOCK_CODE, max_pages=3)
    print(f"  ✅ 获取到 {len(xueqiu_posts)} 条雪球讨论")

    # ---- 输出报告 ----
    print_report(klines, analysis, xueqiu_posts)

    # ---- 保存JSON ----
    output = {
        "generated_at": datetime.now().isoformat(),
        "stock": {"name": STOCK_NAME, "code": STOCK_CODE},
        "latest_price": latest_price,
        "latest_date": dates[-1],
        "analysis": analysis,
        "xueqiu_posts_count": len(xueqiu_posts),
        "xueqiu_posts": xueqiu_posts[:20],
    }
    # 清理不可序列化字段
    output["analysis"].pop("signals", None)
    output["analysis"].pop("warnings", None)
    output["analysis"].pop("summary", None)
    output["analysis"].pop("supports", None)
    output["analysis"].pop("resistances", None)

    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / f"chengchang_analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"📁 原始数据已保存: {json_path}")


if __name__ == "__main__":
    main()
