from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, PrimaryKeyConstraint, SmallInteger, String, Table, Text, UniqueConstraint, create_engine, inspect, text
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

# 社交挖组合：已爬到的候选 ZH（有行即已爬过；selected 为人工选中态）
mined_cubes_table = Table(
    "mined_cubes",
    metadata,
    Column("account_code", String(64), primary_key=True),
    Column("account_name", String(255), nullable=False),
    Column("owner_uid", BigInteger, nullable=True, index=True),
    Column("owner_name", String(255), nullable=True),
    Column("source_user_uid", BigInteger, nullable=True, index=True),
    Column("source_account_code", String(64), nullable=True),
    Column("source_type", String(32), nullable=True),
    Column("source_symbol", String(16), nullable=True),
    Column("depth", Integer, nullable=False, server_default=text("1")),
    Column("cum_return_pct", Float, nullable=True),
    Column("nav_latest_date", String(8), nullable=True),
    Column("latest_rebalance_time", String(32), nullable=True),
    Column("rebalance_count_6m", Integer, nullable=True),
    Column("cube_market", String(16), nullable=True),
    Column("has_non_a_share", Boolean, nullable=False, server_default=text("0")),
    Column("auto_pass", Boolean, nullable=False, server_default=text("0")),
    Column("reject_reasons", Text, nullable=True),
    Column("selected", SmallInteger, nullable=True),
    Column("note", Text, nullable=True),
    Column("imported_at", DateTime, nullable=True),
    Column("first_seen_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

discovery_symbol_pool_table = Table(
    "discovery_symbol_pool",
    metadata,
    Column("symbol", String(16), primary_key=True),
    Column("stock_name", String(64), nullable=True),
    Column("note", String(255), nullable=True),
    Column("enabled", Boolean, nullable=False, server_default=text("1")),
    Column("sort_order", Integer, nullable=False, server_default=text("0")),
    Column("is_builtin", Boolean, nullable=False, server_default=text("0")),
    Column("volume_rank_date", String(8), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

discovery_crawled_users_table = Table(
    "discovery_crawled_users",
    metadata,
    Column("user_uid", BigInteger, nullable=False),
    Column("crawl_kind", String(32), nullable=False),
    Column("crawled_at", DateTime, nullable=False),
    PrimaryKeyConstraint("user_uid", "crawl_kind", name="pk_discovery_crawled_users"),
)

personal_accounts_table = Table(
    "personal_accounts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("cash", Float, nullable=False, server_default=text("0")),
    Column("strategy_id", String(64), nullable=False, server_default="route_g_conviction_trust"),
    Column("updated_at", DateTime, nullable=False),
)

personal_holdings_table = Table(
    "personal_holdings",
    metadata,
    Column("account_id", Integer, ForeignKey("personal_accounts.id"), nullable=False),
    Column("ts_code", String(32), nullable=False),
    Column("stock_name", String(255), nullable=False),
    Column("shares", Integer, nullable=False),
    Column("cost_price", Float, nullable=False),
    Column("opened_at", String(10), nullable=True),
    Column("updated_at", DateTime, nullable=False),
    PrimaryKeyConstraint("account_id", "ts_code", name="pk_personal_holdings"),
)

personal_trades_table = Table(
    "personal_trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("personal_accounts.id"), nullable=False, index=True),
    Column("trade_time", String(32), nullable=False),
    Column("ts_code", String(32), nullable=False),
    Column("stock_name", String(255), nullable=False),
    Column("action", String(16), nullable=False),
    Column("shares", Integer, nullable=False),
    Column("price", Float, nullable=False),
    Column("amount", Float, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

# ---- 雪球观点提取 ----

xueqiu_posts_table = Table(
    "xueqiu_posts",
    metadata,
    Column("post_id", BigInteger, primary_key=True),
    Column("user_id", BigInteger, nullable=False, index=True),
    Column("user_name", String(255), nullable=False),
    Column("created_at", String(32), nullable=False, index=True),
    Column("text", Text, nullable=False),
    Column("retweet_count", Integer, nullable=False, server_default=text("0")),
    Column("reply_count", Integer, nullable=False, server_default=text("0")),
    Column("like_count", Integer, nullable=False, server_default=text("0")),
    Column("source", String(255), nullable=True),
    Column("target", String(255), nullable=True),
    Column("fetched_at", DateTime, nullable=False),
)

xueqiu_comments_table = Table(
    "xueqiu_comments",
    metadata,
    Column("comment_id", BigInteger, primary_key=True),
    Column("post_id", BigInteger, ForeignKey("xueqiu_posts.post_id"), nullable=False, index=True),
    Column("user_id", BigInteger, nullable=True),
    Column("user_name", String(255), nullable=True),
    Column("created_at", String(32), nullable=False),
    Column("text", Text, nullable=False),
    Column("reply_to_comment_id", BigInteger, nullable=True),
    Column("info_score", Float, nullable=True),
    Column("is_author_reply", Boolean, nullable=False, server_default=text("0")),
    Column("fetched_at", DateTime, nullable=False),
)

xueqiu_extractions_table = Table(
    "xueqiu_extractions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("post_id", BigInteger, ForeignKey("xueqiu_posts.post_id"), nullable=False, index=True),
    Column("has_info", Boolean, nullable=False, server_default=text("0")),
    Column("summary", Text, nullable=True),
    Column("macro_views", Text, nullable=True),
    Column("model_used", String(64), nullable=True),
    Column("extracted_at", DateTime, nullable=False),
    UniqueConstraint("post_id", name="uq_extraction_post"),
)

extraction_stocks_table = Table(
    "extraction_stocks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("extraction_id", Integer, ForeignKey("xueqiu_extractions.id"), nullable=False, index=True),
    Column("stock_name", String(255), nullable=False),
    Column("stock_code", String(16), nullable=True),
    Column("mention_type", String(32), nullable=False),
    Column("raw_text", String(255), nullable=True),
    Column("sentiment", String(32), nullable=True),
    Column("time_horizon", String(32), nullable=True),
    Column("key_logic", Text, nullable=True),
    Column("confidence", Float, nullable=False, server_default=text("0.5")),
)

extraction_sectors_table = Table(
    "extraction_sectors",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("extraction_id", Integer, ForeignKey("xueqiu_extractions.id"), nullable=False, index=True),
    Column("sector_name", String(255), nullable=False),
    Column("sentiment", String(32), nullable=True),
    Column("time_horizon", String(32), nullable=True),
    Column("key_logic", Text, nullable=True),
)

nickname_map_table = Table(
    "nickname_map",
    metadata,
    Column("nickname", String(255), primary_key=True),
    Column("stock_name", String(255), nullable=False),
    Column("stock_code", String(16), nullable=True),
    Column("confidence", Float, nullable=False, server_default=text("0.5")),
    Column("confirmed", Boolean, nullable=False, server_default=text("0")),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
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


def _ensure_mined_cubes_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "mined_cubes" not in inspector.get_table_names():
        mined_cubes_table.create(conn)
        return
    columns = {col["name"]: col for col in inspector.get_columns("mined_cubes")}
    for uid_col in ("owner_uid", "source_user_uid"):
        if uid_col not in columns:
            continue
        col_type = str(columns[uid_col].get("type", "")).upper()
        if "BIGINT" not in col_type:
            conn.execute(
                text(f"ALTER TABLE mined_cubes MODIFY COLUMN {uid_col} BIGINT NULL")
            )
    indexes = {idx["name"] for idx in inspector.get_indexes("mined_cubes")}
    if "idx_mined_cubes_source_uid" not in indexes:
        conn.execute(text("CREATE INDEX idx_mined_cubes_source_uid ON mined_cubes(source_user_uid)"))
    if "idx_mined_cubes_owner_uid" not in indexes:
        conn.execute(text("CREATE INDEX idx_mined_cubes_owner_uid ON mined_cubes(owner_uid)"))
    columns = {col["name"] for col in inspector.get_columns("mined_cubes")}
    if "latest_rebalance_time" not in columns:
        conn.execute(text("ALTER TABLE mined_cubes ADD COLUMN latest_rebalance_time VARCHAR(32) NULL"))
    if "cube_market" not in columns:
        conn.execute(text("ALTER TABLE mined_cubes ADD COLUMN cube_market VARCHAR(16) NULL"))
    if "rebalance_count_6m" not in columns:
        conn.execute(text("ALTER TABLE mined_cubes ADD COLUMN rebalance_count_6m INT NULL"))
    if "source_type" not in columns:
        conn.execute(text("ALTER TABLE mined_cubes ADD COLUMN source_type VARCHAR(32) NULL"))
    if "source_symbol" not in columns:
        conn.execute(text("ALTER TABLE mined_cubes ADD COLUMN source_symbol VARCHAR(16) NULL"))
    indexes = {idx["name"] for idx in inspector.get_indexes("mined_cubes")}
    if "idx_mined_cubes_list" not in indexes:
        conn.execute(
            text(
                "CREATE INDEX idx_mined_cubes_list ON mined_cubes "
                "(auto_pass, selected, imported_at, cum_return_pct)"
            )
        )


def _ensure_discovery_symbol_pool_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "discovery_symbol_pool" not in inspector.get_table_names():
        discovery_symbol_pool_table.create(conn)
        return
    columns = {col["name"] for col in inspector.get_columns("discovery_symbol_pool")}
    if "stock_name" not in columns:
        conn.execute(text("ALTER TABLE discovery_symbol_pool ADD COLUMN stock_name VARCHAR(64) NULL"))


def _ensure_discovery_crawled_users_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "discovery_crawled_users" not in inspector.get_table_names():
        discovery_crawled_users_table.create(conn)
        conn.execute(
            text(
                """
                INSERT IGNORE INTO discovery_crawled_users (user_uid, crawl_kind, crawled_at)
                SELECT DISTINCT source_user_uid, COALESCE(source_type, 'watchlist'), updated_at
                FROM mined_cubes
                WHERE source_user_uid IS NOT NULL
                """
            )
        )


def _drop_legacy_cube_catalog(conn: Connection) -> None:
    inspector = inspect(conn)
    if "cube_catalog" in inspector.get_table_names():
        conn.execute(text("DROP TABLE cube_catalog"))


def _ensure_personal_account_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    if "personal_accounts" not in inspector.get_table_names():
        personal_accounts_table.create(conn)
    if "personal_holdings" not in inspector.get_table_names():
        personal_holdings_table.create(conn)
    if "personal_trades" not in inspector.get_table_names():
        personal_trades_table.create(conn)


def _ensure_xueqiu_analysis_schema(conn: Connection) -> None:
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    if "xueqiu_posts" not in existing:
        xueqiu_posts_table.create(conn)
    if "xueqiu_comments" not in existing:
        xueqiu_comments_table.create(conn)
    if "xueqiu_extractions" not in existing:
        xueqiu_extractions_table.create(conn)
    if "extraction_stocks" not in existing:
        extraction_stocks_table.create(conn)
    if "extraction_sectors" not in existing:
        extraction_sectors_table.create(conn)
    if "nickname_map" not in existing:
        nickname_map_table.create(conn)


def _seed_nickname_map(conn: Connection) -> None:
    """预填已知代称映射（仅当表为空时）。"""
    from datetime import datetime as dt

    rows = conn.execute(
        text("SELECT COUNT(*) AS cnt FROM nickname_map")
    ).fetchone()
    if rows and rows[0] > 0:
        return

    now = dt.now()
    seeds = [
        ("小姨", "兆易创新", "603986", 0.90, True, now, now),
        ("罩衣", "兆易创新", "603986", 0.85, True, now, now),
        ("赵姨", "兆易创新", "603986", 0.85, True, now, now),
        ("赵毅", "兆易创新", "603986", 0.70, True, now, now),
        ("赵一", "兆易创新", "603986", 0.70, True, now, now),
        ("德子", "德明利", "309031", 0.95, True, now, now),
        ("德明利", "德明利", "309031", 1.00, True, now, now),
        ("冉子", "香农芯创", "300475", 0.60, False, now, now),
        ("香", "香农芯创", "300475", 0.50, False, now, now),
        ("寒王", "寒武纪", "688256", 0.95, True, now, now),
        ("寒无纪", "寒武纪", "688256", 0.90, True, now, now),
        ("无忌", "寒武纪", "688256", 0.60, False, now, now),
        ("张无忌", "拓荆科技", "688072", 0.70, False, now, now),
        ("拓荆", "拓荆科技", "688072", 1.00, True, now, now),
        ("东土", "东土科技", "300353", 0.80, True, now, now),
        ("长信", "长信科技", "300088", 0.70, False, now, now),
        ("常山", "常山北明", "000158", 0.80, True, now, now),
        ("闪迪", "闪迪", "SNDK", 1.00, True, now, now),
        ("英伟达", "英伟达", "NVDA", 1.00, True, now, now),
        ("荣昌", "荣昌生物", "688331", 0.85, True, now, now),
        ("凯莱英", "凯莱英", "002821", 1.00, True, now, now),
        ("瑞芯微", "瑞芯微", "603893", 1.00, True, now, now),
        ("紫光", "紫光国微", "002049", 0.80, True, now, now),
        ("中际", "中际旭创", "300308", 0.90, True, now, now),
        ("新易盛", "新易盛", "300502", 1.00, True, now, now),
        ("海力士", "SK海力士", "000660.KS", 1.00, True, now, now),
    ]
    for nickname, name, code, conf, confirmed, ctime, utime in seeds:
        conn.execute(
            nickname_map_table.insert().values(
                nickname=nickname,
                stock_name=name,
                stock_code=code,
                confidence=conf,
                confirmed=confirmed,
                created_at=ctime,
                updated_at=utime,
            )
        )


def init_db() -> None:
    metadata.create_all(engine)

    with engine.begin() as conn:
        _ensure_accounts_schema(conn)
        _ensure_rebalance_schema(conn)
        _ensure_quote_points_schema(conn)
        _ensure_benchmark_schema(conn)
        _ensure_cube_nav_schema(conn)
        _drop_legacy_cube_catalog(conn)
        _ensure_mined_cubes_schema(conn)
        _ensure_discovery_symbol_pool_schema(conn)
        _ensure_discovery_crawled_users_schema(conn)
        _ensure_personal_account_schema(conn)
        _ensure_xueqiu_analysis_schema(conn)
        _seed_nickname_map(conn)


def init_personal_db() -> None:
    """仅初始化个人持仓相关表（digest 在无 MySQL 时用 sqlite 内存库）。"""
    with engine.begin() as conn:
        _ensure_personal_account_schema(conn)


@contextmanager
def get_conn() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn
