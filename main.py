import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta
import urllib.parse
import json
import hashlib

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
MAX_SEEN_NEWS = 100  # 保留最近100条记录

# --- 2. RSS 情报源 ---
RSS_FEEDS = [
    {"name": "Intel_Finance", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446256904"},
    {"name": "Intel_Tech_18A", "url": "https://www.google.com/alerts/feeds/02859553752789820389/7842163283446258095"},
    {"name": "Intel_Subsidy", "url": "https://www.google.com/alerts/feeds/02859553752789820389/3911216818205463334"},
    {"name": "CNBC Tech", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"},
    {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"},
]

# --------------------------------------------------------------------------- #
# 去重功能
# --------------------------------------------------------------------------- #
def load_seen_news():
    """加载已推送的新闻标题"""
    if os.path.exists(SEEN_NEWS_FILE):
        try:
            with open(SEEN_NEWS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            pass
    return set()

def save_seen_news(seen_set):
    """保存已推送的新闻标题"""
    # 只保留最近的MAX_SEEN_NEWS条
    seen_list = list(seen_set)[-MAX_SEEN_NEWS:]
    with open(SEEN_NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(seen_list, f, ensure_ascii=False)

def get_news_hash(title):
    """生成新闻标题的哈希值用于去重"""
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def filter_new_news(news_list, seen_set):
    """过滤出未推送的新闻"""
    new_news = []
    for news in news_list:
        news_hash = get_news_hash(news['title'])
        if news_hash not in seen_set:
            new_news.append(news)
            seen_set.add(news_hash)
    return new_news

# --------------------------------------------------------------------------- #
# 诊断启动
# --------------------------------------------------------------------------- #
def print_config_check():
    print("=" * 50)
    print("🔍 配置检查")
    print(f" GEMINI Keys 数量: {len(GEMINI_KEYS)}")
    for i, k in enumerate(GEMINI_KEYS, 1):
        print(f" Key-{i}: ...{k[-6:]} (长度={len(k)})")
    print(f" NOTION_TOKEN: {'✅ 已配置 (长度=' + str(len(NOTION_TOKEN)) + ')' if NOTION_TOKEN else '❌ 未配置'}")
    print(f" DATABASE_ID: {'✅ 已配置 (长度=' + str(len(DATABASE_ID)) + ')' if DATABASE_ID else '❌ 未配置'}")
    print(f" BARK_KEY: {'✅ 已配置 (长度=' + str(len(BARK_KEY)) + ')' if BARK_KEY else '❌ 未配置'}")
    print(f" BARK_SERVER: {BARK_SERVER}")
    print("=" * 50)

# --------------------------------------------------------------------------- #
# Gemini — 修复版：429和404都继续尝试，只要有一个模型成功即可
# --------------------------------------------------------------------------- #
def _call_gemini(api_key, prompt):
    """
    用单个 Key 尝试所有模型。
    - 成功: 返回文本
    - 该 Key 所有模型均 429: 返回 '__QUOTA_EXCEEDED__' → 调用方切换下一个 Key
    - 遇到真正错误(非429/404): 返回 '__ERROR__' → 调用方终止
    """
    model_attempts = [
        ("v1", "gemini-2.5-flash"),
        ("v1", "gemini-2.0-flash-lite"),
        ("v1", "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1", "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "你是一位资深行业分析师，擅长从 RSS 摘要中提取关键投资信息。\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.7},
    }

    got_429 = False  # 是否遇到过 429
    got_success = False

    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=30)
            code = resp.status_code
            print(f" [{api_ver}/{model}] HTTP {code}")

            if code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            elif code == 429:
                # 配额耗尽，记录并继续尝试其他模型
                got_429 = True
                continue

            elif code == 404:
                # 该模型不支持，继续尝试其他模型（不影响切Key判断）
                continue

            else:
                # 真正的错误（401无效Key、500服务器错误等）
                print(f" ❌ 完整错误: {resp.text[:200]}")
                return "__ERROR__"

        except Exception as e:
            print(f" ❌ 请求异常: {e}")
            return "__ERROR__"

    # 所有模型都试完了，没有成功
    # 只要遇到过429就认为是配额问题，切换下一个Key
    if got_429:
        return "__QUOTA_EXCEEDED__"
    else:
        # 全部都是404，说明这个Key可能有问题，也尝试切换
        return "__QUOTA_EXCEEDED__"


def analyze_with_gemini(text):
    if not GEMINI_KEYS:
        print("❌ 未找到任何 Gemini Key")
        return None

    print(f"🔑 共加载 {len(GEMINI_KEYS)} 个 Gemini Key")
    prompt = (
        "请用中文总结以下科技动态，并分析对市场的潜在影响。"
        "格式：先给出3句话的整体概述，再逐条列出每条要点（不超过5条）。\n\n"
        f"{text}"
    )

    for idx, key in enumerate(GEMINI_KEYS, start=1):
        key_label = f"Key-{idx}(...{key[-6:]})"
        print(f" 🔄 尝试 {key_label}")
        result = _call_gemini(key, prompt)

        if result == "__QUOTA_EXCEEDED__":
            print(f" ⚠️ {key_label} 配额耗尽或不可用，切换下一个...")
            continue
        elif result == "__ERROR__":
            print(f" ❌ {key_label} 遇到严重错误，终止")
            return None
        elif result is not None:
            print(f" ✅ {key_label} 调用成功，摘要长度={len(result)}字符")
            return result

    print("❌ 所有 Gemini Key 均不可用，今日配额可能已耗尽")
    return None

# --------------------------------------------------------------------------- #
# 免费翻译功能（使用 Google Translate，无需 API Key）
# --------------------------------------------------------------------------- #
def translate_to_chinese(text):
    """
    使用 Google Translate 免费接口将英文翻译为中文
    返回翻译后的文本，失败时返回原文
    """
    if not text:
        return text

    print("🌐 正在翻译为中文...")
    try:
        # Google Translate API
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "en",
            "tl": "zh-CN",
            "dt": "t",
            "q": text[:5000]  # 限制长度
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # 解析翻译结果
            translated = "".join([item[0] for item in data[0] if item[0]])
            print(f" ✅ 翻译成功 ({len(text)} → {len(translated)} 字符)")
            return translated
        else:
            print(f" ⚠️ 翻译失败 HTTP {resp.status_code}")
            return text
    except Exception as e:
        print(f" ⚠️ 翻译异常: {e}")
        return text  # 翻译失败时返回原文

# --------------------------------------------------------------------------- #
# Bark
# --------------------------------------------------------------------------- #
def push_bark(title, body):
    if not BARK_KEY:
        print("⚠️ BARK_KEY 未配置，跳过")
        return False
    print(f"\n📲 开始 Bark 推送...")
    print(f" URL: {BARK_SERVER}/push")
    print(f" body 长度: {len(body)} 字符")
    try:
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],
            "sound": "minuet",
            "group": "情报雷达",
        }
        resp = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        print(f" HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 200 and resp.json().get("code") == 200:
            print(" ✅ Bark 推送成功")
            return True
        else:
            print(" ❌ Bark 推送失败")
            return False
    except Exception as e:
        print(f" ❌ Bark 推送异常: {e}")
        return False

# --------------------------------------------------------------------------- #
# Notion
# --------------------------------------------------------------------------- #
def push_notion(title, summary, raw_news):
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️ NOTION_TOKEN 或 DATABASE_ID 未配置，跳过")
        return False
    print(f"\n📓 开始 Notion 写入...")
    print(f" DATABASE_ID: {DATABASE_ID[:8]}...")
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
            "Name": {"title": [{"text": {"content": title}}]},
            "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "Date": {"date": {"start": today_str}},
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
        print(f" HTTP {resp.status_code}")
        resp_json = resp.json()

        # 打印详细错误信息
        if resp.status_code == 200:
            print(" ✅ Notion 写入成功")
            print(f" Page ID: {resp_json.get('id', 'N/A')}")
            return True
        elif resp.status_code == 400:
            print(f" ❌ Notion 写入失败: 请求格式错误")
            print(f" 错误详情: {resp_json.get('message', resp.text[:200])}")
        elif resp.status_code == 401:
            print(f" ❌ Notion 写入失败: Token 无效")
        elif resp.status_code == 403:
            print(f" ❌ Notion 写入失败: 无权限访问该数据库")
            print(f" 请确认 DATABASE_ID 正确，且 Notion Integration 已添加到该数据库")
        elif resp.status_code == 404:
            print(f" ❌ Notion 写入失败: 数据库不存在")
            print(f" 请检查 DATABASE_ID 是否正确")
        else:
            print(f" ❌ Notion 写入失败: {resp_json.get('message', resp.text[:200])}")
        return False
    except Exception as e:
        print(f" ❌ Notion 写入异常: {e}")
        return False

# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    print_config_check()
    
    # 加载已推送的新闻
    seen_news = load_seen_news()
    print(f"\n📂 已加载 {len(seen_news)} 条已推送新闻记录")

    print("\n🚀 开始扫描情报源...")
    collected_news = []

    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            count = len(feed.entries[:3])
            print(f" 获取到 {count} 条")
            for entry in feed.entries[:3]:
                collected_news.append({
                    'title': entry.title,
                    'summary': entry.get('summary', '')[:200],
                    'source': feed_info['name']
                })
        except Exception as e:
            print(f"⚠️ 拉取 {feed_info['name']} 失败: {e}")

    if not collected_news:
        print("📭 今日无新动态，退出")
        return

    # 转换为用于去重的格式
    news_for_dedup = [{'title': n['title'], 'summary': n['summary'], 'source': n['source']} for n in collected_news]
    
    # 过滤出新新闻
    new_news = filter_new_news(news_for_dedup, seen_news)
    
    if not new_news:
        print("\n📭 没有新新闻，跳过推送")
        return
    
    print(f"\n📰 共收集到 {len(collected_news)} 条新闻，其中 {len(new_news)} 条为新新闻")

    # 重新构建收集的新闻列表（仅新新闻）
    collected_news = []
    for n in new_news:
        collected_news.append(
            f"【{n['source']}】{n['title']}\n"
            f"摘要: {n['summary']}"
        )

    # 生成AI总结
    summary = analyze_with_gemini("\n\n".join(collected_news))

    tz_cst = timezone(timedelta(hours=8))
    now = datetime.now(tz_cst)
    push_title = f"情报雷达 · {now.strftime('%Y-%m-%d %H:%M')}"

    if not summary:
        # 即使没有 AI 总结，也生成一个简略版本用于通知
        print("⚠️ 未能生成 AI 总结，使用原始新闻摘要...")
        # 将原始新闻压缩为摘要（修复：不在 f-string 中使用反斜杠）
        bullet_lines = []
        for line in collected_news[:5]:
            parts = line.split('】')
            if len(parts) > 1:
                title_part = parts[1].split('\n')[0][:100]
                bullet_lines.append("• " + title_part)
        raw_summary = "\n".join(bullet_lines)
        newline = "\n"
        summary = ("⚠️ AI 总结生成失败（配额耗尽），以下是今日原始新闻摘要："
                  + newline + newline + raw_summary
                  + newline + newline + "共抓取 "
                  + str(len(collected_news)) + " 条新闻，请访问情报雷达查看详情。")

        # 仍然尝试发送通知（使用错误提示）
        summary_cn = translate_to_chinese(summary)  # 翻译
        push_bark(push_title + " ⚠️", summary_cn)
        push_notion(push_title, summary, collected_news)
        
        # 保存到已推送记录
        save_seen_news(seen_news)
        print("\n✅ 通知已发送（无 AI 总结）")
        return

    print("\n📝 AI 总结 (前200字):\n", summary[:200], "...")

    # 翻译为中文后再发送通知
    summary_cn = translate_to_chinese(summary)

    # 先发 Bark（带翻译）
    push_bark(push_title, summary_cn)
    # 再写 Notion（保留英文原文供参考）
    push_notion(push_title, summary, collected_news)
    
    # 保存新新闻到已推送记录
    save_seen_news(seen_news)
    
    print("\n✅ 全部完成")

if __name__ == "__main__":
    main()
