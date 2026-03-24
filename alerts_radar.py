import os
import requests
import feedparser
import json
import hashlib
from datetime import datetime, timezone, timedelta

# --- 1. 配置加载 ---
BARK_KEY    = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY_1")

# --- RSS 订阅源（Google Alerts）---
RSS_FEEDS = [
    {"name": "Intel_Finance",   "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy",   "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "Alert_4",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/10468593379488795476"},
    {"name": "Alert_5",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/5573632328866507271"},
    {"name": "Alert_6",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/12658923786557718878"},
    {"name": "Alert_7",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/2601960625698782407"},
    {"name": "Alert_8",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/11330977868525907062"},
    {"name": "Alert_9",         "url": "https://www.google.com/alerts/feeds/02859553752789820389/15131987033820237330"},
    {"name": "Alert_10",        "url": "https://www.google.com/alerts/feeds/02859553752789820389/15077444616124068808"},
]

# 去重记录文件
SENT_FILE = "seen_news.json"

# --------------------------------------------------------------------------- #
#  去重
# --------------------------------------------------------------------------- #
def load_seen():
    if os.path.exists(SENT_FILE):
        try:
            with open(SENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只保留最近 7 天
            cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).isoformat()
            return {k: v for k, v in data.items() if v > cutoff}
        except:
            pass
    return {}

def save_seen(seen):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)

# --------------------------------------------------------------------------- #
#  Gemini AI 归纳翻译（尝试多个模型）
# --------------------------------------------------------------------------- #
def analyze_and_translate(text_content):
    if not GEMINI_KEY or not text_content:
        return None

    prompt = f"""你是资深情报分析师。以下是从 Google Alerts 抓取的新闻：

{text_content}

任务：
1. 归纳核心要点（多条相关新闻请合并）
2. 翻译为繁体中文
3. 精炼语言，用 Markdown 列表格式输出，控制在300字以内
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    model_attempts = [
        ("v1",     "gemini-2.0-flash"),
        ("v1",     "gemini-2.0-flash-lite"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]

    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            res = requests.post(url, json=payload, timeout=30)
            print(f"    [{api_ver}/{model}] HTTP {res.status_code}")
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif res.status_code in (404, 429):
                continue
            else:
                print(f"    ❌ 错误: {res.text[:150]}")
                return None
        except Exception as e:
            print(f"    ❌ 异常: {e}")
            return None

    print("    ⚠️ 所有模型均不可用")
    return None

# --------------------------------------------------------------------------- #
#  Bark 推送
# --------------------------------------------------------------------------- #
def push_bark(title, content):
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置，跳过")
        return
    payload = {
        "device_key": BARK_KEY,
        "title": title,
        "body": content[:2000],
        "group": "Alerts情报",
        "sound": "minuet",
        "isArchive": 1,
    }
    try:
        resp = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("code") == 200:
            print("  ✅ Bark 推送成功")
        else:
            print(f"  ❌ Bark 推送失败: {data}")
    except Exception as e:
        print(f"  ❌ Bark 异常: {e}")

# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def main():
    seen = load_seen()
    new_items = []
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    current_time = now_cst.isoformat()

    print(f"📡 开始扫描 Google Alerts... ({now_cst.strftime('%H:%M')})")
    print(f"   已有记录: {len(seen)} 条")

    for feed in RSS_FEEDS:
        try:
            d = feedparser.parse(feed["url"])
            for entry in d.entries:
                uid = hashlib.md5(entry.get("link", entry.title).encode()).hexdigest()
                if uid not in seen:
                    summary = entry.get("summary", "")[:300]
                    new_items.append(f"标题: {entry.title}\n摘要: {summary}")
                    seen[uid] = current_time
                    print(f"   🆕 [{feed['name']}] {entry.title[:60]}")
        except Exception as e:
            print(f"   ⚠️ 拉取 {feed['name']} 失败: {e}")

    if not new_items:
        print("📭 没有新内容，跳过推送。")
        save_seen(seen)
        return

    print(f"\n🤖 发现 {len(new_items)} 条新情报，正在 AI 归纳...")
    full_text = "\n---\n".join(new_items)
    ai_result = analyze_and_translate(full_text)

    push_title = f"🚀 Alerts 更新 ({now_cst.strftime('%H:%M')})"

    if ai_result:
        push_bark(push_title, ai_result)
        print("✅ 已推送 AI 归纳内容")
    else:
        # 保底：推送原始标题
        backup = "\n".join([f"• {item.splitlines()[0].replace('标题: ', '')}" for item in new_items[:5]])
        push_bark(push_title + " (原始)", backup)
        print("⚠️  AI 失败，已推送原始标题")

    save_seen(seen)

if __name__ == "__main__":
    main()
