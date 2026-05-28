"""组合总览页聚合统计。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from xueqiu.domain.data_freshness import load_data_freshness
from xueqiu.domain.overview_light import load_portfolios_overview_items


def _parse_trade_day(trade_time: str | None) -> str | None:
    if not trade_time:
        return None
    return trade_time[:10]


def load_portfolios_overview_stats() -> dict[str, Any]:
    items = load_portfolios_overview_items()
    freshness = load_data_freshness()
    today = date.today().isoformat()

    with_nav = [i for i in items if i.get("cum_return_pct") is not None]
    avg_cum = (
        round(sum(float(i["cum_return_pct"]) for i in with_nav) / len(with_nav), 2) if with_nav else None
    )
    beat_bench = [i for i in items if (i.get("excess_return_pct") or 0) > 0]
    traded_today = [
        i for i in items if _parse_trade_day(i.get("latest_trade_time")) == today
    ]

    sorted_by_cum = sorted(
        items,
        key=lambda row: float(row.get("cum_return_pct") or -1e9),
        reverse=True,
    )
    top3 = sorted_by_cum[:3]
    bottom3 = list(reversed(sorted_by_cum[-3:])) if len(sorted_by_cum) >= 3 else []

    stale_threshold = (date.today() - timedelta(days=3)).strftime("%Y%m%d")
    nav_max_raw = freshness.get("cube_nav", {}).get("latest_date_max_raw")

    watchlist: list[dict[str, Any]] = []
    for item in items:
        reasons: list[str] = []
        nav_raw = item.get("latest_nav_date")
        if nav_raw:
            nav_compact = nav_raw.replace("-", "")
            if nav_max_raw and nav_compact < nav_max_raw:
                reasons.append("净值落后")
        else:
            reasons.append("无官方净值")

        trade_day = _parse_trade_day(item.get("latest_trade_time"))
        if trade_day:
            try:
                if (date.today() - datetime.strptime(trade_day, "%Y-%m-%d").date()).days >= 7:
                    reasons.append("7日未调仓")
            except ValueError:
                pass

        if reasons:
            watchlist.append(
                {
                    "account_code": item["account_code"],
                    "account_name": item["account_name"],
                    "reasons": reasons,
                    "latest_nav_date": item.get("latest_nav_date"),
                    "cum_return_pct": item.get("cum_return_pct"),
                }
            )

    watchlist.sort(key=lambda x: len(x["reasons"]), reverse=True)

    return {
        "summary": {
            "portfolio_count": len(items),
            "avg_cum_return_pct": avg_cum,
            "beat_benchmark_count": len(beat_bench),
            "beat_benchmark_ratio": round(len(beat_bench) / len(items), 2) if items else 0,
            "traded_today_count": len(traded_today),
            "freshness": freshness,
        },
        "top_performers": top3,
        "bottom_performers": bottom3,
        "watchlist": watchlist[:8],
        "items": items,
    }
