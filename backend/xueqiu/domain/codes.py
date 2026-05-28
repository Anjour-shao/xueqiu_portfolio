from __future__ import annotations

import re

_CN_A_PREFIXES = frozenset({"SH", "SZ", "BJ"})
_HK_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_HK_NUMERIC_RE = re.compile(r"^\d{5}$")


def to_xueqiu_code(ts_code: str) -> str:
    code = ts_code.strip().upper()
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return code
    if "." in code:
        symbol, exchange = code.split(".", 1)
        return f"{exchange}{symbol}"
    return code


def to_tushare_code(xueqiu_code: str) -> str:
    code = xueqiu_code.strip().upper()
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return f"{code[2:]}.{code[:2]}"
    return code


def is_cn_a_share(symbol: str) -> bool:
    code = to_xueqiu_code(symbol).strip().upper()
    return len(code) >= 8 and code[:2] in _CN_A_PREFIXES and code[2:8].isdigit()


def is_hk_us_or_non_a_share(symbol: str) -> bool:
    """非沪深京 A 股（港美股等）。"""
    raw = str(symbol or "").strip().upper()
    if not raw or raw.startswith("ZH"):
        return False
    code = to_xueqiu_code(raw)
    if is_cn_a_share(code):
        return False
    if code.startswith("HK") or code.endswith(".HK"):
        return True
    if code.startswith("US") or code.endswith(".US"):
        return True
    if _HK_NUMERIC_RE.match(code):
        return True
    if _HK_US_TICKER_RE.match(code):
        return True
    # 带点号的其它市场，如 00700.HK、BABA
    if "." in code:
        suffix = code.rsplit(".", 1)[-1]
        if suffix in {"HK", "US", "N", "O", "NYSE", "NASDAQ"}:
            return True
    return True
