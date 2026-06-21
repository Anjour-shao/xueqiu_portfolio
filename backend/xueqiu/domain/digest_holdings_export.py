"""「我的持仓」→ Digest 快照：前端保存后自动写本地 JSON + 上传 OSS（GHA 读取）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from xueqiu.config import (
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    OSS_BUCKET_NAME,
    OSS_CUSTOM_DOMAIN,
    OSS_ENDPOINT,
    PROJECT_ROOT,
)
from xueqiu.domain.personal_account import get_personal_account_raw

SNAPSHOT_REL = Path("daily_digest") / "holdings_snapshot.json"
OSS_SNAPSHOT_KEY = "digest/holdings_snapshot.json"


def _digits_code(ts_code: str) -> str:
    code = str(ts_code or "").strip().upper()
    if len(code) >= 8 and code[:2] in {"SH", "SZ", "BJ"}:
        return code[2:]
    return code


def build_digest_holdings_snapshot() -> dict[str, Any]:
    raw = get_personal_account_raw()
    if not raw:
        return {
            "version": 1,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "account": {"name": "股票账户1", "cash": 0.0, "strategy_id": "route_g_conviction_trust"},
            "holdings": [],
        }

    holdings: list[dict[str, Any]] = []
    for h in sorted(raw.get("holdings") or [], key=lambda x: str(x.get("ts_code") or "")):
        opened = str(h.get("opened_at") or "").strip()
        item: dict[str, Any] = {
            "code": _digits_code(str(h["ts_code"])),
            "name": str(h["stock_name"]),
            "shares": int(h["shares"]),
            "cost_price": float(h["cost_price"]),
        }
        if opened:
            item["opened_at"] = opened[:10]
        holdings.append(item)

    return {
        "version": 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account": {
            "name": str(raw.get("name") or "股票账户1"),
            "cash": float(raw.get("cash") or 0),
            "strategy_id": str(raw.get("strategy_id") or "route_g_conviction_trust"),
        },
        "holdings": holdings,
    }


def _upload_snapshot_to_oss(path: Path) -> str | None:
    if not all([OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_ENDPOINT, OSS_BUCKET_NAME]):
        return None
    try:
        import oss2
    except ImportError:
        return None

    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, f"https://{OSS_ENDPOINT}", OSS_BUCKET_NAME)
    bucket.put_object_from_file(OSS_SNAPSHOT_KEY, str(path))
    if OSS_CUSTOM_DOMAIN:
        return f"{OSS_CUSTOM_DOMAIN.rstrip('/')}/{OSS_SNAPSHOT_KEY}"
    return f"https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}/{OSS_SNAPSHOT_KEY}"


def export_digest_holdings_snapshot(*, quiet: bool = False) -> Path:
    """写入 daily_digest/holdings_snapshot.json，并尽力上传 OSS。"""
    payload = build_digest_holdings_snapshot()
    out = PROJECT_ROOT / SNAPSHOT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    oss_url = _upload_snapshot_to_oss(out)
    if not quiet:
        n = len(payload.get("holdings") or [])
        cash = float((payload.get("account") or {}).get("cash") or 0)
        msg = f"Digest 持仓快照已更新（{n} 只，现金 {cash:,.2f}）→ {out.name}"
        if oss_url:
            msg += f"，OSS: {oss_url}"
        print(msg)
    return out


def sync_digest_holdings_after_change() -> None:
    """API 保存持仓后调用；失败不影响主流程。"""
    try:
        export_digest_holdings_snapshot(quiet=True)
    except Exception as exc:
        print(f"Digest 持仓快照导出失败（不影响保存）: {exc}")
