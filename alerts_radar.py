"""
alerts_radar.py — 分层情报监控
- 🔴 高频层：Intel财务/Trump政策/股价异动 → 每15分钟触发，立即推送
- 🟡 中频层：Intel技术/补贴/Panera → 每小时汇总一次推送
- 🟢 低频层：学术突破/生命科学/疫情 → 每天汇总一次（由 main.py 兼顾）
"""
import os
import re
import json
import hashlib
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------- #
#  配置
# --------------------------------------------------------------------------- #
BARK_KEY    = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY_1")

# 去重文件：高频和中频各自独立，避免互相干扰
SEEN_FILE_HOT  = "seen_hot.json"    # 高频层去重
SEEN_FILE_WARM = "seen_warm.json"   # 中频层去重

# --------------------------------------------------------------------------- #
#  分层 RSS 源
# --------------------------------------------------------------------------- #

# 🔴 高频层：价格催化剂，错过即亏钱
HOT_FEEDS = [
    # Google Alerts — Intel 财务事件（earnings/SEC/buyback/IPO）
    {"name": "Intel_Finance",
     "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    # Google Alerts — Trump 政策（Tariff/EO/CHIPS Act）
    {"name": "Trump_Policy",
     "url": "https://www.google.com/alerts/feeds/02859553752789820389/5573632328866507271"},
    # Yahoo Finance RSS — INTC 实时新闻
    {"name": "INTC_Yahoo",
     "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=INTC&region=US&lang=en-US"},
    # Yahoo Finance RSS — TSM 实时新闻
    {"name": "TSM_Yahoo",
     "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSM&region=US&lang=en-US"},
    # Google News — Intel 关键词（比 Alerts 快 2-3 倍）
    {"name": "Intel_GNews",
     "url": "https://news.google.com/rss/search?q=Intel+earnings+OR+SEC+OR+buyback+OR+spinoff&hl=en-US&gl=US&ceid=US:en"},
    # Google News — 半导体关税/出口管制
    {"name": "Semicon_Trade",
     "url": "https://news.google.com/rss/search?q=semiconductor+tariff+OR+export+control+OR+CHIPS+Act&hl=en-US&gl=US&ceid=US:en"},
]

# 🟡 中频层：当天知道就够，不影响当天交易决策
WARM_FEEDS = [
    # Google Alerts — Intel 18A/Foundry 技术进展
    {"name": "Intel_Tech",
     "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    # Google Alerts — Intel 补贴/建厂
    {"name": "Intel_Subsidy",
     "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    # Google Alerts — Panera IPO
    {"name": "Panera_IPO",
     "url": "https://www.google.com/alerts/feeds/02859553752789820389/10468593379488795476"},
    # Google News — TSMC 技术/产能
    {"name": "TSMC_Tech",
     "url": "https://news.google.com/rss/search?q=TSMC+yield+OR+capacity+OR+CoWoS+OR+N2&hl=en-US&gl=US&ceid=US:en"},
    # CNBC 半导体板块
    {"name": "CNBC_Semicon",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
]

# --------------------------------------------------------------------------- #
#  去重工具
# --------------------------------------------------------------------------- #
def load_seen(filepath, ttl_hours=24):
    """加载去重记录，TTL 内的记录不重复推送"""
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            cutoff = (datetime.now(timezone(timedelta(hours=8)))
                      - timedelta(hours=ttl_hours)).isoformat()
            return {k: v for k, v in data.items() if v > cutoff}
        except:
            pass
    return {}

def save_seen(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def make_uid(entry):
    key = entry.get("link") or entry.get("title", "")
    return hashlib.md5(key.encode()).hexdigest()

def clean_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

# --------------------------------------------------------------------------- #
#  Gemini AI（多模型尝试）
# --------------------------------------------------------------------------- #
def call_gemini(prompt):
    if not GEMINI_KEY:
        return None
    models = [
        ("v1",     "gemini-2.0-flash"),
        ("v1",     "gemini-2.0-flash-lite"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
    ]
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": 0.5, "maxOutputTokens": 600}}
    for api_ver, model in models:
        url = (f"https://generativelanguage.googleapis.com/{api_ver}"
               f"/models/{model}:generateContent?key={GEMINI_KEY}")
        try:
            r = requests.post(url, json=payload, timeout=30)
            print(f"    [{api_ver}/{model}] HTTP {r.status_code}")
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif r.status_code in (404, 429):
                continue
            else:
                print(f"    ❌ {r.text[:100]}")
                return None
        except Exception as e:
            print(f"    ❌ 异常: {e}")
            return None
    print("    ⚠️ 所有模型不可用")
    return None

# --------------------------------------------------------------------------- #
#  Google Translate 保底翻译
# --------------------------------------------------------------------------- #
def translate(texts):
    combined = "\n||||\n".join(texts[:8])
    try:
        params = {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t",
                  "q": combined[:4000]}
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params=params, timeout=10)
        if r.status_code == 200:
            result = "".join(s[0] for s in r.json()[0] if s[0])
            return result.split("||||")
    except Exception as e:
        print(f"  ⚠️ 翻译失败: {e}")
    return texts

# --------------------------------------------------------------------------- #
#  Bark 推送
# --------------------------------------------------------------------------- #
def push_bark(title, body, group="情报雷达"):
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置")
        return False
    try:
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],
            "group": group,
            "sound": "minuet",
            "isArchive": 1,
        }
        r = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        data = r.json()
        if r.status_code == 200 and data.get("code") == 200:
            print(f"  ✅ Bark 推送成功 → {group}")
            return True
        print(f"  ❌ Bark 失败: {data}")
        return False
    except Exception as e:
        print(f"  ❌ Bark 异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  抓取新闻（通用）
# --------------------------------------------------------------------------- #
def fetch_new_items(feeds, seen_dict, max_per_feed=5):
    new_items = []
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    for feed_info in feeds:
        try:
            d = feedparser.parse(feed_info["url"])
            count = 0
            for entry in d.entries[:max_per_feed]:
                uid = make_uid(entry)
                if uid not in seen_dict:
                    title = clean_html(entry.get("title", ""))
                    summary = clean_html(entry.get("summary", ""))[:200]
                    new_items.append({
                        "source": feed_info["name"],
                        "title": title,
                        "summary": summary,
                        "uid": uid,
                    })
                    seen_dict[uid] = now
                    count += 1
            if count:
                print(f"   🆕 [{feed_info['name']}] {count} 条新内容")
            else:
                print(f"   ⏭️  [{feed_info['name']}] 无新内容")
        except Exception as e:
            print(f"   ⚠️ [{feed_info['name']}] 拉取失败: {e}")
    return new_items

# --------------------------------------------------------------------------- #
#  构建推送内容
# --------------------------------------------------------------------------- #
def build_push_body(items, layer_name):
    """先尝试 AI 归纳，失败则用 Google Translate 翻译标题"""
    if not items:
        return None

    # 构建给 AI 的文本
    news_text = "\n---\n".join(
        f"[{it['source']}] {it['title']}\n{it['summary']}" for it in items
    )

    if layer_name == "hot":
        prompt = f"""你是股票投资情报分析师。以下是刚抓取到的高优先级市场新闻：

{news_text}

要求：
1. 识别其中对股价有直接影响的信息（earnings/insider trading/policy/tariff等）
2. 用简体中文写出，每条一行，格式：【来源】核心信息 — 潜在影响
3. 控制在300字以内，语言精炼"""
    else:
        prompt = f"""你是股票投资情报分析师。以下是今日科技/半导体新闻：

{news_text}

要求：
1. 归纳核心要点，翻译为简体中文
2. 格式：• 【来源】一句话要点
3. 控制在400字以内"""

    print(f"  🤖 AI 归纳 {len(items)} 条...")
    result = call_gemini(prompt)
    if result:
        return result

    # 保底：Google Translate 翻译标题
    print("  🌐 降级为 Google Translate...")
    titles = [f"[{it['source']}] {it['title']}" for it in items]
    translated = translate(titles)
    return "\n".join(f"• {t.strip()}" for t in translated if t.strip())

# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def main():
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    time_str = now_cst.strftime("%H:%M")
    print(f"\n{'='*55}")
    print(f"🔍 情报扫描 {now_cst.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    print(f"{'='*55}")

    # ── 🔴 高频层 ──────────────────────────────────────────
    print("\n🔴 高频层扫描（Intel财务 / Trump政策 / 股价）")
    seen_hot = load_seen(SEEN_FILE_HOT, ttl_hours=4)   # 4小时内不重复推
    hot_items = fetch_new_items(HOT_FEEDS, seen_hot, max_per_feed=3)
    save_seen(SEEN_FILE_HOT, seen_hot)

    if hot_items:
        print(f"  ⚡ 发现 {len(hot_items)} 条高优先级新闻，立即处理...")
        body = build_push_body(hot_items, "hot")
        if body:
            push_bark(f"⚡ 市场快讯 {time_str}", body, group="🔴市场快讯")
    else:
        print("  📭 高频层无新内容")

    # ── 🟡 中频层 ──────────────────────────────────────────
    # 只在整点（分钟 < 20）运行，避免每15分钟重复汇总
    minute = now_cst.minute
    if minute < 20:
        print(f"\n🟡 中频层扫描（Intel技术 / 补贴 / TSMC）")
        seen_warm = load_seen(SEEN_FILE_WARM, ttl_hours=24)  # 24小时内不重复
        warm_items = fetch_new_items(WARM_FEEDS, seen_warm, max_per_feed=3)
        save_seen(SEEN_FILE_WARM, seen_warm)

        if warm_items:
            print(f"  📰 发现 {len(warm_items)} 条中优先级新闻，汇总推送...")
            body = build_push_body(warm_items, "warm")
            if body:
                push_bark(f"📡 科技情报 {time_str}", body, group="🟡科技情报")
        else:
            print("  📭 中频层无新内容")
    else:
        print(f"\n🟡 中频层：本轮跳过（仅整点运行，当前 :{minute:02d}）")

    print(f"\n✅ 扫描完成")

if __name__ == "__main__":
    main()
