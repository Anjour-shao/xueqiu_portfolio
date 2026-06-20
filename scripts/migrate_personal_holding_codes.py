"""迁移 personal_holdings 中的 6 位代码为 SH/SZ 前缀格式。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from sqlalchemy import select

from xueqiu.domain.personal_account import _norm_code
from xueqiu.storage.db import get_conn, init_db, personal_holdings_table


def main() -> None:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(select(personal_holdings_table)).mappings().all()
        for row in rows:
            old = str(row["ts_code"])
            try:
                new = _norm_code(old)
            except ValueError:
                print(f"skip invalid: {old}")
                continue
            if new == old:
                continue
            exists = conn.execute(
                select(personal_holdings_table).where(
                    personal_holdings_table.c.account_id == row["account_id"],
                    personal_holdings_table.c.ts_code == new,
                )
            ).first()
            if exists:
                conn.execute(
                    personal_holdings_table.delete().where(
                        personal_holdings_table.c.account_id == row["account_id"],
                        personal_holdings_table.c.ts_code == old,
                    )
                )
                print(f"removed duplicate old code {old} (already have {new})")
            else:
                conn.execute(
                    personal_holdings_table.update()
                    .where(
                        personal_holdings_table.c.account_id == row["account_id"],
                        personal_holdings_table.c.ts_code == old,
                    )
                    .values(ts_code=new)
                )
                print(f"{old} -> {new}")
    print("done")


if __name__ == "__main__":
    main()
