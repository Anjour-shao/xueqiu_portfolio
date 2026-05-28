from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, PrimaryKeyConstraint, String, Table, Text, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

from xueqiu.config import DATABASE_URL

metadata = MetaData()

accounts_table = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_code", String(64), nullable=False, unique=True),
    Column("account_name", String(255), nullable=False),
)

rebalance_trades_table = Table(
    "rebalance_trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False, index=True),
    Column("trade_time", String(32), nullable=False, index=True),
    Column("stock_name", String(255), nullable=False),
    Column("ts_code", String(32), nullable=False, index=True),
    Column("from_weight", Float, nullable=False),
    Column("to_weight", Float, nullable=False),
    Column("weight_delta", Float, nullable=False),
    Column("action", String(32), nullable=False),
    Column("price", Float, nullable=True),
    Column("price_hfq", Float, nullable=True),
    Column("raw_block", Text, nullable=False),
    UniqueConstraint(
        "account_id",
        "trade_time",
        "ts_code",
        "from_weight",
        "to_weight",
        "price",
        name="uq_rebalance_trade_identity",
    ),
)

quote_points_table = Table(
    "quote_points",
    metadata,
    Column("ts_code", String(16), nullable=False),
    Column("trade_date", String(8), nullable=False),
    Column("adj_factor", Float, nullable=False),
    Column("close_hfq", Float, nullable=True),
    PrimaryKeyConstraint("ts_code", "trade_date", name="pk_quote_points"),
)

benchmark_table = Table(
    "benchmark",
    metadata,
    Column("ts_code", String(16), nullable=False),
    Column("trade_date", String(8), nullable=False),
    Column("close", Float, nullable=False),
    Column("pct_chg", Float, nullable=True),
    Column("cum_return_pct", Float, nullable=True),
    PrimaryKeyConstraint("ts_code", "trade_date", name="pk_benchmark"),
)

cube_nav_points_table = Table(
    "cube_nav_points",
    metadata,
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("trade_date", String(8), nullable=False),
    Column("nav_value", Float, nullable=False),
    Column("cum_return_pct", Float, nullable=False),
    Column("synced_at", DateTime, nullable=False),
    PrimaryKeyConstraint("account_id", "trade_date", name="pk_cube_nav_points"),
)

# 雪球发现页/榜单 API 拉取的组合目录（代码 + 名称，与 accounts 关注列表独立）
cube_catalog_table = Table(
    "cube_catalog",
    metadata,
    Column("account_code", String(64), primary_key=True),
    Column("account_name", String(255), nullable=False),
    Column("first_seen_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("discovered", Boolean, nullable=False, server_default=text("0")),
    Column("discovered_at", DateTime, nullable=True),
)

engine: Engine = create_engine(DATABASE_URL, future=True)


def _ensure_accounts_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    account_columns = {col["name"] for col in inspector.get_columns("accounts")}
    account_indexes = {idx["name"] for idx in inspector.get_indexes("accounts")}
    account_uniques = {uc["name"] for uc in inspector.get_unique_constraints("accounts")}

    if "account_code" not in account_columns:
        conn.execute(text("ALTER TABLE accounts ADD COLUMN account_code VARCHAR(64) NULL"))

    conn.execute(text("UPDATE accounts SET account_code = CAST(id AS CHAR) WHERE account_code IS NULL OR account_code = ''"))
    conn.execute(text("ALTER TABLE accounts MODIFY COLUMN account_code VARCHAR(64) NOT NULL"))

    if "uq_accounts_code" not in account_uniques:
        conn.execute(text("ALTER TABLE accounts ADD CONSTRAINT uq_accounts_code UNIQUE (account_code)"))

    if "idx_accounts_name" not in account_indexes:
        conn.execute(text("CREATE INDEX idx_accounts_name ON accounts(account_name)"))


def _ensure_rebalance_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    trade_columns = {col["name"] for col in inspector.get_columns("rebalance_trades")}
    trade_indexes = {idx["name"] for idx in inspector.get_indexes("rebalance_trades")}

    if "price_hfq" not in trade_columns:
        conn.execute(text("ALTER TABLE rebalance_trades ADD COLUMN price_hfq DOUBLE NULL"))

    if "idx_rebalance_account_time" not in trade_indexes:
        conn.execute(text("CREATE INDEX idx_rebalance_account_time ON rebalance_trades(account_id, trade_time)"))

    if "idx_rebalance_code_time" not in trade_indexes:
        conn.execute(text("CREATE INDEX idx_rebalance_code_time ON rebalance_trades(ts_code, trade_time)"))


def _ensure_quote_points_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "quote_points" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("quote_points")}
    if "close" in columns and "close_hfq" not in columns:
        conn.execute(text("ALTER TABLE quote_points CHANGE COLUMN close close_hfq FLOAT NULL"))

    indexes = {idx["name"] for idx in inspector.get_indexes("quote_points")}
    if "idx_quote_points_date" not in indexes:
        conn.execute(text("CREATE INDEX idx_quote_points_date ON quote_points(trade_date)"))


def _ensure_benchmark_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "benchmark" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("benchmark")}
    for legacy_col in ("open", "high", "low", "pre_close", "change", "vol", "amount"):
        if legacy_col in columns:
            col_sql = f"`{legacy_col}`" if legacy_col == "change" else legacy_col
            conn.execute(text(f"ALTER TABLE benchmark DROP COLUMN {col_sql}"))
    if "pct_chg" not in columns:
        conn.execute(text("ALTER TABLE benchmark ADD COLUMN pct_chg DOUBLE NULL"))
    if "cum_return_pct" not in columns:
        conn.execute(text("ALTER TABLE benchmark ADD COLUMN cum_return_pct DOUBLE NULL"))

    indexes = {idx["name"] for idx in inspector.get_indexes("benchmark")}
    if "idx_benchmark_trade_date" not in indexes:
        conn.execute(text("CREATE INDEX idx_benchmark_trade_date ON benchmark(trade_date)"))


def _ensure_cube_nav_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "cube_nav_points" not in inspector.get_table_names():
        cube_nav_points_table.create(conn)
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("cube_nav_points")}
    if "idx_cube_nav_account_date" not in indexes:
        conn.execute(text("CREATE INDEX idx_cube_nav_account_date ON cube_nav_points(account_id, trade_date)"))


def _ensure_cube_catalog_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "cube_catalog" not in inspector.get_table_names():
        cube_catalog_table.create(conn)
        return
    columns = {col["name"] for col in inspector.get_columns("cube_catalog")}
    if "discovered" not in columns:
        conn.execute(text("ALTER TABLE cube_catalog ADD COLUMN discovered TINYINT(1) NOT NULL DEFAULT 0"))
    if "discovered_at" not in columns:
        conn.execute(text("ALTER TABLE cube_catalog ADD COLUMN discovered_at DATETIME NULL"))


def init_db() -> None:
    metadata.create_all(engine)

    with engine.begin() as conn:
        _ensure_accounts_schema(conn)
        _ensure_rebalance_schema(conn)
        _ensure_quote_points_schema(conn)
        _ensure_benchmark_schema(conn)
        _ensure_cube_nav_schema(conn)
        _ensure_cube_catalog_schema(conn)


@contextmanager
def get_conn() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn
