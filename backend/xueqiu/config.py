from __future__ import annotations

from pathlib import Path
import os

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")

RAW_DATABASE_URL = os.getenv("ACCOUNT_DASHBOARD_DATABASE_URL", "").strip()
if not RAW_DATABASE_URL:
    raise RuntimeError(
        "缺少环境变量 ACCOUNT_DASHBOARD_DATABASE_URL，例如："
        "mysql+pymysql://root:password@127.0.0.1:3306/portfolio?charset=utf8mb4"
    )

DATABASE_URL = RAW_DATABASE_URL
HOST = os.getenv("ACCOUNT_DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.getenv("ACCOUNT_DASHBOARD_PORT", "8010"))

TUSHARE_API_KEY = os.getenv("TUSHARE_API_KEY", os.getenv("TUSHARE_TOKEN", "")).strip()
TUSHARE_HTTP_URL = os.getenv("TUSHARE_HTTP_URL", os.getenv("TUSHARE_PROXY_URL", "")).strip()
BENCHMARK_TS_CODE = os.getenv("BENCHMARK_TS_CODE", "000001.SH").strip()

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "").strip()
# 钉钉机器人若开启「自定义关键词」，每条消息正文须包含该词
DINGTALK_KEYWORD = os.getenv("DINGTALK_KEYWORD", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

COOKIE_FILE = PROJECT_ROOT / "data" / "xueqiu_cookie.txt"
