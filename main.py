import os
import requests
import feedparser
import json
import time
import google.generativeai as genai
from datetime import datetime

# --- 配置區 ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv('BARK_SERVER', 'https://api.day.app').rstrip('/')

# --- 更新後的 RSS 鏈接 (確保有效) ---
RSS_FEEDS = [
    {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC_Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "Reuters Tech", "url": "https://www.reutersagency.com/feed/?best-topics=technology&post_type=best"},
    {"name": "FDA_Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"}
]

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def ai_analyze(title, content):
    prompt = f"分析以下投資情報：\n標題：{title}\n內容：{content}\n要求：翻譯成中文，給出30字摘要，重要性評分(1-5)，返回JSON: {{\"cn_title\":\"...\",\"summary\":\"...\",\"score\":5,\"category\":\"...\"}}"
    try:
        response = model.generate_content(prompt)
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except: return None

def write_to_notion(ai_data, url):
    target_url = "https://api.notion.com/v1/pages"
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    payload = {
        "parent": { "database_id": DATABASE_ID },
        "properties": {
            "名稱": { "title": [{"text": {"content": ai_data['cn_title']}}] },
            "Summary": { "rich_text": [{"text": {"content": ai_data['summary']}}] },
            "Score": { "number": ai_data['score'] },
            "URL": { "url": url }
        }
    }
    r = requests.post(target_url, headers=headers, json=payload)
    print(f"Notion 響應: {r.status_code}") # 可以在日誌看 Notion 是否成功

def main():
    print(f"🚀 啟動掃描...")
    high_value_news = []
    
    for feed_info in RSS_FEEDS:
        print(f"📡 正在檢查: {feed_info['name']}")
        feed = feedparser.parse(feed_info['url'])
        print(f"   找到 {len(feed.entries)} 條文章") # 👈 關鍵日誌
        
        for entry in feed.entries[:3]:
            ai_res = ai_analyze(entry.title, entry.get('summary', ''))
            if ai_res:
                print(f"   ✅ AI 完成: {ai_res['cn_title']} (得分: {ai_res['score']})")
                write_to_notion(ai_res, entry.link)
                # 測試階段：只要有新聞就推送到 Bark
                if ai_res['score'] >= 1:
                    high_value_news.append(f"· {ai_res['cn_title']} ({ai_res['score']}分)")
            time.sleep(4) # 避開 AI 頻率限制

    if high_value_news:
        msg = "\n".join(high_value_news)
        requests.get(f"{BARK_SERVER}/{BARK_KEY}/{requests.utils.quote(msg)}?group=GlobalIntel")
        print("🔔 Bark 推送已發出")

if __name__ == "__main__":
    main()
