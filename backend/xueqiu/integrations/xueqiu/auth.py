from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from xueqiu.config import COOKIE_FILE

COOKIE_FILE_EXAMPLE = COOKIE_FILE.parent / "xueqiu_cookie.txt.example"
WATCHLIST_CANARY_UID = "2116089912"

_IMPORTANT_COOKIE_KEYS = frozenset(
    {
        "u",
        "cookiesu",
        "xq_a_token",
        "xq_r_token",
        "xq_id_token",
        "xqat",
        "xq_is_login",
        "device_id",
        "bid",
        "s",
        "ssxmod_itna",
        "ssxmod_itna2",
        "acw_tc",
    }
)


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


def parse_cookie_map(cookie: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        result[name.strip()] = value.strip()
    return result


def cookie_user_id(cookie: str) -> str | None:
    uid = parse_cookie_map(cookie).get("u", "").strip()
    if uid.isdigit() and uid != "0":
        return uid
    return None


def cookie_has_login_fields(cookie: str) -> bool:
    fields = parse_cookie_map(cookie)
    token = fields.get("xq_a_token", "").strip()
    refresh = fields.get("xq_r_token", "").strip()
    return bool(token and refresh and cookie_user_id(cookie))


def cookie_to_str(fields: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in fields.items() if name and value)


def _canary_watchlist_counts(api: Any, *, canary_uid: str) -> tuple[int, int]:
    """返回 (元数据自选数, 明细自选数)。"""
    from xueqiu.integrations.xueqiu.client import XueQiuApiError

    meta_count = 0
    detail_count = 0
    referer = f"https://xueqiu.com/u/{canary_uid}"
    try:
        meta = api.get_json_with_retry(
            "https://stock.xueqiu.com/v5/stock/portfolio/list.json",
            params={"uid": canary_uid, "system": "true"},
            referer=referer,
            max_retries=2,
            delay=(1.0, 1.8),
        )
        cubes = (meta.get("data") or {}).get("cubes") if isinstance(meta, dict) else None
        if isinstance(cubes, list):
            for group in cubes:
                if isinstance(group, dict) and group.get("id") == -120:
                    meta_count = int(group.get("symbol_count") or 0)
                    break
        if meta_count > 0:
            detail = api.get_json_with_retry(
                "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json",
                params={
                    "uid": canary_uid,
                    "category": 3,
                    "pid": -120,
                    "size": 200,
                },
                referer=referer,
                max_retries=2,
                delay=(1.0, 1.8),
            )
            stocks = (detail.get("data") or {}).get("stocks") if isinstance(detail, dict) else None
            detail_count = len(stocks) if isinstance(stocks, list) else 0
    except XueQiuApiError:
        pass
    return meta_count, detail_count


def score_cookie_for_discovery(
    cookie: str,
    *,
    canary_uid: str = WATCHLIST_CANARY_UID,
) -> dict[str, Any]:
    """评估 Cookie 是否适合挖组合（含他人自选探测）。"""
    cookie = cookie.strip()
    fields = parse_cookie_map(cookie)
    uid = cookie_user_id(cookie)
    score = 0
    notes: list[str] = []

    if not fields.get("xq_a_token", "").strip():
        return {"score": -1000, "ok": False, "uid": uid, "reason": "缺少 xq_a_token", "notes": notes}
    if not uid:
        return {"score": -1000, "ok": False, "uid": None, "reason": "缺少有效 u", "notes": notes}
    if not fields.get("xq_r_token", "").strip():
        return {"score": -1000, "ok": False, "uid": uid, "reason": "缺少 xq_r_token", "notes": notes}

    if fields.get("xq_is_login") == "1":
        score += 20
        notes.append("xq_is_login=1")
    else:
        notes.append("无 xq_is_login")

    present_keys = _IMPORTANT_COOKIE_KEYS.intersection(fields.keys())
    score += len(present_keys) * 2
    if "xq_id_token" in fields:
        score += 10
    if "ssxmod_itna" in fields or "ssxmod_itna2" in fields:
        score += 8

    from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError

    api = XueQiuApiClient(cookie=cookie)
    try:
        api.get_json_with_retry(
            "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json",
            params={"uid": uid, "category": 3, "pid": -120},
            max_retries=2,
            delay=(1.0, 1.8),
        )
    except XueQiuApiError as exc:
        return {"score": -500, "ok": False, "uid": uid, "reason": str(exc), "notes": notes}

    canary_meta = 0
    canary_detail = 0
    if uid != canary_uid:
        canary_meta, canary_detail = _canary_watchlist_counts(api, canary_uid=canary_uid)

    if canary_detail >= 10:
        score += 2000
        notes.append(f"canary明细{canary_detail}")
    elif canary_detail > 0:
        score += 800 + canary_detail * 10
        notes.append(f"canary明细{canary_detail}")
    elif canary_meta > 0:
        score -= 600
        notes.append(f"canary仅元数据{canary_meta}")

    watchlist_degraded = canary_meta > 0 and canary_detail == 0 and uid != canary_uid
    ok = not watchlist_degraded
    reason = "登录态有效"
    if watchlist_degraded:
        reason = (
            f"登录有效但无法读他人自选（探测 {canary_uid} 元数据 {canary_meta}、明细 0）"
        )

    return {
        "score": score,
        "ok": ok,
        "uid": uid,
        "reason": reason,
        "watchlist_degraded": watchlist_degraded,
        "canary_uid": canary_uid,
        "canary_meta": canary_meta,
        "canary_detail": canary_detail,
        "field_count": len(fields),
        "important_keys": sorted(present_keys),
        "notes": notes,
    }


def verify_xueqiu_cookie(cookie: str | None = None) -> dict[str, Any]:
    """探测 Cookie 是否为已登录用户且可调受保护 API。"""
    if cookie is None:
        try:
            cookie = load_cookie()
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}

    scored = score_cookie_for_discovery(cookie)
    result: dict[str, Any] = {
        "ok": scored.get("score", -1000) >= 0 and bool(scored.get("ok")),
        "uid": scored.get("uid"),
        "reason": scored.get("reason") or "未知",
        "score": scored.get("score"),
    }
    if scored.get("watchlist_degraded"):
        result["watchlist_degraded"] = True
        result["reason"] = (
            f"{scored['reason']}。"
            "建议重新登录并访问他人主页后再导出 Cookie。"
        )
    elif scored.get("ok"):
        detail = scored.get("canary_detail") or 0
        if detail > 0:
            result["reason"] = f"登录态有效，他人自选探测 {detail} 个"
    return result


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


def is_cookie_invalid_text(text: str) -> bool:
    msg = str(text or "")
    return any(
        token in msg
        for token in (
            "Cookie 已失效",
            "登录态失效",
            "400016",
            "重新登录",
            "未找到有效雪球 Cookie",
            "缺少 xq_a_token",
        )
    )


COOKIE_REFRESH_HINT = (
    "本地：运行 python scripts/xueqiu_login.py，更新 data/xueqiu_cookie.txt 后重启后端。"
    "云端 Actions：GitHub 仓库 Settings → Secrets → 更新 XUEQIU_COOKIE（粘贴新 Cookie 全文）。"
)
