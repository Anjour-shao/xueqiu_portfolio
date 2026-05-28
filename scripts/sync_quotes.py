"""批量同步持仓标的的新浪后复权价到 quote_points 表。

个股浮动盈亏、持仓成本、grouped_stats 依赖此行情。导入/调仓后 API 也会自动触发。

用法:
    cd backend
    python ../scripts/sync_quotes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.sync.sync_quotes import run_sync

if __name__ == "__main__":
    run_sync()
