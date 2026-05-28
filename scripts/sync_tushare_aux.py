"""用 TuShare 同步基准指数等辅助数据到 benchmark 表（可选）。

看板超额收益、基准对比线需要 benchmark 数据。需配置 TUSHARE_API_KEY。

用法:
    cd backend
    python ../scripts/sync_tushare_aux.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.sync.sync_tushare_aux import main

if __name__ == "__main__":
    main()
