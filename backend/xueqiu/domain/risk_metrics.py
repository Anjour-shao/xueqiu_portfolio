"""基于净值曲线计算风险指标（官方日频净值）。"""

from __future__ import annotations

import math
import statistics
from typing import Any


def compute_risk_metrics(curve: list[dict[str, Any]]) -> dict[str, Any]:
    if len(curve) < 2:
        return {}

    nav_series: list[tuple[str, float]] = []
    daily_returns: list[float] = []

    for point in curve:
        trade_date = str(point.get("trade_date") or "")[:10]
        nav = point.get("nav")
        if isinstance(nav, (int, float)) and nav > 0:
            nav_series.append((trade_date, float(nav)))
        pr = point.get("period_return_pct")
        if pr is not None:
            try:
                daily_returns.append(float(pr))
            except (TypeError, ValueError):
                pass

    if len(nav_series) < 2:
        return {}

    peak = nav_series[0][1]
    peak_date = nav_series[0][0]
    max_dd = 0.0
    dd_start = peak_date
    dd_end = peak_date
    current_peak_date = peak_date

    for trade_date, nav in nav_series:
        if nav > peak:
            peak = nav
            current_peak_date = trade_date
        dd = (nav / peak - 1.0) * 100 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            dd_start = current_peak_date
            dd_end = trade_date

    first_nav = nav_series[0][1]
    last_nav = nav_series[-1][1]
    n_days = len(nav_series)
    total_ret = last_nav / first_nav - 1.0 if first_nav > 0 else 0.0
    annual_ret = ((1.0 + total_ret) ** (252.0 / max(n_days, 1)) - 1.0) * 100 if n_days > 0 else 0.0

    volatility_pct: float | None = None
    if len(daily_returns) >= 2:
        volatility_pct = round(statistics.stdev(daily_returns) * math.sqrt(252), 2)

    sharpe_ratio: float | None = None
    if volatility_pct is not None and volatility_pct > 1e-9:
        sharpe_ratio = round(annual_ret / volatility_pct, 2)

    calmar_ratio: float | None = None
    if max_dd < -1e-9:
        calmar_ratio = round(annual_ret / abs(max_dd), 2)

    positive_day_ratio: float | None = None
    if daily_returns:
        positive_day_ratio = round(sum(1 for r in daily_returns if r > 0) / len(daily_returns) * 100, 1)

    return {
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_start": dd_start,
        "max_drawdown_end": dd_end,
        "volatility_pct": volatility_pct,
        "sharpe_ratio": sharpe_ratio,
        "calmar_ratio": calmar_ratio,
        "positive_day_ratio": positive_day_ratio,
    }
