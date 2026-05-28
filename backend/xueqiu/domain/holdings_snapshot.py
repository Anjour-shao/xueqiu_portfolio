"""按调仓记录回放权重，为净值曲线各交易日生成持仓快照与当日调仓。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol


class TradeLike(Protocol):
    trade_time: str
    stock_name: str
    ts_code: str
    action: str
    from_weight: float
    to_weight: float


def _date_key(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) >= 10 and raw[4] == "-":
        return raw[:10].replace("-", "")
    if len(raw) >= 8 and raw.isdigit():
        return raw[:8]
    return raw.replace("-", "")[:8]


def _display_date(date_key: str) -> str:
    if len(date_key) == 8 and date_key.isdigit():
        return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    return date_key


def build_holdings_snapshots(
    trades: list[TradeLike],
    curve_dates: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not curve_dates:
        return {}

    sorted_trades = sorted(trades, key=lambda t: (t.trade_time, t.ts_code))
    sorted_dates = sorted({_date_key(d) for d in curve_dates if d})

    weights: dict[str, float] = {}
    names: dict[str, str] = {}
    trade_idx = 0
    result: dict[str, list[dict[str, Any]]] = {}

    for dk in sorted_dates:
        while trade_idx < len(sorted_trades) and _date_key(sorted_trades[trade_idx].trade_time) <= dk:
            trade = sorted_trades[trade_idx]
            code = str(trade.ts_code)
            names[code] = str(trade.stock_name)
            weights[code] = float(trade.to_weight)
            trade_idx += 1

        holdings = [
            {"stock_name": names[code], "weight": round(weights[code], 2)}
            for code in weights
            if weights[code] > 1e-6
        ]
        holdings.sort(key=lambda item: item["weight"], reverse=True)
        result[dk] = holdings

    return result


def build_trades_today_map(trades: list[TradeLike]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in sorted(trades, key=lambda t: (t.trade_time, t.ts_code)):
        display = _display_date(_date_key(trade.trade_time))
        grouped[display].append(
            {
                "stock_name": str(trade.stock_name),
                "action": str(trade.action),
                "from_weight": round(float(trade.from_weight), 2),
                "to_weight": round(float(trade.to_weight), 2),
            }
        )
    return dict(grouped)


def attach_curve_extras(
    curve: list[dict[str, Any]],
    trades: list[TradeLike],
) -> list[dict[str, Any]]:
    if not curve:
        return curve
    holdings_map = build_holdings_snapshots(trades, [str(p.get("trade_date", "")) for p in curve])
    trades_map = build_trades_today_map(trades)
    enriched: list[dict[str, Any]] = []
    for point in curve:
        row = dict(point)
        dk = _date_key(str(row.get("trade_date", "")))
        row["holdings"] = holdings_map.get(dk, [])
        row["trades_today"] = trades_map.get(str(row.get("trade_date", "")), [])
        enriched.append(row)
    return enriched
