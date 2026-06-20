"""新浪实时行情（未复权现价）。"""

from __future__ import annotations

import requests

from xueqiu.integrations.sina.hfq import xueqiu_to_sina

_SPOT_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _normalize_xueqiu_code(code: str) -> str:
    raw = str(code or "").strip().upper()
    if len(raw) >= 8 and raw[:2] in {"SH", "SZ", "BJ"}:
        return raw
    digits = raw.split(".")[0]
    if digits.isdigit() and len(digits) == 6:
        if digits.startswith(("5", "6", "9")):
            return f"SH{digits}"
        return f"SZ{digits}"
    return raw


def _code_to_sinajs_symbol(code: str) -> str:
    code = _normalize_xueqiu_code(code)
    digits, market = xueqiu_to_sina(code)
    return f"{market}{digits}"


def _parse_sinajs_line(line: str) -> tuple[float | None, float | None]:
    eq = line.find("=")
    if eq < 0:
        return None, None
    payload = line[eq + 1 :].strip().strip('";')
    if not payload or payload == '""':
        return None, None
    parts = payload.split(",")
    if len(parts) < 4:
        return None, None
    try:
        pre_close = float(parts[2])
        current = float(parts[3])
    except ValueError:
        return None, None
    if pre_close <= 0 or current <= 0:
        return None, None
    return current, pre_close


def fetch_spot_prices(ts_codes: list[str]) -> dict[str, float]:
    """批量拉取现价；失败代码不在结果中。"""
    if not ts_codes:
        return {}

    sym_map: dict[str, str] = {}
    for code in ts_codes:
        code = str(code).strip().upper()
        if not code:
            continue
        norm = _normalize_xueqiu_code(code)
        sym_map[_code_to_sinajs_symbol(norm)] = norm

    if not sym_map:
        return {}

    url = "http://hq.sinajs.cn/list=" + ",".join(sym_map.keys())
    try:
        resp = requests.get(url, headers=_SPOT_HEADERS, timeout=15)
        resp.encoding = "gbk"
        resp.raise_for_status()
    except Exception:
        return {}

    out: dict[str, float] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        var_name = line.split("=", 1)[0]
        sym = var_name.replace("var hq_str_", "")
        code = sym_map.get(sym)
        if not code:
            continue
        current, _ = _parse_sinajs_line(line)
        if current is not None:
            out[code] = current
    return out
