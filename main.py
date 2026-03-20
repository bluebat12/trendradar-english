import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# --- 1. 环境参数配置 ---
# 支持多个 Gemini Key 轮换：在 GitHub Secrets 中配置
#   GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3 ...（最多支持5个）
# 也兼容旧的单 Key 配置 GEMINI_API_KEY
def _load_gemini_keys() -> list:
    keys = []
    for i in range(1, 6):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys

GEMINI_KEYS  = _load_gemini_keys()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")
BARK_KEY     = os.getenv("BARK_KEY")
BARK_SERVER  = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")

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

def _call_gemini(api_key, prompt):
    """
    用单个 Key 尝试所有可用模型。
    成功返回文本；配额耗尽返回 '__QUOTA_EXCEEDED__'；其他失败返回 None。
    """
    model_attempts = [
        ("v1",     "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "你是一位资深行业分析师，擅长从 RSS 摘要中提取关键投资信息。\n\n" + prompt}]
            }
        ],
        "generationConfig": {"temperature": 0.7},
    }

    all_quota = True  # 是否所有失败都是 429

    for api_ver, model in model_attempts:
        url = (
            f"https://generativelanguage.googleapis.com/{api_ver}"
            f"/models/{model}:generateContent?key={api_key}"
        )
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                print(f"    ⚠️  {api_ver}/{model}: 配额耗尽 (429)")
                continue
            elif resp.status_code == 404:
                print(f"    ⚠️  {api_ver}/{model}: 不支持 (404)")
                all_quota = False
                continue
            else:
                print(f"    ❌ {api_ver}/{model}: 错误 [{resp.status_code}] {resp.text[:150]}")
                all_quota = False
                return None
        except Exception as e:
            print(f"    ❌ 请求异常 ({model}): {e}")
            all_quota = False

    return "__QUOTA_EXCEEDED__" if all_quota else None


def analyze_with_gemini(text):
    """轮换所有配置的 Gemini Key，配额耗尽自动切换下一个。"""
    if not GEMINI_KEYS:
        print("❌ 错误: 未找到任何 Gemini Key，请在 GitHub Secrets 中配置")
        return None

    print(f"🔑 共加载 {len(GEMINI_KEYS)} 个 Gemini Key")

    prompt = (
        "请用中文总结以下科技动态，并分析对市场的潜在影响。"
        "格式：先给出3句话的整体概述，再逐条列出每条要点（不超过5条）。\n\n"
        f"{text}"
    )

    for idx, key in enumerate(GEMINI_KEYS, start=1):
        key_label = f"Key-{idx} (...{key[-6:]})"
        print(f"  🔄 尝试 {key_label}")
        result = _call_gemini(key, prompt)

        if result == "__QUOTA_EXCEEDED__":
            print(f"  ⚠️  {key_label} 配额已耗尽，切换下一个 Key...")
            continue
        elif result is not None:
            print(f"  ✅ {key_label} 调用成功")
            return result
        else:
            print(f"  ❌ {key_label} 调用失败（非配额问题）")
            return None

    print("❌ 所有 Gemini Key 配额均已耗尽，请明天再试或添加新 Key")
    return None


def push_bark(title, body):
    """推送到 Bark（POST 方式）"""
    if not BARK_KEY:
        print("⚠️  未配置 BARK_KEY，跳过 Bark 推送")
        return False
    try:
        url = f"{BARK_SERVER}/push"
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],
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


def push_notion(title, summary, raw_news):
    """将摘要写入 Notion 数据库"""
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  未配置 NOTION_TOKEN 或 DATABASE_ID，跳过 Notion 写入")
        return False

    tz_cst = timezone(timedelta(hours=8))
    today_str = datetime.now(tz_cst).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    sources = list({line.split("】")[0].lstrip("【") for line in raw_news if "】" in line})
    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":    {"title":     [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "Date":    {"date":      {"start": today_str}},
            "Sources": {"rich_text": [{"text": {"content": ", ".join(sources)}}]},
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
    collected_news = []

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

    tz_cst = timezone(timedelta(hours=8))
    today_label = datetime.now(tz_cst).strftime("%Y-%m-%d")
    push_title = f"情报雷达 · {today_label}"

    push_bark(push_title, summary)
    push_notion(push_title, summary, collected_news)


if __name__ == "__main__":
    main()
