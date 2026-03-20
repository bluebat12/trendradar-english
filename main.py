import os
import requests
import feedparser
import json
import hashlib
import time
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta

# --- 1. 環境參數與 Firebase 初始化 ---
GEMINI_KEYS = [os.getenv(f"GEMINI_API_KEY_{i}", "").strip() for i in range(1, 6) if os.getenv(f"GEMINI_API_KEY_{i}")]
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

def init_firebase():
    firebase_json = os.getenv("FIREBASE_CONFIG_JSON")
    firebase_url = os.getenv("FIREBASE_URL")
    if not firebase_json or not firebase_url:
        print("⚠️ Firebase 配置缺失，無法執行去重")
        return False
    try:
        cred_dict = json.loads(firebase_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': firebase_url})
        return True
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
        return False

# --- 2. 核心功能：去重、翻譯、評級 ---

def is_duplicate(news_hash):
    """檢查 Firebase 是否存過此哈希"""
    try:
        return db.reference(f'seen_news/{news_hash}').get() is not None
    except: return False

def mark_as_sent(news_hash, title):
    """記錄到 Firebase"""
    try:
        db.reference(f'seen_news/{news_hash}').set({
            'title': title[:100],
            'time': datetime.now(timezone(timedelta(hours=8))).isoformat()
        })
    except: pass

def translate_to_zh(text):
    """免費 Google 翻譯"""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text[:4000]}
        resp = requests.get(url, params=params, timeout=15)
        return "".join([item[0] for item in resp.json()[0] if item[0]])
    except: return text

def analyze_and_rate(text):
    """利用 Gemini 總結並評級 (1-10)"""
    if not GEMINI_KEYS: return "0|API_MISSING"
    prompt = (
        "你是一位資深行業分析師。請分析以下新聞：\n" + text +
        "\n\n要求：1. 給出 1-10 的投資價值評分。2. 三句以內的中文總結。輸出格式：分數|總結"
    )
    # 優先嘗試 Gemini 2.0/3 系列模型
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for key in GEMINI_KEYS:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={key}"
            try:
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
                if res.status_code == 200:
                    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except: continue
    return "0|AI_FAILED"

# --- 3. 推送功能 ---

def push_bark(title, body):
    if BARK_KEY:
        requests.post(f"{BARK_SERVER}/push", json={"device_key": BARK_KEY, "title": title, "body": body, "group": "AI情報"})

def push_notion(title, summary, score):
    if not NOTION_TOKEN or not DATABASE_ID: return
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary}}]},
            "Score": {"number": score},
            "Date": {"date": {"start": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")}}
        }
    }
    requests.post("https://api.notion.com/v1/pages", headers=headers, json=data)

# --- 4. 主流程 ---

def main():
    if not init_firebase(): return
    
    RSS_FEEDS = [
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
        {"name": "CNBC_Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"}
    ]

    for feed_info in RSS_FEEDS:
        print(f"📡 掃描: {feed_info['name']}")
        feed = feedparser.parse(feed_info["url"])
        
        for entry in feed.entries[:5]:
            news_hash = hashlib.md5(entry.title.encode()).hexdigest()
            
            # 1. Firebase 查重
            if is_duplicate(news_hash): continue

            # 2. AI 處理
            raw_text = f"Title: {entry.title}\nSummary: {entry.get('summary', '')}"
            ai_out = analyze_and_rate(raw_text)
            
            try:
                score_str, summary = ai_out.split('|', 1)
                score = int(''.join(filter(str.isdigit, score_str)))
            except: score, summary = 0, ai_out

            # 3. 判斷與推送
            if score >= 7: # 只有高分才發手機
                push_bark(f"🔥 重要({score}分): {feed_info['name']}", summary)
            
            push_notion(entry.title, summary, score)
            mark_as_sent(news_hash, entry.title)
            print(f"✅ 已處理: {entry.title[:20]} ({score}分)")

if __name__ == "__main__":
    main()
