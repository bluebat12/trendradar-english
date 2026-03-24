"""
TrendRadar Intelligence Scout
目标：每天早上9点（北京时间）抓取全球重要科技/财经/政策新闻，
      AI 归纳为中文日报，同时推送 Bark + 写入 Notion。
运行频率：每天一次（trendradar.yml 设定 cron: '0 1 * * *'）
"""
import os
import re
import json
import hashlib
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------- #
#  1. 配置
# --------------------------------------------------------------------------- #
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

SENT_FILE = "sent_news_hashes.json"

# --------------------------------------------------------------------------- #
#  2. 全球重要新闻 RSS 源（覆盖科技、财经、政策、半导体）
# --------------------------------------------------------------------------- #
RSS_FEEDS = [
    # 综合科技
    {"name": "Reuters Tech",      "url": "https://feeds.reuters.com/reuters/technologyNews"},
    {"name": "BBC Tech",          "url": "http://feeds.bbci.co.uk/news/technology/rss.xml"},
    {"name": "CNBC Tech",         "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "Ars Technica",      "url": "https://feeds.arstechnica.com/arstechnica/index"},
    # 财经
    {"name": "Reuters Business",  "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "FT Tech",           "url": "https://www.ft.com/technology?format=rss"},
    # AI
    {"name": "MIT Tech Review",   "url": "https://www.technologyreview.com/feed/"},
    # 政策/监管
    {"name": "FDA Press",         "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
    # 🟢 低频层（从 alerts_radar.py 移过来，每天汇总即可）
    # AI/量子/核聚变 学术突破
    {"name": "Science_Breakthrough", "url": "https://www.google.com/alerts/feeds/02859553752789820389/12658923786557718878"},
    # 自动驾驶/机器人/基因编辑 里程碑
    {"name": "Tech_Milestone",        "url": "https://www.google.com/alerts/feeds/02859553752789820389/2601960625698782407"},
    # 生物年龄逆转/senolytics
    {"name": "Longevity_Science",     "url": "https://www.google.com/alerts/feeds/02859553752789820389/11330977868525907062"},
    # longevity 临床试验
    {"name": "Longevity_Trial",       "url": "https://www.google.com/alerts/feeds/02859553752789820389/15131987033820237330"},
    # 疫情预警/AI病原体检测
    {"name": "Pandemic_Watch",        "url": "https://www.google.com/alerts/feeds/02859553752789820389/15077444616124068808"},
]

# --------------------------------------------------------------------------- #
#  3. 去重（按日期，每天重置，不跨天积累）
# --------------------------------------------------------------------------- #
def load_sent_hashes():
    if os.path.exists(SENT_FILE):
        try:
            with open(SENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只保留今天的记录，昨天的自动清除（避免积累导致漏推）
            today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            data = {k: v for k, v in data.items() if v.startswith(today)}
            return set(data.keys()), data
        except:
            pass
    return set(), {}

def save_sent_hashes(d):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def get_hash(title, source):
    return hashlib.md5(f"{source}:{title}".encode()).hexdigest()

def clean_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()

# --------------------------------------------------------------------------- #
#  4. Gemini AI（一次调用归纳所有新闻，节省配额）
# --------------------------------------------------------------------------- #
def _call_gemini_once(api_key, prompt):
    model_attempts = [
        ("v1",     "gemini-2.0-flash"),
        ("v1",     "gemini-2.0-flash-lite"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1500},
    }
    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=45)
            print(f"    [{api_ver}/{model}] HTTP {resp.status_code}")
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code in (404, 429):
                continue
            else:
                print(f"    ❌ {resp.text[:150]}")
                return "__ERROR__"
        except Exception as e:
            print(f"    ❌ 异常: {e}")
            return "__ERROR__"
    return "__QUOTA_EXCEEDED__"

def summarize_all_news(news_list):
    """把所有新新闻一次性交给 AI 归纳，只消耗1次配额"""
    if not GEMINI_KEYS or not news_list:
        return None

    # 构建新闻列表文本
    news_text = ""
    for i, n in enumerate(news_list, 1):
        news_text += f"{i}. [{n['source']}] {n['title']}\n   {n['summary'][:200]}\n\n"

    prompt = f"""你是全球科技与财经情报分析师。以下是今日抓取的最新新闻（共{len(news_list)}条）：

{news_text}

请完成以下任务：
1. 筛选出其中最重要的5-8条新闻（优先考虑：重大政策、芯片/半导体动态、AI进展、市场重大事件）
2. 用简体中文写成今日情报日报
3. 格式如下：

📅 今日情报摘要

🔺 重点新闻：
• [来源] 标题中文翻译 — 一句话分析意义

📊 市场影响：
整体一段话分析今日动态对科技/半导体市场的潜在影响（3-4句）

控制在500字以内，语言精炼专业。"""

    print(f"🤖 开始 AI 归纳（共 {len(news_list)} 条新闻）...")
    for idx, key in enumerate(GEMINI_KEYS, 1):
        print(f"  🔄 尝试 Key-{idx}(...{key[-6:]})")
        result = _call_gemini_once(key, prompt)
        if result == "__QUOTA_EXCEEDED__":
            print(f"  ⚠️  Key-{idx} 配额耗尽，切换...")
            continue
        elif result == "__ERROR__":
            print(f"  ❌ Key-{idx} 错误，终止")
            return None
        else:
            print(f"  ✅ Key-{idx} 成功")
            return result

    print("❌ 所有 Key 不可用，降级为翻译模式")
    return None

# --------------------------------------------------------------------------- #
#  5. Google Translate 免费翻译（AI 失败时的保底）
# --------------------------------------------------------------------------- #
def translate_batch(texts):
    """批量翻译，合并成一次请求"""
    combined = "\n||||\n".join(texts)
    try:
        params = {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": combined[:4000]}
        resp = requests.get("https://translate.googleapis.com/translate_a/single", params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            translated = "".join([seg[0] for seg in data[0] if seg[0]])
            return translated.split("||||")
    except Exception as e:
        print(f"  ⚠️ 翻译失败: {e}")
    return texts  # 失败则返回原文

# --------------------------------------------------------------------------- #
#  6. Bark 推送
# --------------------------------------------------------------------------- #
def push_bark(title, body):
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置")
        return False
    try:
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],
            "sound": "minuet",
            "group": "情报雷达",
            "isArchive": 1,
        }
        resp = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("code") == 200:
            print("  ✅ Bark 推送成功")
            return True
        else:
            print(f"  ❌ Bark 失败: {data}")
            return False
    except Exception as e:
        print(f"  ❌ Bark 异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  7. Notion 写入
# --------------------------------------------------------------------------- #
def push_notion(title, summary):
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  Notion 未配置，跳过")
        return False
    tz_cst = timezone(timedelta(hours=8))
    today_str = datetime.now(tz_cst).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":    {"title":     [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "Date":    {"date":      {"start": today_str}},
            "Sources": {"rich_text": [{"text": {"content": "TrendRadar Daily"}}]},
        },
    }
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data, timeout=20)
        if resp.status_code == 200:
            print(f"  ✅ Notion 写入成功")
            return True
        else:
            print(f"  ❌ Notion 失败 {resp.status_code}: {resp.json().get('message','')}")
            return False
    except Exception as e:
        print(f"  ❌ Notion 异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  8. 主流程
# --------------------------------------------------------------------------- #
def main():
    tz_cst = timezone(timedelta(hours=8))
    now = datetime.now(tz_cst)
    today = now.strftime("%Y-%m-%d")

    print("=" * 55)
    print(f"🌍 TrendRadar 全球情报扫描 — {today}")
    print(f"   Gemini Keys: {len(GEMINI_KEYS)} 个")
    print(f"   Notion: {'✅' if NOTION_TOKEN else '❌'}")
    print(f"   Bark:   {'✅' if BARK_KEY else '❌'}")
    print("=" * 55)

    # 加载今日已推送记录
    sent_hashes, sent_dict = load_sent_hashes()
    print(f"📋 今日已推送: {len(sent_hashes)} 条\n")

    # 抓取新闻
    new_news = []
    for feed_info in RSS_FEEDS:
        print(f"📡 拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            count = 0
            for entry in feed.entries[:5]:  # 每个源最多取5条
                title = clean_html(entry.title)
                h = get_hash(title, feed_info["name"])
                if h not in sent_hashes:
                    new_news.append({
                        "source": feed_info["name"],
                        "title": title,
                        "summary": clean_html(entry.get("summary", ""))[:300],
                        "link": entry.get("link", ""),
                        "hash": h,
                    })
                    sent_dict[h] = now.isoformat()
                    count += 1
            print(f"   → {count} 条新内容")
        except Exception as e:
            print(f"   ⚠️ 失败: {e}")

    print(f"\n📊 共发现 {len(new_news)} 条新新闻")

    if not new_news:
        print("📭 今日无新动态，退出")
        return

    # 保存去重记录
    save_sent_hashes(sent_dict)

    # AI 一次性归纳所有新闻（只消耗1次配额）
    summary = summarize_all_news(new_news)

    push_title = f"🌍 今日情报 {today}"

    if summary:
        # AI 成功：推送日报
        push_bark(push_title, summary)
        push_notion(push_title, summary)
    else:
        # AI 失败：翻译标题后推送
        print("🌐 AI 不可用，使用 Google Translate 翻译...")
        titles = [f"[{n['source']}] {n['title']}" for n in new_news[:10]]
        translated = translate_batch(titles)
        backup = "\n".join([f"• {t.strip()}" for t in translated[:10]])
        push_bark(push_title + " (摘要)", backup)
        push_notion(push_title, backup)

    print(f"\n✅ 完成！")

if __name__ == "__main__":
    main()
