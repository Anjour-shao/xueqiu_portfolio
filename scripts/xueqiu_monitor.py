"""雪球调仓巡检：轮询库内 ZH 组合最新调仓，钉钉推送并可调用 DeepSeek 做舆情摘要。

适合 crontab 定时跑。需配置 Cookie；可选 DINGTALK_WEBHOOK、DEEPSEEK_API_KEY。

用法:
    cd backend
    python ../scripts/xueqiu_monitor.py
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import requests
from openai import OpenAI

from xueqiu.api.services import list_xueqiu_portfolio_codes
from xueqiu.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DINGTALK_WEBHOOK
from xueqiu.integrations.xueqiu.client import XueQiuApiClient
from xueqiu.integrations.xueqiu.portfolio import fetch_portfolio_rebalance
from xueqiu.integrations.xueqiu.posts import fetch_stock_posts
from xueqiu.storage.db import init_db


# 仅这些关注组合发生“今日调仓”时推送钉钉；本地同步/记录逻辑不依赖该列表。
WATCHED_PORTFOLIOS: dict[str, str] = {
    "ZH3337164": "三年10倍",
    "ZH3365207": "5年退休计划",
    "ZH3472193": "利润断层",
    "ZH3558598": "2026垃圾站",
    "ZH3300885": "AI概念",
    "ZH1236871": "赌出个自由",
    "ZH3393223": "血战到底",
    "ZH3483962": "投资界老萨满",
    "ZH3610939": "友谊的大船",
    "ZH3104761": "景气组合",
    "ZH3437281": "实仓跟踪",
    "ZH3476690": "科技",
    "ZH3530915": "复利中线",
    "ZH3546223": "争取五倍",
    "ZH3585531": "2026年十倍股",
    "ZH3484875": "钽坦",
}


def _deepseek_client() -> OpenAI | None:
    if not DEEPSEEK_API_KEY:
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def _is_recent_post(created_at: str, days: int = 3) -> bool:
    if not created_at:
        return False
    try:
        post_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.now() - post_time <= timedelta(days=days)


def clean_xueqiu_comments(raw_comments_list: list[str]) -> list[str]:
    cleaned_comments = []
    for text in raw_comments_list:
        text = re.sub(r"\$.*?\$", "", text)
        text = re.sub(r"@\S+", "", text)
        text = re.sub(r"\s+", " ", text)
        text = text.strip()
        if len(text) >= 10:
            cleaned_comments.append(text)
    return cleaned_comments


def fetch_stock_comments(client: XueQiuApiClient, symbol: str, max_pages: int = 5) -> str:
    print(f"      抓取舆情: {symbol}…")
    try:
        posts = fetch_stock_posts(client, symbol, max_pages=max_pages, page_size=20)
        comments = [
            post.text
            for post in posts
            if post.text and _is_recent_post(post.created_at, days=3)
        ]
        unique_comments = list(dict.fromkeys(comments))
        cleaned_comments = clean_xueqiu_comments(unique_comments)
        final_text = ""
        for idx, text in enumerate(cleaned_comments, 1):
            final_text += f"{idx}. {text}\n"
        print(f"      {symbol} 获取 {len(cleaned_comments)} 条近期讨论。")
        return final_text
    except Exception as exc:
        print(f"      股票舆情抓取失败: {exc}")
        return ""


def call_deepseek_summary(stock_name: str, comments: str) -> str:
    if not comments or len(comments) < 20:
        return "近期无足够讨论数据或热度较低。"

    client = _deepseek_client()
    if client is None:
        return "未配置 DEEPSEEK_API_KEY，跳过 AI 分析。"

    prompt = f"""
    你是一个专业的A股量化投研助手。请根据以下雪球用户的近期原生评论，深度分析当前市场对【{stock_name}】的共识与分歧。

    输出要求：
    1. 请分为三个维度进行结构化总结：【看多逻辑】、【看空隐患】、【关键事件/基本面追踪】。
    2. 提取评论中提及的核心产业逻辑、订单传闻或财报预期。
    3. 过滤纯情绪宣泄，只保留有信息量的论点。
    4. 字数控制在300-500字左右，格式要求使用 Markdown 小标题与列表，排版清晰易读。

    评论原始数据：
    {comments}
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个客观严谨的金融AI分析师，擅长从噪音中提取核心商业和市场逻辑。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"      DeepSeek 调用失败: {exc}")
        return "AI 分析生成异常。"


def send_dingtalk_msg(portfolio_id, portfolio_name, rebalance_time, rebalances, ai_summaries):
    if not DINGTALK_WEBHOOK:
        print(f"[{portfolio_id}] 未配置 DINGTALK_WEBHOOK，跳过推送。")
        return

    display_name = WATCHED_PORTFOLIOS.get(portfolio_id, portfolio_name)
    md_text = "### 雪球关注组合调仓提醒\n"
    md_text += f"**组合名称：** {display_name} ({portfolio_id})\n"
    md_text += f"**调仓时间：** {rebalance_time}\n\n"
    md_text += "> 本通知仅展示关注组合调仓与买入标的评论区研判，不展示本地跟单持仓收益。\n\n---\n"

    for item in rebalances:
        action_icon = "卖出" if item["action"] == "卖出" else "买入/加仓"
        md_text += f"#### {action_icon} | {item['action']} | {item['name']} ({item['code']})\n"
        md_text += f"- **成交价:** {item['price']} | **组合仓位变化:** {item['weight_change']}\n\n"

        if item["action"] == "买入" and item["code"] in ai_summaries:
            md_text += "> **雪球评论区 / DeepSeek 研判：**\n"
            summary_lines = ai_summaries[item["code"]].split("\n")
            formatted_summary = "\n> ".join(summary_lines)
            md_text += f"> {formatted_summary}\n\n"

    data = {
        "msgtype": "markdown",
        "markdown": {"title": f"调仓提醒: {display_name}", "text": md_text},
    }
    try:
        headers = {"Content-Type": "application/json"}
        requests.post(DINGTALK_WEBHOOK, headers=headers, data=json.dumps(data), timeout=15)
        print(f"[{portfolio_id}] 钉钉消息推送成功。")
    except Exception as exc:
        print(f"[{portfolio_id}] 钉钉推送失败: {exc}")


def check_single_portfolio(client: XueQiuApiClient, portfolio_id: str) -> None:
    display_name = WATCHED_PORTFOLIOS.get(portfolio_id, portfolio_id)
    print(f"\n[{portfolio_id}] {display_name} 正在巡检…")

    try:
        crawled = fetch_portfolio_rebalance(portfolio_id, client=client)
    except RuntimeError as exc:
        print(f"[{portfolio_id}] {exc}")
        return
    except Exception as exc:
        print(f"[{portfolio_id}] 巡检发生异常: {exc}")
        return

    rebalance_time = crawled["rebalance_time"]
    today_str = datetime.now().strftime("%Y-%m-%d")
    if today_str not in rebalance_time:
        print(f"[{portfolio_id}] 今日无调仓 (最新为 {rebalance_time})，略过。")
        return

    print(f"[{portfolio_id}] 发现今日调仓，启动分析…")
    rebalancing_records = crawled["records"]
    if not rebalancing_records:
        return

    ai_summaries = {}
    for record in rebalancing_records:
        if record["action"] == "买入":
            print(f"      分析买入标的: {record['name']}")
            comments = fetch_stock_comments(client, record["code"])
            summary = call_deepseek_summary(record["name"], comments)
            ai_summaries[record["code"]] = summary

    send_dingtalk_msg(
        portfolio_id,
        crawled["portfolio_name"],
        rebalance_time,
        rebalancing_records,
        ai_summaries,
    )


def main() -> None:
    init_db()
    db_portfolios = set(list_xueqiu_portfolio_codes())
    target_portfolios = list(WATCHED_PORTFOLIOS.keys())
    missing = [pid for pid in target_portfolios if pid not in db_portfolios]

    print(f"=== 雪球 AI 监控启动 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")
    print("钉钉关注组合:")
    for pid in target_portfolios:
        suffix = "（数据库暂未导入，本次仍尝试在线巡检）" if pid in missing else ""
        print(f"  - {pid} {WATCHED_PORTFOLIOS[pid]}{suffix}")

    client = XueQiuApiClient()
    total = len(target_portfolios)
    for index, pid in enumerate(target_portfolios, 1):
        check_single_portfolio(client, pid)
        if index < total:
            time.sleep(random.uniform(1.0, 2.0))

    print("=== 所有关注组合巡检完毕 ===")


if __name__ == "__main__":
    main()
