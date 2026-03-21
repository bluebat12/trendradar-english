import os
import requests
import feedparser
import json
import hashlib
import time
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta

# --- 1. 配置与环境变量 ---
def _load_gemini_keys():
    keys = []
    for i in range(1, 6):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k: keys.append(k)
    return keys

GEMINI_KEYS = _load_gemini_keys()
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

# --- 2. Firebase 初始化 ---
def init_firebase():
    firebase_json = os.getenv("FIREBASE_CONFIG_JSON")
    firebase_url = os.getenv("FIREBASE_URL")
    if not firebase_json or not firebase_url:
        print("⚠️ Firebase 配置缺失")
        return False
    try:
        cred_dict = json.loads(firebase_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': firebase_url})
        return True
    except Exception as e:
        print(f"❌ Firebase 初始化失败: {e}")
        return False

def is_duplicate(news_hash):
    """检查新闻是否已推送"""
    try:
        return db.reference(f'seen_news/{news_hash}').get() is not None
    except:
        return False

def mark_as_sent(news_hash, title):
    """标记新闻已推送"""
    try:
        db.reference(f'seen_news/{news_hash}').set({
            'title': title[:100],
            'time': datetime.now(timezone(timedelta(hours=8))).isoformat()
        })
    except:
        pass

def get_all_seen_hashes():
    """获取所有已推送的新闻哈希"""
    try:
        data = db.reference('seen_news').get()
        return set(data.keys()) if data else set()
    except:
        return set()

# --- 3. AI 核心：分析与总结 ---
def analyze_and_rate(text):
    if not GEMINI_KEYS:
        return "0|API_MISSING"

    prompt = (
        "You are a professional securities analyst. Analyze the following news:\n" + text +
        "\n\nRequirements:\n1. Rate the investment value/market impact from 1-10.\n"
        "2. Provide a 1-sentence summary in Chinese.\n"
        "Output format: [Score]|[Summary]\n"
        "Example: 8|英特尔宣布获得美国政府百亿美元补贴，利好制程发展。"
    )

    # 模型优先级（按配额从高到低）
    models = [
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-1.5-flash"
    ]
    
    for key in GEMINI_KEYS:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                res = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 100}
                }, timeout=30)
                if res.status_code == 200:
                    out = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if "|" in out:
                        return out
            except:
                continue
    return "0|AI_PROCESSING_FAILED"

# --- 4. 推送功能 ---
def push_bark(title, body):
    if BARK_KEY:
        try:
            requests.post(f"{BARK_SERVER}/push", json={
                "device_key": BARK_KEY,
                "title": title,
                "body": body,
                "group": "情报雷达",
            })
        except Exception as e:
            print(f"❌ Bark 推送失败: {e}")

def push_notion(title, summary, score):
    if not NOTION_TOKEN or not DATABASE_ID:
        return
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "标题": {"title": [{"text": {"content": title[:100]}}]},
            "AI摘要": {"rich_text": [{"text": {"content": summary[:500]}}]},
            "评分": {"number": score},
            "时间": {"date": {"start": datetime.now(timezone(timedelta(hours=8))).isoformat()}}
        }
    }
    try:
        requests.post("https://api.notion.com/v1/pages", headers=headers, json=data)
    except:
        pass

# --- 5. 主流程 ---
def main():
    tz_cst = timezone(timedelta(hours=8))
    now = datetime.now(tz_cst)
    print(f"\n{'='*50}")
    print(f"🚀 TrendRadar 启动 ({now.strftime('%Y-%m-%d %H:%M')})")
    print(f"{'='*50}")

    if not init_firebase():
        print("❌ Firebase 初始化失败，程序退出")
        return

    seen_hashes = get_all_seen_hashes()
    print(f"📊 Firebase 中已有 {len(seen_hashes)} 条历史推送记录\n")

    RSS_FEEDS = [
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/10468593379488795476"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/5573632328866507271"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/12658923786557718878"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/2601960625698782407"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/11330977868525907062"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15131987033820237330"},
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15077444616124068808"},
        {"name": "CNBC_Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"}
    ]

    new_articles = []
    total_checked = 0

    print("📡 正在扫描 RSS 源...")
    for feed_info in RSS_FEEDS:
        print(f"   扫描: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:3]:
                total_checked += 1
                news_hash = hashlib.md5(entry.title.encode()).hexdigest()

                if is_duplicate(news_hash):
                    print(f"   ⏭️  已推送: {entry.title[:30]}...")
                    continue

                new_articles.append({
                    'hash': news_hash,
                    'title': entry.title,
                    'summary': entry.get('summary', '')[:500],
                    'source': feed_info['name']
                })
                print(f"   🆕 新发现: {entry.title[:30]}...")

        except Exception as e:
            print(f"   ⚠️ 扫描失败: {e}")

    print(f"\n{'='*50}")
    print(f"📊 扫描统计:")
    print(f"   • 检查新闻: {total_checked} 条")
    print(f"   • 发现新新闻: {len(new_articles)} 条")
    print(f"{'='*50}\n")

    # 关键：无新新闻则不推送
    if not new_articles:
        print("📭 没有发现新新闻，结束运行（不推送通知）")
        return

    print(f"🆕 发现 {len(new_articles)} 条新新闻，开始处理...\n")

    for i, article in enumerate(new_articles):
        print(f"[{i+1}/{len(new_articles)}] 处理: {article['title'][:40]}...")

        raw_text = f"Title: {article['title']}\nContent: {article['summary']}"
        ai_out = analyze_and_rate(raw_text)

        try:
            score_part, summary = ai_out.split('|', 1)
            score = int(''.join(filter(str.isdigit, score_part)))
        except:
            score, summary = 0, ai_out

        MIN_SCORE = 5
        if score >= MIN_SCORE:
            push_bark(
                f"🔥 {article['source']} ({score}分)",
                f"{article['title']}\n\n{summary}"
            )
            print(f"   📱 已推送 Bark (评分: {score})")
        else:
            print(f"   ⏭️ 跳过推送 (评分: {score} < {MIN_SCORE})")

        push_notion(article['title'], summary, score)
        mark_as_sent(article['hash'], article['title'])
        print(f"   💾 已存入 Firebase")

        time.sleep(12)

    print(f"\n{'='*50}")
    print(f"✅ 处理完成！共处理 {len(new_articles)} 条新闻")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
