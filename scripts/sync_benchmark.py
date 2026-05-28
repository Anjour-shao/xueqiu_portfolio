"""用 TuShare 将基准指数写入 benchmark 表（新浪接口不可用时的临时方案）。

需 backend/.env 中配置 TUSHARE_API_KEY。

用法:
    cd backend
    python ../scripts/sync_benchmark.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.storage.db import init_db
from xueqiu.sync.sync_tushare_aux import sync_benchmark_from_tushare


def main() -> None:
    init_db()
    count = sync_benchmark_from_tushare()
    print(f"完成，写入约 {count} 条 benchmark 记录。")


if __name__ == "__main__":
    main()
