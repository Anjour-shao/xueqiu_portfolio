"""本地弹窗扫码登录雪球，采集多种 Cookie 方案并自动择优写入 data/xueqiu_cookie.txt。

云端部署时把该文件上传即可；过期后本地重新登录再上传。需安装可选依赖:
    pip install -e ".[login]"

用法:
    cd backend
    python ../scripts/xueqiu_login.py

登录完成后脚本会:
  1. 用多种方式采集 Cookie（当前页 / CDP 全域 / 访问首页 / 访问探测用户主页 / 触发 stock API）
  2. 逐个打分（含他人自选能否读到）
  3. 把最优方案写入 data/xueqiu_cookie.txt
"""

from __future__ import annotations

import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.config import COOKIE_FILE
from xueqiu.integrations.xueqiu.auth import (
    WATCHLIST_CANARY_UID,
    cookie_has_login_fields,
    cookie_to_str,
    cookie_user_id,
    parse_cookie_map,
    score_cookie_for_discovery,
)

LOGIN_URL = "https://xueqiu.com/account/login"
HOME_URL = "https://xueqiu.com/"
CANARY_USER_URL = f"https://xueqiu.com/u/{WATCHLIST_CANARY_UID}"
WAIT_TIMEOUT_SEC = 300
STABLE_LOGIN_HITS = 2


@dataclass(frozen=True)
class CookieCandidate:
    strategy: str
    description: str
    cookie: str
    score_report: dict[str, Any]

    @property
    def score(self) -> int:
        return int(self.score_report.get("score") or -9999)

    @property
    def canary_detail(self) -> int:
        return int(self.score_report.get("canary_detail") or 0)


def _cookie_pairs(cookies: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not cookies:
        return pairs
    for item in cookies:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
            value = str(getattr(item, "value", "") or "").strip()
        if name and value:
            pairs.append((name, value))
    return pairs


def _merge_cookie_maps(*maps: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for m in maps:
        merged.update(m)
    return merged


def _pairs_to_map(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return {name: value for name, value in pairs}


def _page_context_cookie_map(page) -> dict[str, str]:
    return _pairs_to_map(_cookie_pairs(page.cookies()))


def _cdp_xueqiu_cookie_map(page) -> dict[str, str]:
    items: list[Any] = []
    try:
        result = page.run_cdp("Network.getAllCookies")
        if isinstance(result, dict):
            items = result.get("cookies") or []
    except Exception:
        pass
    if not items:
        items = page.cookies(all_domains=True) if hasattr(page, "cookies") else page.cookies()
    merged: dict[str, str] = {}
    for name, value in _cookie_pairs(items):
        merged[name] = value
    return merged


def _describe_fields(fields: dict[str, str]) -> str:
    bits = []
    if fields.get("xq_a_token"):
        bits.append("xq_a_token")
    if fields.get("xq_r_token"):
        bits.append("xq_r_token")
    if fields.get("xq_is_login") == "1":
        bits.append("xq_is_login=1")
    uid = fields.get("u")
    if uid:
        bits.append(f"u={uid}")
    return ", ".join(bits) if bits else "（空）"


def _wait_for_login(page, *, timeout_sec: int = WAIT_TIMEOUT_SEC) -> bool:
    print(">>> 请在浏览器中扫码/登录")
    print(">>> 必须出现 xq_r_token；建议等页面跳转到首页后再稍等几秒")
    print(f">>> 最长等待 {timeout_sec} 秒")

    stable_hits = 0
    last_state = ""

    for remaining in range(timeout_sec, 0, -1):
        fields = _merge_cookie_maps(_page_context_cookie_map(page), _cdp_xueqiu_cookie_map(page))
        state = _describe_fields(fields)
        if state != last_state:
            last_state = state
            print(f">>> 当前 Cookie：{state}")

        if not cookie_has_login_fields(cookie_to_str(fields)):
            stable_hits = 0
        elif fields.get("xq_is_login") == "1":
            stable_hits += 1
            if stable_hits >= STABLE_LOGIN_HITS:
                print(f">>> 检测到登录完成（用户 {fields.get('u')}）")
                return True
        else:
            # 有 token 但尚未标记 xq_is_login，继续等
            stable_hits = 0
            if remaining % 20 == 0:
                print(">>> 已有登录 token，等待 xq_is_login=1 …")

        if remaining % 30 == 0:
            print(f">>> 仍在等待登录… 剩余约 {remaining}s")
        time.sleep(1)

    fields = _merge_cookie_maps(_page_context_cookie_map(page), _cdp_xueqiu_cookie_map(page))
    if cookie_has_login_fields(cookie_to_str(fields)):
        print(">>> 超时，但检测到基础登录字段，将继续尝试采集（可能不完整）")
        return True
    return False


def _sleep(sec: float) -> None:
    time.sleep(sec)


def _capture_strategies(page) -> list[tuple[str, str, Callable[[], dict[str, str]]]]:
    """返回 (代号, 说明, 采集函数)。"""
    return [
        (
            "A_page_context",
            "仅当前页面 context cookies（旧方案，常缺字段）",
            lambda: _page_context_cookie_map(page),
        ),
        (
            "B_cdp_all",
            "CDP Network.getAllCookies 合并全部 xueqiu 域",
            lambda: _cdp_xueqiu_cookie_map(page),
        ),
        (
            "C_after_home",
            "访问首页后 CDP 全量采集",
            lambda: (_sleep(2), page.get(HOME_URL), _sleep(2), _cdp_xueqiu_cookie_map(page))[-1],
        ),
        (
            "D_after_canary_user",
            f"访问探测用户主页 {WATCHLIST_CANARY_UID} 后 CDP 采集",
            lambda: (
                _sleep(1),
                page.get(CANARY_USER_URL),
                _sleep(3),
                _cdp_xueqiu_cookie_map(page),
            )[-1],
        ),
        (
            "E_canary_plus_stock_fetch",
            "探测用户主页 + 页面内 fetch stock 自选 API 后再采集",
            lambda: _capture_after_stock_fetch(page),
        ),
    ]


def _capture_after_stock_fetch(page) -> dict[str, str]:
    page.get(CANARY_USER_URL)
    _sleep(2)
    js = """
    (uid) => fetch(
      `https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?uid=${uid}&category=3&pid=-120&size=20`,
      { credentials: 'include', headers: { Accept: 'application/json' } }
    ).then(r => r.json()).catch(e => ({ error: String(e) }))
    """
    try:
        page.run_js(js, WATCHLIST_CANARY_UID)
    except TypeError:
        page.run_js(f"fetch('https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?uid={WATCHLIST_CANARY_UID}&category=3&pid=-120&size=20', {{credentials:'include'}})")
    except Exception:
        pass
    _sleep(2)
    return _cdp_xueqiu_cookie_map(page)


def _evaluate_candidates(page) -> list[CookieCandidate]:
    # 先跑一遍预热，让 F 方案也能拿到 D/E 后的状态
    page.get(HOME_URL)
    _sleep(1.5)

    raw: list[tuple[str, str, dict[str, str]]] = []
    for code, desc, capture in _capture_strategies(page):
        try:
            fields = capture()
        except Exception as exc:
            print(f"  ! {code} 采集失败: {exc}")
            continue
        if not fields:
            print(f"  ! {code} 未采到 Cookie")
            continue
        raw.append((code, desc, fields))

    # F_merge 改为真正合并 A-E 所有字段
    if raw:
        merged = _merge_cookie_maps(*(m for _, _, m in raw))
        raw.append(("F_merge_all_captured", "合并 A~E 已采集到的全部字段", merged))

    candidates: list[CookieCandidate] = []
    seen: set[str] = set()
    for code, desc, fields in raw:
        cookie = cookie_to_str(fields)
        if cookie in seen:
            continue
        seen.add(cookie)
        report = score_cookie_for_discovery(cookie)
        candidates.append(CookieCandidate(code, desc, cookie, report))
    return candidates


def _print_report(candidates: list[CookieCandidate]) -> None:
    if not candidates:
        print(">>> 未采集到任何 Cookie 方案")
        return

    ranked = sorted(
        candidates,
        key=lambda c: (c.canary_detail, c.score),
        reverse=True,
    )
    print("\n>>> Cookie 方案评测（按 canary 明细数 / 总分排序）")
    print("-" * 72)
    for idx, item in enumerate(ranked, start=1):
        rep = item.score_report
        flag = "OK" if item.canary_detail > 0 else ("DEGRADED" if rep.get("watchlist_degraded") else "WEAK")
        print(
            f"{idx}. [{item.strategy}] score={item.score} {flag} "
            f"canary={rep.get('canary_detail', 0)}/{rep.get('canary_meta', 0)} "
            f"fields={rep.get('field_count', 0)}"
        )
        print(f"    {item.description}")
        print(f"    notes: {', '.join(rep.get('notes') or [])}")
    print("-" * 72)


def _pick_best(candidates: list[CookieCandidate]) -> CookieCandidate | None:
    if not candidates:
        return None
    viable = [c for c in candidates if c.canary_detail > 0]
    pool = viable or [c for c in candidates if c.score_report.get("ok")]
    if not pool:
        pool = candidates
    return max(pool, key=lambda c: (c.canary_detail, c.score))


def _save_cookie(cookie: str) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(cookie.strip() + "\n", encoding="utf-8")


def _pause_before_close(*, success: bool, best: CookieCandidate | None) -> None:
    if success and best:
        print(f"\n>>> 最优方案: {best.strategy}（canary 明细 {best.canary_detail}，score {best.score}）")
        print(f">>> 已写入 {COOKIE_FILE}")
    try:
        input(">>> 按 Enter 关闭浏览器并退出… ")
    except EOFError:
        time.sleep(2)


def main() -> None:
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError as exc:
        print("缺少 DrissionPage。请运行: pip install -e \".[login]\"")
        raise SystemExit(1) from exc

    tmp_dir = tempfile.mkdtemp(prefix="xueqiu_login_")
    co = ChromiumOptions()
    co.set_user_data_path(tmp_dir)
    co.headless(False)
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")

    print(">>> 正在打开雪球登录页…")
    page = None
    success = False
    best: CookieCandidate | None = None
    try:
        page = ChromiumPage(co)
        page.get(LOGIN_URL)
        time.sleep(2)

        if not _wait_for_login(page, timeout_sec=WAIT_TIMEOUT_SEC):
            print(">>> 登录超时。请确认已完成扫码。")
            raise SystemExit(1)

        print("\n>>> 登录成功，开始采集并评测多种 Cookie 方案…")
        candidates = _evaluate_candidates(page)
        _print_report(candidates)

        best = _pick_best(candidates)
        if best is None:
            print(">>> 没有可用 Cookie")
            raise SystemExit(1)

        if best.canary_detail <= 0:
            print(
                "\n>>> 警告：所有方案都无法读取他人自选明细。"
                "请在浏览器中手动打开 "
                f"{CANARY_USER_URL} "
                "确认能看到自选组合，然后重新运行本脚本，或手动复制 Cookie。"
            )

        _save_cookie(best.cookie)
        success = True
    finally:
        _pause_before_close(success=success, best=best)
        if page is not None:
            page.quit()


if __name__ == "__main__":
    main()
