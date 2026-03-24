import os
import requests
import feedparser
import json
import hashlib
from datetime import datetime, timezone, timedelta

# --- 1. 配置加载 ---
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
GEMINI_KEY = os.getenv("GEMINI_API_KEY_1") # 默认使用第一个Key

# 这里替换为你自己的 Google Alerts RSS 链接
RSS_FEEDS = [
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/10468593379488795476"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/5573632328866507271"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/12658923786557718878"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/2601960625698782407"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/11330977868525907062"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15131987033820237330"}
    {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15077444616124068808"}
]

SEEN_NEWS_FILE = "seen_news.json"

def load_seen_news():
    if os.path.exists(SEEN_NEWS_FILE):
        try:
            with open(SEEN_NEWS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except: return set()
    return set()

def save_seen_news(seen_ids):
    with open(SEEN_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-200:], f) # 保留最近200条记录

# --- 2. AI 处理 (归纳+翻译) ---
def analyze_and_translate(text_content):
    if not GEMINI_KEY or not text_content: return None
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"""
    你是一个资深情报官。以下是从 Google Alerts 抓取的新闻内容：
    {text_content}
    
    任务：
    1. 归纳这些新闻的核心要点（如果有多条相关新闻，请合并归纳）。
    2. 将结果翻译为繁体中文。
    3. 语言风格要精炼，去除无用的广告或格式信息。
    4. 以 Markdown 列表输出。
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(url, json=payload, timeout=30)
        return res.json()['candidates'][0]['content']['parts'][0]['text']
    except: return None

# --- 3. Bark 推送 ---
def push_bark(title, content):
    if not BARK_KEY: return
    payload = {
        "title": title,
        "body": content,
        "group": "Alerts情报", # 独立分组
        "icon": "https://www.gstatic.com/images/branding/product/2x/alerts_48dp.png",
        "isArchive": 1
    }
    requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)

# --- 4. 主程序 ---
def main():
    seen_news = load_seen_news()
    new_items = []
    
    for feed in RSS_FEEDS:
        d = feedparser.parse(feed['url'])
        for entry in d.entries:
            content_id = hashlib.md5(entry.link.encode()).hexdigest()
            if content_id not in seen_news:
                summary = entry.get('summary', '')[:300]
                new_items.append(f"标题: {entry.title}\n摘要: {summary}")
                seen_news.add(content_id)

    if not new_items:
        print("📭 本次没有新新闻，跳过推送。")
        return

    print(f"🤖 正在处理 {len(new_items)} 条新情报...")
    full_text = "\n---\n".join(new_items)
    ai_result = analyze_and_translate(full_text)

    # 推送
    now = (datetime.now(timezone(timedelta(hours=8)))).strftime('%H:%M')
    push_title = f"🚀 Alerts 更新 ({now})"
    
    if ai_result:
        push_bark(push_title, ai_result)
    else:
        # 保底逻辑：如果AI失败，推标题
        backup = "\n".join([f"• {item.splitlines()[0]}" for item in new_items[:5]])
        push_bark(push_title + " (Raw)", backup)

    save_seen_news(seen_news)

if __name__ == "__main__":
    main()
