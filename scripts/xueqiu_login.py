"""本地弹窗扫码登录雪球，将 Cookie 写入 data/xueqiu_cookie.txt。

云端部署时把该文件上传即可；过期后本地重新登录再上传。需安装可选依赖:
    pip install -e ".[login]"

用法:
    cd backend
    python ../scripts/xueqiu_login.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.config import COOKIE_FILE


def _cookie_pairs(cookies) -> list[str]:
    pairs: list[str] = []
    for item in cookies:
        if isinstance(item, dict):
            name = item.get("name", "")
            value = item.get("value", "")
        else:
            name = getattr(item, "name", "")
            value = getattr(item, "value", "")
        if name and value:
            pairs.append(f"{name}={value}")
    return pairs


def _is_logged_in(cookies) -> bool:
    names = set()
    for item in cookies:
        if isinstance(item, dict):
            names.add(item.get("name", ""))
        else:
            names.add(getattr(item, "name", ""))
    return "xq_a_token" in names


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

    print(">>> 正在打开雪球登录窗口…")
    page = ChromiumPage(co)
    try:
        page.get("https://xueqiu.com")
        time.sleep(2)

        if not _is_logged_in(page.cookies()):
            print(">>> 请在弹出的浏览器中完成登录（推荐二维码登录）")
            for i in range(120):
                if _is_logged_in(page.cookies()):
                    break
                if i % 10 == 0 and i:
                    print(f"等待登录中… ({120 - i}s)")
                time.sleep(1)

        if not _is_logged_in(page.cookies()):
            print(">>> 登录超时，请重试。")
            raise SystemExit(1)

        cookie = "; ".join(_cookie_pairs(page.cookies()))
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(cookie + "\n", encoding="utf-8")
        print(f">>> 登录成功，Cookie 已写入 {COOKIE_FILE}")
        print(">>> 云端部署时上传此文件，或设置环境变量 XUEQIU_COOKIE")
    finally:
        page.quit()


if __name__ == "__main__":
    main()
