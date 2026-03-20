import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta
import urllib.parse
import json
import hashlib
import time

# --- 1. 环境参数配置 ---
def _load_gemini_keys():
    keys = []
    for i in range(1, 6):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys

GEMINI_KEYS = _load_gemini_keys()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")

# 去重文件路径
SEEN_NEWS_FILE = "seen_news.json"
MAX_SEEN_NEWS = 200  # 适当增加保留记录数量

# --- 2. RSS 情报源 ---
RSS_FEEDS = [
    {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
]

# --------------------------------------------------------------------------- #
# 去重功能核心优化
# --------------------------------------------------------------------------- #
def load_seen_news():
    """加载已推送的新闻哈希"""
    if os.path.exists(SEEN_NEWS_FILE):
        try:
            with open(SEEN_NEWS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception as e:
            print(f"⚠️ 加载去重文件失败: {e}")
    return set()

def save_seen_news(seen_set):
    """保存已推送的新闻哈希"""
    # 转换为列表并保留最新的记录
    seen_list = list(seen_set)[-MAX_SEEN_NEWS:]
    with open(SEEN_NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(seen_list, f, ensure_ascii=False)

def get_news_hash(title, source):
    """生成标题+来源的哈希，避免不同来源同标题的冲突"""
    content = f"{title}_{source}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()

# --------------------------------------------------------------------------- #
# 其他原有功能函数 (保持不变)
# --------------------------------------------------------------------------- #
def print_config_check():
    print("=" * 50)
    print("🔍 配置检查")
    print(f" GEMINI Keys 数量: {len(GEMINI_KEYS)}")
    print(f" BARK_KEY: {'✅ 已配置' if BARK_KEY else '❌ 未配置'}")
    print("=" * 50)

def _call_gemini(api_key, prompt):
    model_attempts = [
        ("v1", "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1", "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "你是一位资深行业分析师...\n\n" + prompt}]}],
    }
    got_429 = False
    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                got_429 = True
        except: continue
    return "__QUOTA_EXCEEDED__" if got_429 else "__ERROR__"

def analyze_with_gemini(text):
    if not GEMINI_KEYS: return None
    prompt = f"请用中文总结以下科技动态，并分析对市场的潜在影响：\n\n{text}"
    for key in GEMINI_KEYS:
        result = _call_gemini(key, prompt)
        if result not in ["__QUOTA_EXCEEDED__", "__ERROR__"]:
            return result
    return None

def translate_to_chinese(text):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text[:5000]}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return "".join([item[0] for item in resp.json()[0] if item[0]])
    except: pass
    return text

def push_bark(title, body):
    if not BARK_KEY: return
    try:
        payload = {"device_key": BARK_KEY, "title": title, "body": body[:2000], "group": "情报雷达"}
        requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
    except: pass

def push_notion(title, summary, raw_news_list):
    if not NOTION_TOKEN or not DATABASE_ID: return
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "Date": {"date": {"start": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")}}
        }
    }
    requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data, timeout=20)

# --------------------------------------------------------------------------- #
# 主流程优化
# --------------------------------------------------------------------------- #
def main():
    print_config_check()
    seen_news_set = load_seen_news()
    
    print("\n🚀 开始扫描情报源...")
    new_collected_news = []

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:5]:
                news_hash = get_news_hash(entry.title, feed_info['name'])
                # 【去重判断】
                if news_hash not in seen_news_set:
                    new_collected_news.append({
                        'text': f"【{feed_info['name']}】{entry.title}\n摘要: {entry.get('summary', '')[:200]}",
                        'hash': news_hash
                    })
        except Exception as e:
            print(f"⚠️ 拉取 {feed_info['name']} 失败")

    # 【无新新闻则退出】
    if not new_collected_news:
        print("📭 今日无新动态，不执行推送，退出。")
        return

    print(f"✨ 发现 {len(new_collected_news)} 条新情报！")
    
    # 准备待处理文本
    full_text_for_ai = "\n\n".join([n['text'] for n in new_collected_news])
    
    # 尝试生成 AI 总结
    summary = analyze_with_gemini(full_text_for_ai)

    tz_cst = timezone(timedelta(hours=8))
    push_title = f"情报雷达 · {datetime.now(tz_cst).strftime('%Y-%m-%d %H:%M')}"

    # 【保留逻辑：配额耗尽则推原始摘要】
    if not summary:
        print("⚠️ AI 配额耗尽，发送原始新闻列表...")
        bullet_lines = ["• " + n['text'].split('\n')[0] for n in new_collected_news[:8]]
        summary = "⚠️ AI 总结失败（配额耗尽），今日新情报：\n\n" + "\n".join(bullet_lines)
    
    # 翻译并推送
    summary_cn = translate_to_chinese(summary)
    push_bark(push_title, summary_cn)
    push_notion(push_title, summary, [n['text'] for n in new_collected_news])
    
    # 【更新记忆】只有发送成功后才更新本地文件
    for n in new_collected_news:
        seen_news_set.add(n['hash'])
    save_seen_news(seen_news_set)
    print("\n✅ 处理完成，去重数据库已更新。")

if __name__ == "__main__":
    main()
