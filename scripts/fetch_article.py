"""爬取雪球单篇文章/帖子内容及评论。

用法:
    cd backend
    python ../scripts/fetch_article.py

修改下方 POST_URL 为你需要爬取的文章链接即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 修复 Windows 终端中文/特殊字符编码问题
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from xueqiu.integrations.xueqiu.client import XueQiuApiClient, XueQiuApiError
from xueqiu.integrations.xueqiu.auth import load_cookie
from xueqiu.integrations.xueqiu.posts import fetch_post_detail, fetch_post_comments

# ========== 修改这里 ==========
POST_URL = "https://xueqiu.com/7845696728/319291242"
MAX_COMMENT_PAGES = 5  # 最多拉取多少页评论
# =============================


def parse_post_url(url: str) -> tuple[str, str]:
    """从 xueqiu.com/{user_id}/{post_id} 中提取 user_id 和 post_id"""
    url = url.rstrip("/")
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    raise ValueError(f"无法解析文章 URL: {url}")


def main() -> None:
    user_id, post_id = parse_post_url(POST_URL)
    print(f"文章链接: {POST_URL}")
    print(f"用户 ID: {user_id}  帖子 ID: {post_id}")
    print("-" * 60)

    # 1. 加载 Cookie & 创建客户端
    try:
        cookie = load_cookie()
        print("Cookie 加载成功")
    except RuntimeError as exc:
        print(f"Cookie 加载失败: {exc}")
        return

    client = XueQiuApiClient(cookie=cookie)

    # 2. 获取文章详情
    print("正在获取文章内容...")
    post = fetch_post_detail(client, post_id, user_id=user_id)

    if post is None:
        print("获取文章失败！请检查:")
        print("  1. Cookie 是否有效 -> 运行 python ../scripts/xueqiu_login.py 重新登录")
        print("  2. 文章 URL 是否正确")
        return

    print(f"\n{'=' * 60}")
    print(f"作者: {post.user_name} (uid={post.user_id})")
    print(f"发布时间: {post.created_at}")
    print(f"转发: {post.retweet_count} | 评论: {post.reply_count} | 点赞: {post.like_count}")
    if post.source:
        print(f"来源: {post.source}")
    print(f"{'=' * 60}")
    print(f"\n{post.text}")
    print(f"\n{'=' * 60}")

    # 3. 获取评论
    if post.reply_count > 0:
        print(f"\n正在获取评论 (最多 {MAX_COMMENT_PAGES} 页)...")
        try:
            comments = fetch_post_comments(
                client, post_id, max_pages=MAX_COMMENT_PAGES, user_id=user_id,
            )
        except XueQiuApiError as exc:
            print(f"获取评论失败: {exc}")
            comments = []

        if comments:
            print(f"\n--- 评论 ({len(comments)} 条) ---")
            for i, comment in enumerate(comments, 1):
                print(f"\n[{i}] {comment.user_name} | {comment.created_at}")
                print(f"    {comment.text}")
        else:
            print("(无评论或获取失败)")
    else:
        print("\n(该帖子暂无评论)")

    print(f"\n完成!")


if __name__ == "__main__":
    main()
