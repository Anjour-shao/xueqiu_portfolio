"""雪球 API 连通性测试：个股讨论、用户动态、组合调仓、官方净值。

用法:
    cd backend
    python ../scripts/test_xueqiu_api.py
    python ../scripts/test_xueqiu_api.py --portfolio ZH3207026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.portfolio import fetch_portfolio_rebalance
from xueqiu.integrations.xueqiu.posts import (
    fetch_stock_posts,
    fetch_stock_posts_page,
    fetch_user_posts,
    fetch_user_timeline_page,
    resolve_user_id,
)


def safe_print(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def print_posts(title: str, posts, limit: int = 5) -> None:
    safe_print(f"\n{'=' * 60}")
    safe_print(title)
    safe_print(f"{'=' * 60}")
    if not posts:
        safe_print("(无数据)")
        return
    for idx, post in enumerate(posts[:limit], 1):
        preview = post.text.replace("\n", " ")
        if len(preview) > 100:
            preview = preview[:100] + "..."
        safe_print(f"{idx}. [{post.created_at}] @{post.user_name}")
        safe_print(f"   {preview}")
    if len(posts) > limit:
        safe_print(f"... 还有 {len(posts) - limit} 条未显示")


def test_stock(client: XueQiuApiClient, symbol: str, pages: int, size: int) -> bool:
    safe_print(f"\n[测试] 个股讨论区: {symbol}")
    try:
        first_page, has_more = fetch_stock_posts_page(client, symbol, page=1, size=size)
        safe_print(f"  第1页: {len(first_page)} 条, has_more={has_more}")
        if pages > 1:
            all_posts = fetch_stock_posts(client, symbol, max_pages=pages, page_size=size)
            print_posts(f"个股 {symbol} 共 {len(all_posts)} 条", all_posts)
        else:
            print_posts(f"个股 {symbol} 第1页", first_page)
        return len(first_page) > 0
    except XueQiuApiError as exc:
        safe_print(f"  失败: {exc}")
        return False


def test_user(client: XueQiuApiClient, target: str, pages: int, size: int) -> bool:
    safe_print(f"\n[测试] 用户发言: {target}")
    try:
        user_id = int(target) if target.isdigit() else resolve_user_id(client, target)
        safe_print(f"  用户 ID: {user_id}")
        first_page, has_more = fetch_user_timeline_page(client, user_id, page=1, count=size)
        safe_print(f"  第1页: {len(first_page)} 条, has_more={has_more}")
        if pages > 1:
            all_posts = fetch_user_posts(client, user_id, max_pages=pages, page_size=size)
            print_posts(f"用户 {target} 共 {len(all_posts)} 条", all_posts)
        else:
            print_posts(f"用户 {target} 第1页", first_page)
        return len(first_page) > 0
    except (XueQiuApiError, ValueError) as exc:
        safe_print(f"  失败: {exc}")
        return False


def test_portfolio(client: XueQiuApiClient, portfolio_id: str) -> bool:
    safe_print(f"\n[测试] 组合调仓: {portfolio_id}")
    try:
        data = fetch_portfolio_rebalance(portfolio_id, client=client)
        safe_print(f"  组合: {data['portfolio_name']} ({data['portfolio_id']})")
        safe_print(f"  最新调仓: {data['rebalance_time']}，共 {len(data['trades'])} 条")
        for idx, record in enumerate(data["records"][:3], 1):
            safe_print(
                f"  {idx}. {record['action']} {record['name']} "
                f"({record['code']}) {record['weight_change']}"
            )
        return len(data["trades"]) > 0
    except Exception as exc:
        safe_print(f"  失败: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="雪球 API 爬虫测试")
    parser.add_argument("--stock", default="SH600519", help="测试股票代码")
    parser.add_argument("--user", default="不明真相的群众", help="测试用户 ID 或昵称")
    parser.add_argument("--portfolio", default="", help="测试组合 ID，如 ZH3207026")
    parser.add_argument("--stock-pages", type=int, default=2)
    parser.add_argument("--user-pages", type=int, default=2)
    parser.add_argument("--size", type=int, default=10)
    args = parser.parse_args()

    safe_print("雪球 API 测试（requests + Cookie）")
    safe_print(f"Cookie: {ROOT / 'data' / 'xueqiu_cookie.txt'}")

    try:
        client = XueQiuApiClient()
    except RuntimeError as exc:
        safe_print(f"\n错误: {exc}")
        safe_print("\n请先运行: python ../scripts/xueqiu_login.py")
        sys.exit(1)

    stock_ok = test_stock(client, args.stock, args.stock_pages, args.size)
    user_ok = test_user(client, args.user, args.user_pages, args.size)
    portfolio_ok = True
    if args.portfolio:
        portfolio_ok = test_portfolio(client, args.portfolio.strip().upper())

    safe_print(f"\n{'=' * 60}")
    safe_print("测试结果:")
    safe_print(f"  个股讨论 ({args.stock}): {'通过' if stock_ok else '失败'}")
    safe_print(f"  用户发言 ({args.user}): {'通过' if user_ok else '失败'}")
    if args.portfolio:
        safe_print(f"  组合调仓 ({args.portfolio}): {'通过' if portfolio_ok else '失败'}")
    safe_print(f"{'=' * 60}")

    ok = stock_ok and user_ok and portfolio_ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
