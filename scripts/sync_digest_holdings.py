"""手动导出一次 Digest 持仓快照（正常由前端保存时自动触发，一般不必运行）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

from xueqiu.domain.digest_holdings_export import export_digest_holdings_snapshot
from xueqiu.storage.db import init_db

if __name__ == "__main__":
    init_db()
    export_digest_holdings_snapshot()
