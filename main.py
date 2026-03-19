import os
import feedparser
import requests
from google import genai
from google.genai import types

# --- 1. 配置区域 (从环境变量获取) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BARK_KEY = os.environ.get("BARK_KEY")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")

# RSS 源配置 (保持你之前的配置)
RSS_FEEDS = {
    "Intel_Finance": "https://www.google.com/alerts/feeds/12345/67890", # 示例，请替换
    "Intel_Tech_18A": "https://www.google.com/alerts/feeds/.../...",
    "CNBC_Tech": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
}

def get_rss_content():
    """获取所有订阅源的内容"""
    print("🚀 启动扫描...")
    all_articles = []
    for name, url in RSS_FEEDS.items():
        feed = feedparser.parse(url)
        print(f"📡 正在检查: {name} | 找到 {len(feed.entries)} 條文章")
        for entry in feed.entries:
            all_articles.append(f"Source: {name}\nTitle: {entry.title}\nLink: {entry.link}\nSummary: {entry.get('summary', '')}\n")
    return "\n---\n".join(all_articles)

def summarize_with_gemini(content):
    """使用新版 google-genai SDK 进行总结"""
    if not GEMINI_API_KEY:
        print("❌ 错误: 未检测到 GEMINI_API_KEY")
        return None

    try:
        # 初始化新版客户端
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"Please summarize the following tech news in Chinese, highlighting key trends and financial impacts:\n\n{content}"
        
        # 新版调用方式
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="你是一位专业的科技与金融分析师，善于从琐碎新闻中提取核心趋势。",
                temperature=0.7,
            )
        )
        return response.text
    except Exception as e:
        print(f"❌ Gemini 总结失败: {e}")
        return None

def push_to_bark(text):
    """通过 Bark 推送通知"""
    if not BARK_KEY:
        print("ℹ️ 未配置 BARK_KEY，跳过推送")
        return

    url = f"{BARK_SERVER}/{BARK_KEY}/TrendRadar Daily Briefing/{text}"
    try:
        requests.get(url)
        print("📲 Bark 推送成功")
    except Exception as e:
        print(f"❌ Bark 推送失败: {e}")

def main():
    # 1. 获取 RSS 内容
    raw_content = get_rss_content()
    if not raw_content:
        print("📭 没有找到新内容，退出。")
        return

    # 2. 调用新版 Gemini 总结
    summary = summarize_with_gemini(raw_content)
    
    if summary:
        print("\n📝 总结内容:\n", summary)
        # 3. 推送结果
        push_to_bark(summary)
        # 如果需要写入 Notion，可以在此调用你之前的 Notion 函数
    else:
        print("⚠️ 未能生成总结")

if __name__ == "__main__":
    main()
