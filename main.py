import os
import requests
import feedparser
import hashlib
import time
from datetime import datetime, timezone, timedelta

# --- 1. 环境参数配置 ---
def _load_gemini_keys():
    keys = []
    for i in range(1, 6):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k: keys.append(k)
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single and single not in keys: keys.append(single)
    return keys

GEMINI_KEYS  = _load_gemini_keys()
BARK_KEY     = os.getenv("BARK_KEY")
BARK_SERVER  = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")

# 记忆库文件路径（与 .yml 中的文件名对应）
DB_FILE = "sent_news.txt"

# --- 2. RSS 情报源 ---
RSS_FEEDS = [
    {"name": "Intel_Finance",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech",      "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
]

# --- 3. 记忆库核心功能 ---
def load_sent_hashes():
    """读取已推送过的新闻指纹"""
    if not os.path.exists(DB_FILE):
        return set()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_new_hashes(hashes):
    """保存新推送的新闻指纹"""
    with open(DB_FILE, "a", encoding="utf-8") as f:
        for h in hashes:
            f.write(h + "\n")

# --- 4. Gemini 调用逻辑 (保持多 Key 轮询) ---
def _call_gemini_api(api_key, prompt):
    # 优先尝试 2.0 Flash (1500次/天版本)
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7}
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                continue # 换模型或换 Key
        except:
            continue
    return None

def analyze_all(news_list):
    full_text = "\n\n".join(news_list)
    prompt = f"你是一位资深分析师。请用中文总结以下新情报，分析对Intel或科技市场的潜在影响，并列出要点：\n\n{full_text}"
    
    for key in GEMINI_KEYS:
        result = _call_gemini_api(key, prompt)
        if result: return result
    return None

# --- 5. 推送功能 (保持你现有的 Bark/Notion) ---
def push_results(summary, raw_items):
    tz_cst = timezone(timedelta(hours=8))
    today = datetime.now(tz_cst).strftime("%Y-%m-%d %H:%M")
    title = f"情报雷达·实时更新({len(raw_items)})"
    
    # Bark 推送
    if BARK_KEY:
        requests.post(f"{BARK_SERVER}/push", json={
            "device_key": BARK_KEY,
            "title": title,
            "body": summary,
            "group": "XJ_Intelligence"
        })
    
    # Notion 写入 (此处可复用你之前的 push_notion 函数)
    print(f"✅ 推送完成: {title}")

# --- 主流程 ---
def main():
    sent_hashes = load_sent_hashes()
    new_articles = []
    current_hashes = []

    print(f"🔎 开始增量扫描... (已记录 {len(sent_hashes)} 条历史)")

    for feed_info in RSS_FEEDS:
        print(f"📡 抓取: {feed_info['name']}")
        feed = feedparser.parse(feed_info["url"])
        
        for entry in feed.entries[:10]: # 检查最新的10条
            # 使用标题+链接生成唯一 ID
            fingerprint = hashlib.md5((entry.title + entry.link).encode()).hexdigest()
            
            if fingerprint not in sent_hashes:
                content = f"【{feed_info['name']}】{entry.title}\n摘要: {entry.get('summary', '')[:150]}"
                new_articles.append(content)
                current_hashes.append(fingerprint)

    if not new_articles:
        print("📭 没有发现新消息，结束运行。")
        return

    print(f"🆕 发现 {len(new_articles)} 条新情报，正在调取 AI...")
    
    # 为了节省额度，我们将新消息合并成一个请求
    summary = analyze_all(new_articles)
    
    if summary:
        push_results(summary, new_articles)
        save_new_hashes(current_hashes)
        print(f"💾 记忆库已更新，新增 {len(current_hashes)} 条记录")
    else:
        print("❌ AI 总结失败，本次记录未保存，下次将重试。")

if __name__ == "__main__":
    main()
