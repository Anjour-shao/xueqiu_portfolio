from __future__ import annotations

import os
from pathlib import Path

from xueqiu.config import COOKIE_FILE

COOKIE_FILE_EXAMPLE = COOKIE_FILE.parent / "xueqiu_cookie.txt.example"


def _cookie_from_env() -> str | None:
    raw = os.getenv("XUEQIU_COOKIE", "").strip()
    return raw or None


def _cookie_from_file(path: Path | None = None) -> str | None:
    target = path or COOKIE_FILE
    if not target.exists():
        return None
    text = target.read_text(encoding="utf-8").strip()
    if not text or text.startswith("#"):
        return None
    return text


def load_cookie(*, cookie_file: Path | None = None) -> str:
    for loader in (
        lambda: _cookie_from_env(),
        lambda: _cookie_from_file(cookie_file),
    ):
        cookie = loader()
        if cookie and "xq_a_token" in cookie:
            return cookie
    raise RuntimeError(
        "未找到有效雪球 Cookie。请运行 python ../scripts/xueqiu_login.py 扫码登录，"
        "或将 Cookie 写入 data/xueqiu_cookie.txt，或设置环境变量 XUEQIU_COOKIE。"
    )
