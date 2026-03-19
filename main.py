import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# --- 1. 环境参数配置 ---
GEMINI_KEY    = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN  = os.getenv("NOTION_TOKEN")
DATABASE_ID   = os.getenv("DATABASE_ID")
BARK_KEY      = os.getenv("BARK_KEY")
BARK_SERVER   = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")

# --- 2. RSS 情报源 ---
RSS_FEEDS = [
    {"name": "Intel_Finance",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech",      "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press",      "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
]

# --------------------------------------------------------------------------- #
#  核心功能函数
# --------------------------------------------------------------------------- #

def analyze_with_gemini(text: str) -> str | None:
    """调用 Gemini REST API 生成中文摘要，兼容所有 Key 类型"""
    if not GEMINI_KEY:
        print("❌ 错误: 找不到 GEMINI_API_KEY，请在 GitHub Secrets 中配置")
        return None

    # 依次尝试可用的模型（新 key 优先用 v1，旧 key 用 v1beta）
    model_attempts = [
        ("v1",     "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]

    prompt = (
        "请用中文总结以下科技动态，并分析对市场的潜在影响。"
        "格式：先给出3句话的整体概述，再逐条列出每条要点（不超过5条）。\n\n"
        f"{text}"
    )

    for api_ver, model in model_attempts:
        url = (
            f"https://generativelanguage.googleapis.com/{api_ver}"
            f"/models/{model}:generateContent?key={GEMINI_KEY}"
        )
        payload = {
            "system_instruction": {
                "parts": [{"text": "你是一位资深行业分析师，擅长从 RSS 摘要中提取关键投资信息。"}]
            },
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7},
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                result = data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"✅ 使用模型: {api_ver}/{model}")
                return result
            elif resp.status_code in (404, 429):
                print(f"⚠️  {api_ver}/{model} 不可用 ({resp.status_code})，尝试下一个...")
                continue
            else:
                print(f"❌ AI 分析失败 [{resp.status_code}]: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"❌ 请求异常 ({model}): {e}")
            continue

    print("❌ 所有模型均不可用，请检查 GEMINI_API_KEY 是否有效")
    return None


def push_bark(title: str, body: str) -> bool:
    """
    推送到 Bark（POST 方式，避免 URL 过长）
    文档: https://bark.day.app/#/tutorial
    """
    if not BARK_KEY:
        print("⚠️  未配置 BARK_KEY，跳过 Bark 推送")
        return False
    try:
        url = f"{BARK_SERVER}/push"
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],   # Bark 建议 body ≤ 2000 字符
            "sound": "minuet",
            "group": "情报雷达",
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("code") == 200:
            print("📲 Bark 推送成功")
            return True
        else:
            print(f"⚠️  Bark 推送失败: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Bark 推送异常: {e}")
        return False


def push_notion(title: str, summary: str, raw_news: list[str]) -> bool:
    """
    将摘要写入 Notion 数据库
    数据库需要包含以下属性：
      - Name (title 类型)
      - Summary (rich_text 类型)
      - Date (date 类型)
      - Sources (rich_text 类型)
    """
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  未配置 NOTION_TOKEN 或 DATABASE_ID，跳过 Notion 写入")
        return False

    # 北京时间
    tz_cst = timezone(timedelta(hours=8))
    today_str = datetime.now(tz_cst).strftime("%Y-%m-%d")

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # 提取来源名称列表
    sources = list({line.split("】")[0].lstrip("【") for line in raw_news if "】" in line})

    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {
                "title": [{"text": {"content": title}}]
            },
            "Summary": {
                "rich_text": [{"text": {"content": summary[:2000]}}]
            },
            "Date": {
                "date": {"start": today_str}
            },
            "Sources": {
                "rich_text": [{"text": {"content": ", ".join(sources)}}]
            },
        },
    }

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json=page_data,
            timeout=20,
        )
        if resp.status_code == 200:
            print(f"📓 Notion 写入成功: {resp.json().get('url', '')}")
            return True
        else:
            print(f"⚠️  Notion 写入失败: {resp.status_code} {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"❌ Notion 写入异常: {e}")
        return False


# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #

def main():
    print("🚀 开始扫描情报源...")
    collected_news: list[str] = []

    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:3]:
                news_item = (
                    f"【{feed_info['name']}】{entry.title}\n"
                    f"摘要: {entry.get('summary', '')[:200]}"
                )
                collected_news.append(news_item)
        except Exception as e:
            print(f"⚠️  拉取 {feed_info['name']} 失败: {e}")

    if not collected_news:
        print("📭 今日无新动态，退出")
        return

    print(f"\n📰 共收集到 {len(collected_news)} 条新闻，正在生成 AI 总结...")
    full_text = "\n\n".join(collected_news)
    summary = analyze_with_gemini(full_text)

    if not summary:
        print("⚠️  未能生成 AI 总结，流程终止")
        return

    print("\n📝 AI 总结:\n", summary)

    # 推送标题（含日期）
    tz_cst = timezone(timedelta(hours=8))
    today_label = datetime.now(tz_cst).strftime("%Y-%m-%d")
    push_title = f"情报雷达 · {today_label}"

    # 同步推送 Bark + Notion
    push_bark(push_title, summary)
    push_notion(push_title, summary, collected_news)


if __name__ == "__main__":
    main()
