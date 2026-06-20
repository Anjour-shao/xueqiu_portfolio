"""挖组合扩展能力 live 探测（不写入 DB、不改前端）。

用法（在 backend 目录）:
  python ../scripts/test_discovery_expand_live.py
  python ../scripts/test_discovery_expand_live.py --seed 2116089912
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from xueqiu.domain.discovery_hot_symbols import (
    STOCK_HOT_USER_PAGE_SIZE,
    STOCK_HOT_USER_SAMPLE_SIZE,
    VOLUME_TOP100_SYMBOLS,
    VOLUME_TOP100_TRADE_DATE,
)
from xueqiu.integrations.xueqiu.auth import WATCHLIST_CANARY_UID, load_cookie
from xueqiu.integrations.xueqiu.client import XueQiuApiClient
from xueqiu.integrations.xueqiu.social import (
    fetch_stock_hot_users,
    fetch_user_following_page,
    probe_watchlist_yield,
)


def _pct(num: int, den: int) -> str:
    if den <= 0:
        return "0%"
    return f"{100 * num / den:.0f}%"


def probe_following(api: XueQiuApiClient, seed_uid: str) -> dict:
    users, max_page = fetch_user_following_page(seed_uid, page=1, client=api)
    with_wl = 0
    wl_total = 0
    samples: list[dict] = []
    for u in users:
        wl, meta = probe_watchlist_yield(u.uid, client=api)
        if wl > 0:
            with_wl += 1
            wl_total += wl
        if len(samples) < 5:
            samples.append(
                {
                    "uid": u.uid,
                    "name": u.screen_name,
                    "followers": u.followers_count,
                    "watchlist": wl,
                    "meta": meta,
                }
            )
    return {
        "seed_uid": seed_uid,
        "page1_users": len(users),
        "max_page": max_page,
        "with_watchlist": with_wl,
        "watchlist_hit_rate": _pct(with_wl, len(users)),
        "avg_watchlist_when_hit": round(wl_total / with_wl, 1) if with_wl else 0,
        "samples": samples,
    }


def probe_stock_hot(api: XueQiuApiClient, symbols: list[str]) -> dict:
    per_symbol: list[dict] = []
    all_users: dict[int, dict] = {}
    for sym in symbols:
        users = fetch_stock_hot_users(
            sym, start=0, count=STOCK_HOT_USER_PAGE_SIZE, client=api
        )
        sym_with_wl = 0
        for u in users:
            wl, meta = probe_watchlist_yield(u.uid, client=api)
            prev = all_users.get(u.uid)
            if prev is None or wl > prev["watchlist"]:
                all_users[u.uid] = {
                    "uid": u.uid,
                    "name": u.screen_name,
                    "followers": u.followers_count,
                    "watchlist": wl,
                    "meta": meta,
                    "symbol": sym,
                }
            if wl > 0:
                sym_with_wl += 1
        per_symbol.append(
            {
                "symbol": sym,
                "hot_users": len(users),
                "with_watchlist": sym_with_wl,
            }
        )
    ranked = sorted(all_users.values(), key=lambda x: (-x["watchlist"], -x["followers"]))
    return {
        "symbols_tested": len(symbols),
        "unique_users": len(all_users),
        "with_watchlist": sum(1 for x in all_users.values() if x["watchlist"] > 0),
        "per_symbol": per_symbol,
        "top_users": ranked[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default=WATCHLIST_CANARY_UID, help="关注链种子 uid")
    parser.add_argument(
        "--hot-sample",
        type=int,
        default=STOCK_HOT_USER_SAMPLE_SIZE,
        help="抽几只 Top100 股票测 hot user",
    )
    args = parser.parse_args()

    if not load_cookie().strip():
        print("ERROR: 缺少 data/xueqiu_cookie.txt", file=sys.stderr)
        return 1

    api = XueQiuApiClient()
    # 优先测新兴/高成交额前排 + 用户点名的票
    priority = ["SZ300308", "SZ001309"]
    sample: list[str] = []
    for sym in priority + VOLUME_TOP100_SYMBOLS:
        if sym not in sample:
            sample.append(sym)
        if len(sample) >= max(3, args.hot_sample):
            break

    report = {
        "trade_date": VOLUME_TOP100_TRADE_DATE,
        "symbol_pool_size": len(VOLUME_TOP100_SYMBOLS),
        "following_chain": probe_following(api, str(args.seed)),
        "stock_hot_users": probe_stock_hot(api, sample),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
