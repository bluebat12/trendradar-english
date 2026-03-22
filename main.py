import os
import requests
import feedparser
import json
import hashlib
from datetime import datetime, timezone, timedelta
from datetime import datetime as dt

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

GEMINI_KEYS  = _load_gemini_keys()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")
BARK_KEY     = os.getenv("BARK_KEY")
BARK_SERVER  = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")

# --- 2. 已推送新闻记录文件 ---
SENT_NEWS_FILE = "sent_news_hashes.json"

def load_sent_hashes():
    """加载已推送的新闻哈希记录"""
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 清理超过7天的记录
                cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).isoformat()
                data = {k: v for k, v in data.items() if v > cutoff}
                return set(data.keys()), data
        except:
            pass
    return set(), {}

def save_sent_hashes(hashes_dict):
    """保存已推送的新闻哈希记录"""
    with open(SENT_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(hashes_dict, f, ensure_ascii=False, indent=2)

def get_news_hash(title, source):
    """生成新闻的唯一哈希"""
    content = f"{source}:{title}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()

# --- 3. RSS 情报源 ---
RSS_FEEDS = [
    {"name": "Intel_Finance",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy",  "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech",      "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press",      "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
]

# --------------------------------------------------------------------------- #
#  诊断启动
# --------------------------------------------------------------------------- #
def print_config_check():
    print("=" * 50)
    print("🔍 配置检查")
    print(f"  GEMINI Keys 数量: {len(GEMINI_KEYS)}")
    for i, k in enumerate(GEMINI_KEYS, 1):
        print(f"    Key-{i}: ...{k[-6:]} (长度={len(k)})")
    print(f"  NOTION_TOKEN: {'✅ 已配置 (长度=' + str(len(NOTION_TOKEN)) + ')' if NOTION_TOKEN else '❌ 未配置'}")
    print(f"  DATABASE_ID:  {'✅ 已配置 (长度=' + str(len(DATABASE_ID)) + ')' if DATABASE_ID else '❌ 未配置'}")
    print(f"  BARK_KEY:     {'✅ 已配置 (长度=' + str(len(BARK_KEY)) + ')' if BARK_KEY else '❌ 未配置'}")
    print(f"  BARK_SERVER:  {BARK_SERVER}")
    print("=" * 50)

# --------------------------------------------------------------------------- #
#  Gemini API 调用（带速率限制处理）
# --------------------------------------------------------------------------- #
def _call_gemini(api_key, prompt, max_tokens=500):
    """
    用单个 Key 尝试所有模型。
    - 成功: 返回文本
    - 该 Key 所有模型均 429: 返回 '__QUOTA_EXCEEDED__' → 调用方切换下一个 Key
    - 遇到真正错误(非429/404): 返回 '__ERROR__' → 调用方终止
    """
    model_attempts = [
        ("v1",     "gemini-2.5-flash", 15000),
        ("v1",     "gemini-2.0-flash-lite", 8000),
        ("v1",     "gemini-2.0-flash", 8000),
        ("v1beta", "gemini-2.0-flash", 8000),
        ("v1beta", "gemini-1.5-flash", 8000),
        ("v1",     "gemini-1.5-flash", 8000),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,  # 限制输出 token 数
        },
    }

    got_429 = False

    for api_ver, model, _ in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=30)
            code = resp.status_code
            print(f"    [{api_ver}/{model}] HTTP {code}")

            if code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            elif code == 429:
                got_429 = True
                continue

            elif code == 404:
                continue

            else:
                print(f"    ❌ 错误: {resp.text[:200]}")
                return "__ERROR__"

        except Exception as e:
            print(f"    ❌ 请求异常: {e}")
            return "__ERROR__"

    if got_429:
        return "__QUOTA_EXCEEDED__"
    else:
        return "__QUOTA_EXCEEDED__"


def analyze_with_gemini(text, max_tokens=500):
    """调用 Gemini API 生成总结（带备用方案）"""
    if not GEMINI_KEYS:
        print("❌ 未找到任何 Gemini Key")
        return None

    print(f"🔑 共加载 {len(GEMINI_KEYS)} 个 Gemini Key")
    prompt = (
        "请用中文简洁总结以下科技动态（控制在200字以内）：\n\n"
        f"{text}"
    )

    for idx, key in enumerate(GEMINI_KEYS, start=1):
        key_label = f"Key-{idx}(...{key[-6:]})"
        print(f"  🔄 尝试 {key_label}")
        result = _call_gemini(key, prompt, max_tokens)

        if result == "__QUOTA_EXCEEDED__":
            print(f"  ⚠️  {key_label} 配额耗尽，切换下一个...")
            continue
        elif result == "__ERROR__":
            print(f"  ❌ {key_label} 遇到错误，终止")
            return None
        elif result is not None:
            print(f"  ✅ {key_label} 调用成功")
            return result

    print("❌ 所有 Gemini Key 均不可用")
    return None

# --------------------------------------------------------------------------- #
#  免费翻译功能（使用 Google Translate）
# --------------------------------------------------------------------------- #
def translate_to_chinese(text):
    """使用 Google Translate 免费接口将英文翻译为中文"""
    if not text:
        return text

    print("🌐 正在翻译为中文...")
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "en",
            "tl": "zh-CN",
            "dt": "t",
            "q": text[:5000]
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            translated = "".join([item[0] for item in data[0] if item[0]])
            print(f"  ✅ 翻译成功")
            return translated
        else:
            print(f"  ⚠️ 翻译失败 HTTP {resp.status_code}")
            return text
    except Exception as e:
        print(f"  ⚠️ 翻译异常: {e}")
        return text

# --------------------------------------------------------------------------- #
#  Bark
# --------------------------------------------------------------------------- #
def push_bark(title, body, url_link=""):
    """发送 Bark 推送"""
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置，跳过")
        return False
    print(f"\n📲 开始 Bark 推送...")
    try:
        # 清理 body 中的 HTML 标签，控制长度
        import re
        body_clean = re.sub(r'<[^>]+>', '', body)  # 移除 HTML 标签
        body_clean = re.sub(r'\s+', ' ', body_clean).strip()  # 合并空白字符
        body_clean = body_clean[:500]  # 限制总长度

        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body_clean,
            "sound": "minuet",
            "group": "情报雷达",
        }
        if url_link:
            payload["url"] = url_link

        resp = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        resp_data = resp.json()
        print(f"  HTTP {resp.status_code}: {resp_data}")

        if resp.status_code == 200 and resp_data.get("code") == 200:
            print("  ✅ Bark 推送成功")
            return True
        else:
            print(f"  ❌ Bark 推送失败: {resp_data.get('message', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"  ❌ Bark 推送异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  Notion
# --------------------------------------------------------------------------- #
def push_notion(title, summary, raw_news):
    """写入 Notion 数据库"""
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  NOTION_TOKEN 或 DATABASE_ID 未配置，跳过")
        return False
    print(f"\n📓 开始 Notion 写入...")
    print(f"  DATABASE_ID: {DATABASE_ID[:8]}...")
    tz_cst = timezone(timedelta(hours=8))
    today_str = datetime.now(tz_cst).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    sources = list({line.split("】")[0].lstrip("【") for line in raw_news if "】" in line})
    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":    {"title":     [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "Date":    {"date":      {"start": today_str}},
            "Sources": {"rich_text": [{"text": {"content": ", ".join(sources)}}]},
        },
    }
    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json=page_data,
            timeout=20,
        )
        print(f"  HTTP {resp.status_code}")
        resp_json = resp.json()

        if resp.status_code == 200:
            print("  ✅ Notion 写入成功")
            return True
        elif resp.status_code == 400:
            print(f"  ❌ 请求格式错误: {resp_json.get('message', resp.text[:200])}")
        elif resp.status_code == 401:
            print("  ❌ Token 无效")
        elif resp.status_code == 403:
            print("  ❌ 无权限访问数据库，请确认 Integration 已添加到数据库")
        elif resp.status_code == 404:
            print("  ❌ 数据库不存在")
        else:
            print(f"  ❌ 错误: {resp_json.get('message', resp.text[:200])}")
        return False
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  关键词提取
# --------------------------------------------------------------------------- #
def extract_keywords(title, summary, max_keywords=3):
    """从标题和摘要中提取关键词"""
    # 提取英文关键词
    import re
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', title + ' ' + summary[:500])
    # 常见噪声词
    stopwords = {'The', 'This', 'That', 'These', 'Those', 'For', 'And', 'But', 'With', 'From', 'About', 'How', 'What', 'When', 'Where', 'Why', 'Who', 'Which', 'Google', 'Apple', 'Amazon'}
    keywords = [w for w in words if w not in stopwords and len(w) > 2][:max_keywords]
    return keywords

# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def main():
    print_config_check()

    # 加载已推送的新闻哈希
    sent_hashes, sent_hashes_dict = load_sent_hashes()
    print(f"\n📋 已加载 {len(sent_hashes)} 条历史推送记录")

    tz_cst = timezone(timedelta(hours=8))
    now = datetime.now(tz_cst)
    print(f"\n🚀 开始扫描情报源... ({now.strftime('%H:%M:%S')})")

    new_news = []
    new_hashes = set()

    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            # 只取最新1条，避免重复
            for entry in feed.entries[:1]:
                news_hash = get_news_hash(entry.title, feed_info['name'])
                if news_hash not in sent_hashes:
                    new_hashes.add(news_hash)
                    new_news.append({
                        "source": feed_info['name'],
                        "title": entry.title,
                        "summary": entry.get('summary', '')[:300],
                        "link": entry.get('link', ''),
                        "published": entry.get('published', ''),
                        "hash": news_hash,
                    })
                    print(f"   🆕 新: {entry.title[:50]}...")
                else:
                    print(f"   ⏭️  已推送，跳过")
        except Exception as e:
            print(f"⚠️  拉取 {feed_info['name']} 失败: {e}")

    print(f"\n📊 统计: 新新闻 {len(new_news)} 条")

    if not new_news:
        print("📭 今日无新动态，退出")
        return

    # 更新已推送记录
    current_time = now.isoformat()
    for h in new_hashes:
        sent_hashes_dict[h] = current_time
    save_sent_hashes(sent_hashes_dict)

    # 为每条新闻单独推送
    for news in new_news:
        print(f"\n{'='*50}")
        print(f"📰 处理: {news['title'][:60]}...")

        # 提取关键词
        keywords = extract_keywords(news['title'], news['summary'])
        keyword_str = ' · '.join(keywords) if keywords else news['source']

        # 生成总结（更简洁的提示词）
        prompt = f"标题: {news['title']}\n摘要: {news['summary'][:800]}"
        summary = analyze_with_gemini(prompt, max_tokens=150)  # 减少 token 限制

        push_title = f"📡 {news['source']}"

        if summary:
            summary_cn = translate_to_chinese(summary)
            # 简洁格式：关键词 + 一句话总结
            push_body = f"【{keyword_str}】\n{summary_cn}"
        else:
            # 无 AI 总结时，翻译标题作为摘要
            title_cn = translate_to_chinese(news['title'])
            push_body = f"【{keyword_str}】\n{title_cn}"

        push_bark(push_title, push_body, news['link'])

        # 同时写入 Notion
        notion_title = f"情报雷达 · {news['source']}"
        notion_body = summary if summary else news['title']
        push_notion(notion_title, notion_body, [f"【{news['source']}】{news['title']}"])

    print("\n" + "="*50)
    print(f"✅ 完成！共处理 {len(new_news)} 条新闻")

if __name__ == "__main__":
    main()
