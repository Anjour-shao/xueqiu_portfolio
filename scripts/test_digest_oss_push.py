"""测试 OSS 图床上传 + 钉钉图片推送（含抄作业调仓方案区块）。"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DAILY = ROOT / "daily_digest"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(DAILY) not in sys.path:
    sys.path.insert(0, str(DAILY))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
load_dotenv(ROOT / ".env")

from digest import render as digest_render
from daily_portfolio_digest import (
    MY_ACCOUNT,
    MY_HOLDINGS,
    PortfolioUpdate,
    RebalanceBatchDigest,
    _compute_copy_plan_safe,
    _load_my_holdings_config,
    build_account_summary,
    fetch_holding_quotes,
    load_state,
    send_dingtalk_digest,
)


def main() -> None:
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== OSS + 钉钉测试 ({run_time}) ===")

    holdings_cfg, account_cfg = _load_my_holdings_config()
    MY_ACCOUNT.update(account_cfg)

    state = load_state()
    quotes = fetch_holding_quotes(holdings_cfg, state=state) if holdings_cfg else []
    account = build_account_summary(quotes, state) if quotes else None

    # 模拟一条调仓更新
    fake_update = PortfolioUpdate(
        portfolio_id="ZH3337164",
        portfolio_name="三年10倍（测试）",
        batches=[
            RebalanceBatchDigest(
                rebalance_time=run_time,
                records=[
                    {
                        "action": "买入",
                        "name": "测试标的",
                        "code": "SH600519",
                        "price": "1800.00",
                        "weight_change": "10% → 15%",
                    }
                ],
                ai_summaries={},
            )
        ],
    )

    copy_plan = _compute_copy_plan_safe([fake_update])
    print(f"调仓方案: {len(copy_plan.get('actions') or []) if copy_plan else 0} 笔")

    send_dingtalk_digest(
        run_time=run_time,
        account=account,
        quotes=quotes if quotes else None,
        updates=[fake_update],
        include_holdings=bool(quotes),
        copy_plan=copy_plan,
    )
    print("=== 完成，请查看钉钉群 ===")


if __name__ == "__main__":
    main()
