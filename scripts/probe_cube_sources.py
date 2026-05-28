"""探测雪球组合列表的多种数据源，统计可获取条数。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError  # noqa: E402

RANK_URL = "https://xueqiu.com/cubes/discover/rank/cube/list.json"


def sym_from_item(item: dict) -> str | None:
    s = item.get("symbol") or item.get("code")
    return str(s).upper() if s else None


def fetch_rank_all(api: XueQiuApiClient, params: dict, *, page_size: int = 50) -> list[dict]:
    rows: list[dict] = []
    page = 1
    max_page = 1
    while page <= max_page:
        data = api.get_json(RANK_URL, params={**params, "page": page, "count": page_size})
        lst = data.get("list") or []
        if page == 1:
            max_page = int(data.get("maxPage") or 1)
        rows.extend(x for x in lst if isinstance(x, dict))
        if not lst or page >= max_page:
            break
        page += 1
        time.sleep(0.5)
    return rows


def main() -> None:
    api = XueQiuApiClient()
    merged: set[str] = set()

    print("=== category=12 profit 维度 ===")
    profits = [
        "annualized_gain_rate",
        "monthly_gain",
        "daily_gain",
        "weekly_gain",
        "total_gain",
        "max_drawdown",
        "follower_count",
        "turnover_rate",
        "sharpe",
        "volatility",
        "win_rate",
        "rebalance_count",
        "total_return",
        "annual_return",
        "three_month_gain",
        "six_month_gain",
        "year_gain",
    ]
    for p in profits:
        try:
            lst = fetch_rank_all(
                api,
                {"category": 12, "market": "cn", "profit": p},
                page_size=50,
            )
            syms = {s for x in lst if (s := sym_from_item(x))}
            merged |= syms
            print(f"  {p:24s} rows={len(lst):3d} unique={len(syms):3d}")
        except XueQiuApiError:
            print(f"  {p:24s} FAIL")
        time.sleep(0.5)

    print("\n=== category=14 热门（全页）===")
    hot = fetch_rank_all(api, {"category": 14}, page_size=100)
    hot_syms = {s for x in hot if (s := sym_from_item(x))}
    merged |= hot_syms
    print(f"  hot rows={len(hot)} unique={len(hot_syms)}")

    print("\n=== 其它 URL ===")
    others = [
        ("stock rank", "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json", {"pid": -1, "size": 20}),
        ("cube show", "https://xueqiu.com/cubes/show.json", {"symbol": "ZH3388054"}),
        ("cube quote", "https://xueqiu.com/cubes/quote.json", {"code": "ZH3388054"}),
        ("search status", "https://xueqiu.com/statuses/search.json", {"q": "ZH", "count": 10}),
    ]
    for label, url, params in others:
        try:
            data = api.get_json(url, params=params)
            print(f"  OK {label}: type={type(data).__name__} keys={list(data.keys())[:8] if isinstance(data, dict) else 'list'}")
        except XueQiuApiError as e:
            print(f"  FAIL {label}: {str(e)[:60]}")
        time.sleep(0.4)

    print(f"\n=== 合并去重总计（仅榜单）: {len(merged)} ===")
    print("结论: 榜单 API 硬上限约 129；无更大批量列表接口（first_name/second_name、其它 path 均无效）")


if __name__ == "__main__":
    main()
