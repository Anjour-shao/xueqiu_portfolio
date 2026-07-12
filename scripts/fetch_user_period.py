"""采集指定用户指定时间段内的帖子及评论，写入数据库。

用法:
    cd backend
    python ../scripts/fetch_user_period.py                          # 默认：用户 7845696728，2025-01 ~ 2026-01
    python ../scripts/fetch_user_period.py --user-id 123456         # 指定用户
    python ../scripts/fetch_user_period.py --start 2025-06-01       # 指定起始日期
    python ../scripts/fetch_user_period.py --end 2026-06-30         # 指定截止日期
    python ../scripts/fetch_user_period.py --no-comments            # 仅拉帖子不拉评论
    python ../scripts/fetch_user_period.py --max-pages 200          # 最大翻页数
    python ../scripts/fetch_user_period.py --dry-run                # 仅打印不写入
    python ../scripts/fetch_user_period.py --resume                 # 从上次中断处续跑
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# 修复 Windows 终端中文/特殊字符编码问题
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text

from xueqiu.storage.db import engine, init_db
from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.auth import load_cookie
from xueqiu.integrations.xueqiu.posts import (
    parse_post,
    extract_post_list,
    fetch_post_comments,
)

DEFAULT_USER_ID = "7845696728"
DEFAULT_START = "2025-01-01"
DEFAULT_END = "2026-01-31"
USER_TIMELINE_URL = "https://api.xueqiu.com/v4/statuses/user_timeline.json"
CHECKPOINT_FILE = ROOT / "data" / ".fetch_checkpoint.json"


def parse_date(s: str) -> str:
    s = s.strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"日期格式错误: {s}，应为 YYYY-MM-DD")


def post_in_range(created_at: str, start: str, end: str) -> bool:
    if not created_at:
        return False
    return start <= created_at[:10] <= end


def post_before_range(created_at: str, start: str) -> bool:
    if not created_at:
        return False
    return created_at[:10] < start


def _load_checkpoint(user_id: str) -> int:
    """加载上次中断的页码，返回下一页应从哪开始。"""
    if not CHECKPOINT_FILE.exists():
        return 1
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        if data.get("user_id") == user_id:
            last_page = int(data.get("last_page", 0))
            return last_page + 1
    except (json.JSONDecodeError, ValueError):
        pass
    return 1


def _save_checkpoint(user_id: str, page: int) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(
        json.dumps({"user_id": user_id, "last_page": page, "updated_at": datetime.now().isoformat()},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def _clear_checkpoint() -> None:
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


def _fetch_timeline_page_with_retry(
    client: XueQiuApiClient,
    user_id: str,
    page: int,
    count: int = 20,
    max_retries: int = 5,
) -> list:
    """带退避重试的 timeline 分页请求。"""
    last_error = None
    for attempt in range(max_retries):
        try:
            data = client.get_json_with_retry(
                USER_TIMELINE_URL,
                params={"user_id": str(user_id), "page": page, "count": count},
                max_retries=3,
                delay=(1.5, 3.0),
            )
            return [p for p in (parse_post(item) for item in extract_post_list(data)) if p]
        except XueQiuApiError as exc:
            last_error = exc
            msg = str(exc)
            # HTML 响应 / 限流 → 等久一点再试
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 15 + random.uniform(3, 10)
                print(f"      请求失败（{msg[:60]}...），{wait:.0f}s 后重试 ({attempt + 2}/{max_retries})")
                time.sleep(wait)
                # 重新预热（可能 cookie 需要刷新）
                try:
                    client._warmed = False
                    client.warm_up()
                except Exception:
                    pass
                continue
    raise last_error  # type: ignore[misc]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="采集雪球用户指定时间段帖子及评论")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="用户 ID")
    parser.add_argument("--start", default=DEFAULT_START, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="截止日期 YYYY-MM-DD")
    parser.add_argument("--no-comments", action="store_true", help="不拉取评论")
    parser.add_argument("--max-pages", type=int, default=500, help="最大翻页数")
    parser.add_argument("--dry-run", action="store_true", help="仅打印不写入数据库")
    parser.add_argument("--resume", action="store_true", help="从上次中断处续跑")
    parser.add_argument("--break-interval", type=int, default=15, help="每 N 页休息一次")
    parser.add_argument("--break-duration", type=float, default=20.0, help="休息时长(秒)")
    args = parser.parse_args()

    user_id = args.user_id.strip()
    start_date = parse_date(args.start)
    end_date = parse_date(args.end)
    with_comments = not args.no_comments

    # 断点续跑
    start_page = _load_checkpoint(user_id) if args.resume else 1

    print(f"=== 雪球帖子采集 ===")
    print(f"用户 ID: {user_id}")
    print(f"时间范围: {start_date} ~ {end_date}")
    print(f"拉取评论: {'是' if with_comments else '否'}")
    print(f"起始页码: {start_page} {'(续跑)' if start_page > 1 else ''}")
    print(f"最大页数: {args.max_pages}")
    if args.dry_run:
        print("模式: DRY RUN（仅打印不写入）")
    print()

    if not args.dry_run:
        init_db()

    try:
        cookie = load_cookie()
    except RuntimeError as exc:
        print(f"Cookie 加载失败: {exc}")
        return

    client = XueQiuApiClient(cookie=cookie)

    total_posts = 0
    total_comments = 0
    stopped_early = False
    consecutive_empty = 0

    for page in range(start_page, args.max_pages + 1):
        # 每 N 页休息一次
        if page > start_page and (page - start_page) % args.break_interval == 0:
            print(f"  [休息] 已连续请求 {args.break_interval} 页，休息 {args.break_duration:.0f}s...")
            time.sleep(args.break_duration)

        try:
            posts = _fetch_timeline_page_with_retry(client, user_id, page=page, count=20)
        except XueQiuApiError as exc:
            print(f"[页 {page}] API 错误（已重试耗尽）: {exc}")
            _save_checkpoint(user_id, page - 1)
            print(f"  进度已保存至页 {page - 1}，稍后可用 --resume 续跑")
            break

        if not posts:
            consecutive_empty += 1
            print(f"[页 {page}] 无数据。")
            if consecutive_empty >= 3:
                print("  连续 3 页无数据，停止。")
                break
            time.sleep(random.uniform(2.0, 4.0))
            continue

        consecutive_empty = 0

        # 检查这一页的日期范围
        dates_on_page = [p.created_at[:10] for p in posts if p.created_at]
        min_date = min(dates_on_page) if dates_on_page else ""
        max_date = max(dates_on_page) if dates_on_page else ""

        in_range = [p for p in posts if post_in_range(p.created_at, start_date, end_date)]
        before_range = [p for p in posts if post_before_range(p.created_at, start_date)]
        after_range = [p for p in posts if p.created_at[:10] > end_date]
        after_count = len(after_range)

        if after_range and not in_range:
            print(f"[页 {page}] {after_count} 条超出范围（{min_date} ~ {max_date}），继续翻页...")

        if in_range:
            print(f"[页 {page}] 命中 {len(in_range)} 条（{min_date} ~ {max_date}）:")
            for p in in_range:
                preview = p.text[:60].replace("\n", " ")
                print(f"  [{p.id}] {p.created_at} | {preview}...")
                total_posts += 1

                if not args.dry_run:
                    _save_post(p)

                # 拉取评论
                if with_comments and p.reply_count > 0:
                    try:
                        comments = fetch_post_comments(
                            client, p.id, max_pages=10, user_id=user_id,
                        )
                    except XueQiuApiError as exc:
                        print(f"    评论获取失败: {exc}")
                        comments = []

                    author_uid = int(user_id)
                    for c in comments:
                        total_comments += 1
                        if not args.dry_run:
                            _save_comment(c, p.id, author_uid)
                    if comments:
                        print(f"    + {len(comments)} 条评论")
                    time.sleep(random.uniform(0.5, 1.0))

        if before_range and not in_range:
            print(f"[页 {page}] {len(before_range)} 条早于 {start_date}，采集完成。")
            stopped_early = True
            break

        # 检查是否已到末尾
        if dates_on_page and min_date < start_date:
            stopped_early = True
            break

        # 保存 checkpoint
        _save_checkpoint(user_id, page)

        # 页间延迟
        delay = random.uniform(2.0, 4.0)
        time.sleep(delay)

    # 汇总
    if stopped_early:
        _clear_checkpoint()

    print()
    print(f"=== 采集完成 ===")
    print(f"帖子: {total_posts} 条")
    print(f"评论: {total_comments} 条")
    if stopped_early:
        print("停止原因: 已翻到早于起始日期的帖子")
    else:
        print(f"停止原因: 到达第 {args.max_pages} 页上限或遇错中断")


def _save_post(post) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT IGNORE INTO xueqiu_posts
                   (post_id, user_id, user_name, created_at, text,
                    retweet_count, reply_count, like_count, source, target, fetched_at)
                   VALUES (:pid, :uid, :uname, :cat, :txt,
                           :rc, :rpc, :lc, :src, :tgt, NOW())"""
            ),
            {
                "pid": post.id, "uid": post.user_id or 0, "uname": post.user_name,
                "cat": post.created_at, "txt": post.text,
                "rc": post.retweet_count, "rpc": post.reply_count, "lc": post.like_count,
                "src": post.source or "", "tgt": post.target or "",
            },
        )


def _save_comment(comment, post_id: int, author_uid: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT IGNORE INTO xueqiu_comments
                   (comment_id, post_id, user_id, user_name, created_at, text,
                    is_author_reply, fetched_at)
                   VALUES (:cid, :pid, :uid, :uname, :cat, :txt, :is_author, NOW())"""
            ),
            {
                "cid": comment.id, "pid": post_id,
                "uid": comment.user_id or 0, "uname": comment.user_name,
                "cat": comment.created_at, "txt": comment.text,
                "is_author": 1 if (comment.user_id and int(comment.user_id) == author_uid) else 0,
            },
        )


if __name__ == "__main__":
    main()
