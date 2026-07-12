"""本地预览：拉取指定组合最新一批真实调仓并生成简报图（不写 state）。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "backend" / ".env")
load_dotenv(REPO_ROOT / ".env")
if not os.getenv("ACCOUNT_DASHBOARD_DATABASE_URL", "").strip():
    os.environ["ACCOUNT_DASHBOARD_DATABASE_URL"] = "sqlite:///:memory:"

from digest import render as digest_render
from daily_portfolio_digest import (
    PORTFOLIO_NAMES,
    PortfolioUpdate,
    RebalanceBatchDigest,
    call_deepseek_summary,
    fetch_stock_comments,
    send_dingtalk_digest,
)
from xueqiu.integrations.xueqiu.client import XueQiuApiClient
from xueqiu.integrations.xueqiu.portfolio import fetch_portfolio_rebalance


def _build_update_from_latest(client: XueQiuApiClient, portfolio_id: str, *, with_ai: bool) -> PortfolioUpdate:
    pid = portfolio_id.strip().upper()
    print(f"拉取 {pid} 最新一批调仓…")
    crawled = fetch_portfolio_rebalance(pid, client=client)
    records = crawled.get("records") or []
    rebalance_time = crawled.get("rebalance_time", "")
    portfolio_name = PORTFOLIO_NAMES.get(pid) or crawled.get("portfolio_name") or pid
    print(f"  组合: {portfolio_name}  时间: {rebalance_time}  记录: {len(records)} 条")

    ai_summaries: dict[str, str] = {}
    if with_ai:
        for record in records:
            if record.get("action") != "买入":
                continue
            code = str(record.get("code") or "").strip()
            if not code or code in ai_summaries:
                continue
            stock_name = record.get("name", code)
            print(f"  DeepSeek 分析: {stock_name} ({code})…")
            comments = fetch_stock_comments(client, code)
            ai_summaries[code] = call_deepseek_summary(str(stock_name), comments, verbose=True)

    return PortfolioUpdate(
        portfolio_id=pid,
        portfolio_name=str(portfolio_name),
        batches=[
            RebalanceBatchDigest(
                rebalance_time=rebalance_time,
                records=records,
                ai_summaries=ai_summaries,
            )
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="预览指定组合最新调仓简报（不写 state）")
    parser.add_argument(
        "--portfolio",
        default="ZH3393223",
        help="组合 ID，默认 ZH3393223（血战到底）",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="跳过 DeepSeek 舆情",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="推送到钉钉（默认只生成本地 PNG）",
    )
    args = parser.parse_args()

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    pid = args.portfolio.strip().upper()
    print(f"=== 真实最新调仓预览 · {pid} ({run_time}) ===")

    client = XueQiuApiClient()
    update = _build_update_from_latest(client, pid, with_ai=not args.no_ai)

    if args.push:
        send_dingtalk_digest(
            run_time=run_time,
            updates=[update],
        )
    else:
        context = digest_render.build_report_context(
            run_time=run_time,
            simulate_note=f"真实调仓预览 · {update.portfolio_name}",
            updates=[update],
        )
        html = digest_render.render_report_html(context)
        safe = run_time.replace(":", "").replace(" ", "_").replace("-", "")[:12]
        out_path = digest_render.OUTPUT_DIR / f"latest_rebalance_{pid}_{safe}.png"
        digest_render.render_html_to_png(html, out_path)
        print(f"\n预览图已保存:\n  {out_path}")

    print("=== 完成 ===")


if __name__ == "__main__":
    main()
