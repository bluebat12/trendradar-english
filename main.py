import os
import requests
import feedparser
import json
import time
import google.generativeai as genai
from datetime import datetime

# --- 環境變數配置 ---
# 請在 GitHub Secrets 中設置以下參數
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID") # 您的資料庫 ID: 32881552a92f80eeb27af868fa43b3e8
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv('BARK_SERVER', 'https://api.day.app').rstrip('/')

# --- 英文情報源配置 ---
# 您可以隨時在此處增加 Google Alerts 的 RSS 連結
RSS_FEEDS = [
    {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
    {"name": "Reuters Tech", "url": "https://www.reutersagency.com/feed/?best-topics=technology&post_type=best"},
]

# --- 初始化 AI ---
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def ai_analyze_english_news(title, summary):
    """
    專門針對英文投資情報的 AI 分析邏輯
    """
    prompt = f"""
    你是一位專業的股票投資分析師。請分析以下英文新聞內容：
    標題：{title}
    內容：{summary}
    
    任務要求：
    1. 翻譯：將標題翻譯成專業的中文財經術語。
    2. 摘要：用不超過30個字總結核心內容。
    3. 打分：根據對股價或行業的影響力打分（1-5分），5分代表重大行業變革或突發利多/利空。
    4. 分類：歸類為[半導體, 能源, 醫藥, 宏觀, 其他]。
    
    請嚴格返回 JSON 格式：
    {{"cn_title": "...", "summary": "...", "score": 5, "category": "..."}}
    """
    try:
        response = model.generate_content(prompt)
        # 移除 Markdown 標籤，提取 JSON
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        print(f"❌ AI 分析失敗: {e}")
        return None

def write_to_notion(ai_data, original_url):
    """
    將結果寫入 Notion 的 Intelligence_Inbox 
    """
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    payload = {
        "parent": { "database_id": DATABASE_ID },
        "properties": {
            "名稱": { "title": [{"text": {"content": ai_data['cn_title']}}] },
            "Summary": { "rich_text": [{"text": {"content": ai_data['summary']}}] },
            "Score": { "number": ai_data['score'] },
            "Category": { "select": {"name": ai_data['category']} },
            "URL": { "url": original_url },
            "Source": { "select": {"name": "Global_English"} },
            "Date": { "date": {"start": datetime.now().isoformat()} }
        }
    }
    try:
        requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    except Exception as e:
        print(f"❌ Notion 寫入失敗: {e}")

def main():
    print(f"🚀 開始執行全球英文情報掃描 - {datetime.now()}")
    high_value_news = []

    for feed_info in RSS_FEEDS:
        print(f"📡 正在抓取: {feed_info['name']}")
        feed = feedparser.parse(feed_info['url'])
        
        # 每次僅處理最新的 5 條，避免觸發頻率限制
        for entry in feed.entries[:5]:
            # 優先獲取 summary，若無則用 title 代替
            content = entry.get('summary', entry.title)
            
            ai_res = ai_analyze_english_news(entry.title, content)
            
            if ai_res:
                write_to_notion(ai_res, entry.link)
                # 如果是 4 分以上的高價值情報，加入推送列表
                if ai_res['score'] >= 3:
                    high_value_news.append(f"🔥 {ai_res['score']}分: {ai_res['cn_title']}")
            
            # 🔴 關鍵：免費版 Gemini API 每分鐘限制 15 次，這裡每條休息 5 秒
            time.sleep(5)

    # 發送 Bark 推送
    if high_value_news:
        push_content = "\n".join(high_value_news)
        msg = f"🌍 全球高價值情報更新：\n{push_content}"
        push_url = f"{BARK_SERVER}/{BARK_KEY}/{requests.utils.quote(msg)}?group=Global_News"
        requests.get(push_url)
        print("✅ 已推送到手機。")
    else:
        print("📭 本次未發現 4 分以上情報。")

if __name__ == "__main__":
    main()
