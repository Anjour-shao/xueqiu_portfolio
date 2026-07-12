"""HTML 渲染为图片并推送钉钉。"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "digest" / "templates"
OUTPUT_DIR = ROOT / "digest_output"

DIGEST_PUSH_MODE = os.getenv("DIGEST_PUSH_MODE", "image").strip().lower()
DIGEST_IMAGE_UPLOAD = os.getenv("DIGEST_IMAGE_UPLOAD", "auto").strip().lower()
IMG_BB_API_KEY = os.getenv("IMG_BB_API_KEY", "").strip()
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "").strip()
DINGTALK_KEYWORD = os.getenv("DINGTALK_KEYWORD", "").strip()

try:
    from xueqiu.config import (
        OSS_ACCESS_KEY_ID,
        OSS_ACCESS_KEY_SECRET,
        OSS_BUCKET_NAME,
        OSS_CUSTOM_DOMAIN,
        OSS_ENDPOINT,
    )
except Exception:
    OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "").strip()
    OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME", "").strip()
    OSS_CUSTOM_DOMAIN = os.getenv("OSS_CUSTOM_DOMAIN", "").strip()


def _pnl_class(value: float | None) -> str:
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "-"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_money(amount: float | None, *, signed: bool = True) -> str:
    if amount is None:
        return "-"
    if signed:
        sign = "+" if amount > 0 else ""
        return f"{sign}{amount:,.2f}"
    return f"{amount:,.2f}"


def _truncate(text: str, limit: int = 1400) -> str:
    compact = clean_ai_summary(text)
    if len(compact) <= limit:
        return compact
    cut = compact[:limit].rstrip()
    for sep in ("\n\n", "\n", "。"):
        last = cut.rfind(sep)
        if last > limit * 0.45:
            cut = cut[: last + (len(sep) if sep != "\n\n" else 0)]
            break
    return cut.rstrip()


_AI_FILLER_RES = [
    re.compile(r"^好的[，,]?"),
    re.compile(r"^作为.*分析师"),
    re.compile(r"^我已从.*提取"),
    re.compile(r"^根据.*评论"),
    re.compile(r"^根据最新的?市场舆情"),
    re.compile(r"^综合各方信息"),
    re.compile(r"^为您呈现"),
    re.compile(r"^以下是对"),
    re.compile(r"^过滤情绪化"),
]


def clean_ai_summary(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for ln in raw.split("\n"):
        ln = ln.strip()
        ln = re.sub(r"^[\s\u3000]+", "", ln)
        ln = re.sub(r"^#+\s*", "", ln)
        ln = re.sub(r"^[-*•]\s*", "", ln)
        if not ln:
            continue
        if any(p.search(ln) for p in _AI_FILLER_RES):
            continue
        lines.append(ln)
    compact = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", compact).strip()


def _normalize_ai_summary(text: str) -> str:
    return clean_ai_summary(text)


def _ai_summary_to_lines(text: str, limit: int = 1400) -> list[str]:
    compact = _truncate(text, limit)
    if not compact:
        return []
    return [ln for ln in compact.split("\n") if ln.strip()]


def build_report_context(
    *,
    run_time: str,
    simulate_note: str = "",
    updates: list[Any] | None = None,
    watch_summary: dict[str, Any] | None = None,
    cookie_alert: dict[str, str] | None = None,
    user_posts: Any | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "title": "每日组合简报",
        "run_time": run_time,
        "simulate_note": simulate_note,
        "updates": [],
        "watch_summary": watch_summary,
        "cookie_alert": cookie_alert,
        "user_posts": None,
    }

    upd_list: list[dict[str, Any]] = []
    for upd in updates or []:
        batches_ctx: list[dict[str, Any]] = []
        for batch in upd.batches:
            records: list[dict[str, Any]] = []
            for item in batch.records:
                action = str(item.get("action", ""))
                if action == "买入":
                    badge, short = "buy", "买"
                elif action == "卖出":
                    badge, short = "sell", "卖"
                else:
                    badge, short = "other", action[:1] or "·"
                code = item.get("code", "")
                ai_lines: list[str] = []
                if action == "买入" and code in batch.ai_summaries:
                    ai_lines = _ai_summary_to_lines(batch.ai_summaries[code])
                records.append(
                    {
                        "action_short": short,
                        "badge_cls": badge,
                        "name": item.get("name", ""),
                        "code": code,
                        "price": item.get("price", "-"),
                        "weight_change": item.get("weight_change", "-"),
                        "ai_summary": bool(ai_lines),
                        "ai_lines": ai_lines,
                    }
                )
            batches_ctx.append(
                {"rebalance_time": batch.rebalance_time, "records": records}
            )
        upd_list.append(
            {
                "portfolio_name": upd.portfolio_name,
                "portfolio_id": upd.portfolio_id,
                "batches": batches_ctx,
            }
        )
    ctx["updates"] = upd_list

    if user_posts is not None and getattr(user_posts, "summary", ""):
        ctx["user_posts"] = {
            "post_count": getattr(user_posts, "post_count", 0),
            "summary": getattr(user_posts, "summary", ""),
            "summary_lines": _ai_summary_to_lines(
                getattr(user_posts, "summary", ""), limit=2000
            ),
        }
    return ctx


def render_report_html(context: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("report.html").render(**context)


def render_html_to_png(html: str, out_path: Path) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 playwright，请执行: pip install playwright && playwright install chromium"
        ) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 390, "height": 800},
            device_scale_factor=2,
        )
        page.set_content(html, wait_until="load")
        page.wait_for_function("() => document.fonts.ready")
        page.wait_for_timeout(300)
        height = page.evaluate("() => Math.ceil(document.body.scrollHeight)")
        page.set_viewport_size({"width": 390, "height": max(height, 400)})
        page.screenshot(path=str(out_path), full_page=True, type="png")
        browser.close()
    return out_path


def upload_image(path: Path) -> str:
    mode = DIGEST_IMAGE_UPLOAD
    if mode == "none":
        raise RuntimeError("DIGEST_IMAGE_UPLOAD=none，跳过上传")

    errors: list[str] = []
    if mode == "auto":
        backends: list[str] = []
        if all([OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_ENDPOINT, OSS_BUCKET_NAME]):
            backends.append("oss")
        if IMG_BB_API_KEY:
            backends.append("imgbb")
        backends.extend(["0x0", "transfer", "catbox"])
    else:
        backends = [mode]

    for backend in backends:
        try:
            if backend == "oss":
                return _upload_oss(path)
            if backend == "imgbb":
                return _upload_imgbb(path)
            if backend == "0x0":
                return _upload_0x0(path)
            if backend == "transfer":
                return _upload_transfer_sh(path)
            if backend == "catbox":
                return _upload_catbox(path)
            raise ValueError(f"未知上传方式: {backend}")
        except Exception as exc:
            errors.append(f"{backend}: {exc}")

    raise RuntimeError("图床上传均失败 — " + " | ".join(errors))


def _upload_oss(path: Path) -> str:
    try:
        import oss2
    except ImportError as exc:
        raise RuntimeError("未安装 oss2，请执行: pip install oss2") from exc

    if not all([OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_ENDPOINT, OSS_BUCKET_NAME]):
        raise RuntimeError("OSS 环境变量未配置完整")

    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, f"https://{OSS_ENDPOINT}", OSS_BUCKET_NAME)
    key = f"digest/{path.name}"
    bucket.put_object_from_file(key, str(path))
    if OSS_CUSTOM_DOMAIN:
        return f"{OSS_CUSTOM_DOMAIN.rstrip('/')}/{key}"
    return f"https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}/{key}"


def _upload_imgbb(path: Path) -> str:
    if not IMG_BB_API_KEY:
        raise RuntimeError("未配置 IMG_BB_API_KEY")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMG_BB_API_KEY, "image": data},
        timeout=90,
    )
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(body.get("error", {}).get("message", body))
    url = body.get("data", {}).get("url") or body.get("data", {}).get("display_url")
    if not url:
        raise RuntimeError(f"imgbb 无 URL: {body}")
    return str(url)


def _upload_0x0(path: Path) -> str:
    with path.open("rb") as f:
        resp = requests.post(
            "https://0x0.st",
            files={"file": (path.name, f, "image/png")},
            timeout=120,
        )
    url = resp.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"0x0.st: {resp.text[:120]}")
    return url


def _upload_transfer_sh(path: Path) -> str:
    with path.open("rb") as f:
        resp = requests.put(
            f"https://transfer.sh/{path.name}",
            data=f,
            headers={"Content-Type": "image/png"},
            timeout=120,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    url = resp.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"无效 URL: {url[:80]}")
    return url


def _upload_catbox(path: Path) -> str:
    with path.open("rb") as f:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (path.name, f, "image/png")},
            timeout=120,
        )
    url = resp.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"catbox: {resp.text[:120]}")
    return url


def send_dingtalk_image(title: str, image_url: str, caption: str = "") -> bool:
    if not DINGTALK_WEBHOOK:
        print("未配置 DINGTALK_WEBHOOK，跳过推送。")
        return False

    lines = []
    if DINGTALK_KEYWORD and DINGTALK_KEYWORD not in (caption or ""):
        lines.append(f"【{DINGTALK_KEYWORD}】")
    if caption.strip():
        lines.append(caption.strip())
    lines.append(f"![digest]({image_url})")
    md_text = "\n\n".join(lines)

    resp = requests.post(
        DINGTALK_WEBHOOK,
        headers={"Content-Type": "application/json"},
        json={"msgtype": "markdown", "markdown": {"title": title[:64], "text": md_text}},
        timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    if resp.status_code == 200 and body.get("errcode") == 0:
        print(f"钉钉图片消息推送成功: {image_url[:60]}…")
        return True
    print(f"钉钉图片推送失败: {body}")
    return False


def push_digest_image(
    *,
    run_time: str,
    simulate_note: str = "",
    updates: list[Any] | None = None,
    watch_summary: dict[str, Any] | None = None,
    cookie_alert: dict[str, str] | None = None,
    user_posts: Any | None = None,
    title: str = "每日组合",
) -> tuple[bool, Path | None]:
    context = build_report_context(
        run_time=run_time,
        simulate_note=simulate_note,
        updates=updates,
        watch_summary=watch_summary,
        cookie_alert=cookie_alert,
        user_posts=user_posts,
    )
    html = render_report_html(context)
    safe_time = re.sub(r"[^\d]", "", run_time)[:12] or "digest"
    out_path = OUTPUT_DIR / f"digest_{safe_time}.png"

    print("      渲染 HTML 简报图…")
    render_html_to_png(html, out_path)
    print(f"      已保存本地: {out_path}")

    try:
        image_url = upload_image(out_path)
        print(f"      图床 URL: {image_url[:72]}…")
    except Exception as exc:
        print(f"      图床上传失败: {exc}")
        return False, out_path

    caption = f"**{context['title']}** · {run_time}"
    if updates:
        names = "、".join(u.portfolio_name for u in updates[:2])
        if len(updates) > 2:
            names += f" 等{len(updates)}个"
        caption += f"\n\n组合调仓: {names}"
    elif watch_summary and watch_summary.get("count"):
        caption += f"\n\n组合调仓: 今晚无新调仓（已巡检 {watch_summary['count']} 个）"
    if user_posts and getattr(user_posts, "post_count", 0) > 0:
        caption += f"\n\n用户观点: 今日 {getattr(user_posts, 'post_count', 0)} 条发言 · AI 已提炼"
    if cookie_alert:
        caption += "\n\n⚠️ Cookie 已失效，请更新后重试"

    ok = send_dingtalk_image(title, image_url, caption=caption)
    return ok, out_path
