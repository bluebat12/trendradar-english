import os
import requests
import feedparser
from google import genai
from google.genai import types

# --- 1. 环境参数配置 ---
# 确保你在 GitHub Secrets 中设置了这些变量
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip('/')

# --- 2. 英文情报源配置 ---
# 修复了格式问题，使用简单的字典结构
RSS_FEEDS = [
    {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
]

def analyze_with_gemini(text):
    """使用新版 google-genai SDK 分析新闻"""
    if not GEMINI_KEY:
        print("❌ 错误: 找不到 GEMINI_API_KEY")
        return None
    
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"请用中文总结以下科技动态，并分析对市场的潜在影响：\n\n{text}",
            config=types.GenerateContentConfig(
                system_instruction="你是一位资深的行业分析师，擅长从 RSS 摘要中提取关键投资信息。",
                temperature=0.7
            )
        )
        return response.text
    except Exception as e:
        print(f"❌ AI 分析失败: {e}")
        return None

def main():
    print("🚀 开始扫描情报源...")
    collected_news = []
    
    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        feed = feedparser.parse(feed_info['url'])
        
        # 只取每个源最新的 3 条，避免内容过长
        for entry in feed.entries[:3]:
            news_item = f"【{feed_info['name']}】{entry.title}\n摘要: {entry.get('summary', '')[:200]}"
            collected_news.append(news_item)
    
    if not collected_news:
        print("📭 今日无新动态")
        return

    full_text = "\n\n".join(collected_news)
    
    # AI 总结
    summary = analyze_with_gemini(full_text)
    
    if summary:
        print("📝 总结完成:\n", summary)
        # 推送 Bark
        if BARK_KEY:
            push_url = f"{BARK_SERVER}/{BARK_KEY}/情报雷达总结/{summary}"
            requests.get(push_url)
            print("📲 已推送到手机")
    else:
        print("⚠️ 未能生成 AI 总结")

if __name__ == "__main__":
    main()
