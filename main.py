import os
import requests
import feedparser
import json
import hashlib
import time
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta

# --- 1. 配置與環境變量 ---
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
        print(f"❌ Firebase 初始化失敗: {e}")
        return False

def is_duplicate(news_hash):
    try:
        return db.reference(f'seen_news/{news_hash}').get() is not None
    except: return False

def mark_as_sent(news_hash, title):
    try:
        db.reference(f'seen_news/{news_hash}').set({
            'title': title[:100],
            'time': datetime.now(timezone(timedelta(hours=8))).isoformat()
        })
    except: pass

# --- 3. AI 核心：評級與總結 ---
def analyze_and_rate(text):
    if not GEMINI_KEYS: return "0|API_MISSING"
    
    # 優化 Prompt，強制要求格式
    prompt = (
        "You are a professional securities analyst. Analyze the following news:\n" + text +
        "\n\nRequirements:\n1. Rate the investment value/market impact from 1-10.\n"
        "2. Provide a 1-sentence summary in Chinese.\n"
        "Output format: [Score]|[Summary]\n"
        "Example: 8|英特爾宣布獲得美國政府百億美元補貼，利好製程發展。"
    )
    
    # 嘗試不同的模型，增加成功率
    models = ["gemini-1.5-flash", "gemini-2.0-flash-exp"]
    for key in GEMINI_KEYS:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
                if res.status_code == 200:
                    out = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if "|" in out: return out
            except: continue
    return "0|AI_PROCESSING_FAILED"

# --- 4. 推送功能 ---
def push_bark(title, body):
    if BARK_KEY:
        try:
            requests.post(f"{BARK_SERVER}/push", json={
                "device_key": BARK_KEY, 
                "title": title, 
                "body": body, 
                "group": "情報雷達",
                "icon": "https://raw.githubusercontent.com/google/material-design-icons/master/png/action/settings_input_component/materialicons/48dp/1x/baseline_settings_input_component_black_48dp.png"
            })
        except: print("❌ Bark 推送失敗")

def push_notion(title, summary, score):
    if not NOTION_TOKEN or not DATABASE_ID: return
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}", 
        "Content-Type": "application/json", 
        "Notion-Version": "2022-06-28"
    }
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "標題": {"title": [{"text": {"content": title}}]},
            "AI摘要": {"rich_text": [{"text": {"content": summary}}]},
            "評分": {"number": score},
            "時間": {"date": {"start": datetime.now(timezone(timedelta(hours=8))).isoformat()}}
        }
    }
    requests.post("https://api.notion.com/v1/pages", headers=headers, json=data)

# --- 5. 主流程 ---
def main():
    if not init_firebase(): return
    
    RSS_FEEDS = [
        {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
        {"name": "CNBC_Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"}
    ]

    for feed_info in RSS_FEEDS:
        print(f"📡 正在掃描: {feed_info['name']}")
        feed = feedparser.parse(feed_info["url"])
        
        # 每次只處理最新的 3 條，節省 Gemini 額度
        for entry in feed.entries[:3]:
            news_hash = hashlib.md5(entry.title.encode()).hexdigest()
            
            if is_duplicate(news_hash):
                continue

            # 準備 AI 輸入
            raw_text = f"Title: {entry.title}\nContent: {entry.get('summary', '')[:500]}"
            
            # 獲取 AI 評級
            ai_out = analyze_and_rate(raw_text)
            
            try:
                score_part, summary = ai_out.split('|', 1)
                score = int(''.join(filter(str.isdigit, score_part)))
            except:
                score, summary = 0, ai_out

            # 執行推送
            # 門檻設為 7 分，你可以根據需求調整
            if score >= 0:
                push_bark(f"🔥 重磅({score}分): {entry.title[:30]}...", summary)
                print(f"🚀 已推送高分情報: {entry.title[:20]}")
            
            push_notion(entry.title, summary, score)
            mark_as_sent(news_hash, entry.title)
            
            print(f"✅ 已存入資料庫: {entry.title[:20]} ({score}分)")
            
            # 重要：強行等待 12 秒，防止 Gemini API RPM 超限 (5次/分)
            time.sleep(12)

if __name__ == "__main__":
    main()
