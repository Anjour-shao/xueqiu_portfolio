"""同步所有 ZH 组合的雪球官方日净值到 cube_nav_points 表。

看板净值 K 线依赖此数据。调仓同步后也会自动触发单账户同步。

用法:
    cd backend
    python ../scripts/sync_cube_nav.py

需要有效 Cookie: data/xueqiu_cookie.txt 或环境变量 XUEQIU_COOKIE。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.sync.sync_cube_nav import sync_all_cube_nav

if __name__ == "__main__":
    result = sync_all_cube_nav()
    print(result["message"])
    for item in result.get("results", []):
        if item.get("ok"):
            print(
                f"  [{item['account_code']}] {item.get('account_name')} "
                f"→ {item.get('point_count')} 点，最新 {item.get('latest_date')}"
            )
        else:
            print(f"  [{item.get('account_code')}] 失败: {item.get('error')}")
