import os
import requests
import feedparser
import json
import hashlib
from datetime import datetime, timezone, timedelta

# --- 1. 配置加载 ---
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
GEMINI_KEY = os.getenv("GEMINI_API_KEY_1")

# RSS 订阅源 - 确保每一项大括号 {} 后面都有逗号
RSS_FEEDS = [
    {
        "name": "Google Alerts", 
        "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"
        "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/10468593379488795476"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/5573632328866507271"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/12658923786557718878"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/2601960625698782407"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/11330977868525907062"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15131987033820237330"}
        {"name": "Google Alerts", "url": "https://www.google.com/alerts/feeds/02859553752789820389/15077444616124068808"}
    },
]

# 统一使用你仓库现有的去重文件名
SENT_NEWS_FILE = "sent_news_hashes.json"

def load_sent_hashes():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_sent_hashes(hashes_dict):
    with open(SENT_NEWS_FILE, "w", encoding="utf-8") as f:
        # 仅保留最近 7 天记录，防止文件过大
        cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).isoformat()
        filtered_data = {k: v for k, v in hashes_dict.items() if v > cutoff}
        json.dump(filtered_data, f, ensure_ascii=False, indent=2)

# --- 2. AI 处理 (归纳+翻译) ---
def analyze_and_translate(text_content):
    if not GEMINI_KEY or not text_content: return None
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"""
    你是一个资深情报分析师。以下是从 Google Alerts 抓取的新闻原始数据：
    {text_content}
    
    任务：
    1. 归纳这些新闻的核心要点（如果有多条相关新闻，请合并归纳）。
    2. 必须翻译为繁体中文。
    3. 语言精炼，以 Markdown 列表格式输出。
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(url, json=payload, timeout=30)
        res_json = res.json()
        return res_json['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"❌ AI 处理失败: {e}")
        return None

# --- 3. Bark 推送 ---
def push_bark(title, content):
    if not BARK_KEY: return
    payload = {
        "title": title,
        "body": content,
        "group": "Alerts情报",
        "icon": "https://www.gstatic.com/images/branding/product/2x/alerts_48dp.png",
        "isArchive": 1
    }
    try:
        requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
    except: pass

# --- 4. 主程序 ---
def main():
    sent_hashes = load_sent_hashes()
    new_items = []
    
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    current_time_str = now_cst.isoformat()

    print(f"📡 开始扫描 Google Alerts...")
    
    for feed in RSS_FEEDS:
        d = feedparser.parse(feed['url'])
        for entry in d.entries:
            # 使用链接的 MD5 作为唯一 ID
            content_id = hashlib.md5(entry.link.encode()).hexdigest()
            if content_id not in sent_hashes:
                summary = entry.get('summary', '')[:300]
                new_items.append(f"标题: {entry.title}\n摘要: {summary}")
                sent_hashes[content_id] = current_time_str

    if not new_items:
        print("📭 没有发现新内容，跳过推送。")
        return

    print(f"🤖 正在处理 {len(new_items)} 条新情报...")
    full_text = "\n---\n".join(new_items)
    ai_result = analyze_and_translate(full_text)

    push_title = f"🚀 Alerts 更新 ({now_cst.strftime('%H:%M')})"
    
    if ai_result:
        push_bark(push_title, ai_result)
        print("✅ 已推送归纳后的内容。")
    else:
        # 保底推送
        backup = "\n".join([f"• {item.splitlines()[0]}" for item in new_items[:5]])
        push_bark(push_title + " (Raw)", backup)
        print("⚠️ AI 失败，已推送原始标题。")

    save_sent_hashes(sent_hashes)

if __name__ == "__main__":
    main()
