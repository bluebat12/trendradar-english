import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# --- 1. 环境参数配置 ---
def _load_gemini_keys():
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
#  诊断启动：打印所有配置状态
# --------------------------------------------------------------------------- #
def print_config_check():
    print("=" * 50)
    print("🔍 配置检查")
    print(f"  GEMINI Keys 数量: {len(GEMINI_KEYS)}")
    for i, k in enumerate(GEMINI_KEYS, 1):
        print(f"    Key-{i}: ...{k[-6:]} (长度={len(k)})")
    print(f"  NOTION_TOKEN: {'✅ 已配置 (长度=' + str(len(NOTION_TOKEN)) + ')' if NOTION_TOKEN else '❌ 未配置'}")
    print(f"  DATABASE_ID:  {'✅ 已配置 (长度=' + str(len(DATABASE_ID)) + ')' if DATABASE_ID else '❌ 未配置'}")
    print(f"  BARK_KEY:     {'✅ 已配置 (长度=' + str(len(BARK_KEY)) + ')' if BARK_KEY else '❌ 未配置'}")
    print(f"  BARK_SERVER:  {BARK_SERVER}")
    print("=" * 50)

# --------------------------------------------------------------------------- #
#  Gemini
# --------------------------------------------------------------------------- #
def _call_gemini(api_key, prompt):
    model_attempts = [
        ("v1",     "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "你是一位资深行业分析师，擅长从 RSS 摘要中提取关键投资信息。\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.7},
    }
    all_quota = True
    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=30)
            print(f"    [{api_ver}/{model}] HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                all_quota = True
                continue
            elif resp.status_code == 404:
                all_quota = False
                continue
            else:
                # 打印完整错误帮助诊断
                print(f"    ❌ 完整错误: {resp.text[:300]}")
                all_quota = False
                return None
        except Exception as e:
            print(f"    ❌ 请求异常: {e}")
            all_quota = False
    return "__QUOTA_EXCEEDED__" if all_quota else None


def analyze_with_gemini(text):
    if not GEMINI_KEYS:
        print("❌ 未找到任何 Gemini Key")
        return None
    prompt = (
        "请用中文总结以下科技动态，并分析对市场的潜在影响。"
        "格式：先给出3句话的整体概述，再逐条列出每条要点（不超过5条）。\n\n"
        f"{text}"
    )
    for idx, key in enumerate(GEMINI_KEYS, start=1):
        key_label = f"Key-{idx}(...{key[-6:]})"
        print(f"  🔄 尝试 {key_label}")
        result = _call_gemini(key, prompt)
        if result == "__QUOTA_EXCEEDED__":
            print(f"  ⚠️  {key_label} 配额耗尽，切换下一个...")
            continue
        elif result is not None:
            print(f"  ✅ {key_label} 成功，摘要长度={len(result)} 字符")
            return result
        else:
            print(f"  ❌ {key_label} 失败（非配额问题），终止")
            return None
    print("❌ 所有 Key 配额耗尽")
    return None

# --------------------------------------------------------------------------- #
#  Bark
# --------------------------------------------------------------------------- #
def push_bark(title, body):
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置，跳过")
        return False
    print(f"\n📲 开始 Bark 推送...")
    print(f"  URL: {BARK_SERVER}/push")
    print(f"  device_key 末6位: ...{BARK_KEY[-6:]}")
    print(f"  title: {title}")
    print(f"  body 长度: {len(body)} 字符")
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
        print(f"  HTTP 状态: {resp.status_code}")
        print(f"  响应内容: {resp.text[:300]}")
        if resp.status_code == 200 and resp.json().get("code") == 200:
            print("  ✅ Bark 推送成功")
            return True
        else:
            print("  ❌ Bark 推送失败（见上方响应内容）")
            return False
    except Exception as e:
        print(f"  ❌ Bark 推送异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  Notion
# --------------------------------------------------------------------------- #
def push_notion(title, summary, raw_news):
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  NOTION_TOKEN 或 DATABASE_ID 未配置，跳过")
        return False
    print(f"\n📓 开始 Notion 写入...")
    print(f"  DATABASE_ID 末8位: ...{DATABASE_ID[-8:]}")
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
        print(f"  HTTP 状态: {resp.status_code}")
        print(f"  响应内容: {resp.text[:500]}")
        if resp.status_code == 200:
            print(f"  ✅ Notion 写入成功")
            return True
        else:
            print("  ❌ Notion 写入失败（见上方响应内容）")
            return False
    except Exception as e:
        print(f"  ❌ Notion 写入异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def main():
    print_config_check()
    print("\n🚀 开始扫描情报源...")
    collected_news = []

    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            count = len(feed.entries[:3])
            print(f"   获取到 {count} 条")
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

    print("\n📝 AI 总结 (前200字):\n", summary[:200], "...")

    tz_cst = timezone(timedelta(hours=8))
    today_label = datetime.now(tz_cst).strftime("%Y-%m-%d")
    push_title = f"情报雷达 · {today_label}"

    push_bark(push_title, summary)
    push_notion(push_title, summary, collected_news)

    print("\n✅ 全部完成")

if __name__ == "__main__":
    main()
