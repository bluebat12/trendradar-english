import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta

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

# --- 2. RSS 情报源 ---
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
#  Gemini — 修复版：429和404都继续尝试，只要有一个模型成功即可
# --------------------------------------------------------------------------- #
def _call_gemini(api_key, prompt):
    """
    用单个 Key 尝试所有模型。
    - 成功: 返回文本
    - 该 Key 所有模型均 429: 返回 '__QUOTA_EXCEEDED__' → 调用方切换下一个 Key
    - 遇到真正错误(非429/404): 返回 '__ERROR__' → 调用方终止
    """
    model_attempts = [
        ("v1",     "gemini-2.5-flash"),
        ("v1",     "gemini-2.0-flash-lite"),
        ("v1",     "gemini-2.0-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1",     "gemini-1.5-flash"),
    ]
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "你是一位资深行业分析师，擅长从 RSS 摘要中提取关键投资信息。\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.7},
    }

    got_429 = False   # 是否遇到过 429
    got_success = False

    for api_ver, model in model_attempts:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=30)
            code = resp.status_code
            print(f"    [{api_ver}/{model}] HTTP {code}")

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
                print(f"    ❌ 完整错误: {resp.text[:200]}")
                return "__ERROR__"

        except Exception as e:
            print(f"    ❌ 请求异常: {e}")
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
        print(f"  🔄 尝试 {key_label}")
        result = _call_gemini(key, prompt)

        if result == "__QUOTA_EXCEEDED__":
            print(f"  ⚠️  {key_label} 配额耗尽或不可用，切换下一个...")
            continue
        elif result == "__ERROR__":
            print(f"  ❌ {key_label} 遇到严重错误，终止")
            return None
        elif result is not None:
            print(f"  ✅ {key_label} 调用成功，摘要长度={len(result)}字符")
            return result

    print("❌ 所有 Gemini Key 均不可用，今日配额可能已耗尽")
    return None

# --------------------------------------------------------------------------- #
#  Bark
# --------------------------------------------------------------------------- #
def push_bark(title, body):
    if not BARK_KEY:
        print("⚠️  BARK_KEY 未配置，跳过")
        return False
    print(f"\n📲 开始 Bark 推送...")
    print(f"  URL: {BARK_SERVER}/push")
    print(f"  body 长度: {len(body)} 字符")
    try:
        payload = {
            "device_key": BARK_KEY,
            "title": title,
            "body": body[:2000],
            "sound": "minuet",
            "group": "情报雷达",
        }
        resp = requests.post(f"{BARK_SERVER}/push", json=payload, timeout=15)
        print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 200 and resp.json().get("code") == 200:
            print("  ✅ Bark 推送成功")
            return True
        else:
            print("  ❌ Bark 推送失败")
            return False
    except Exception as e:
        print(f"  ❌ Bark 推送异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  Notion
# --------------------------------------------------------------------------- #
def push_notion(title, summary, raw_news):
    if not NOTION_TOKEN or not DATABASE_ID:
        print("⚠️  NOTION_TOKEN 或 DATABASE_ID 未配置，跳过")
        return False
    print(f"\n📓 开始 Notion 写入...")
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
        print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 200:
            print("  ✅ Notion 写入成功")
            return True
        else:
            print("  ❌ Notion 写入失败")
            return False
    except Exception as e:
        print(f"  ❌ Notion 写入异常: {e}")
        return False

# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def main():
    print_config_check()
    print("\n🚀 开始扫描情报源...")
    collected_news = []

    for feed_info in RSS_FEEDS:
        print(f"📡 正在拉取: {feed_info['name']}")
        try:
            feed = feedparser.parse(feed_info["url"])
            count = len(feed.entries[:3])
            print(f"   获取到 {count} 条")
            for entry in feed.entries[:3]:
                collected_news.append(
                    f"【{feed_info['name']}】{entry.title}\n"
                    f"摘要: {entry.get('summary', '')[:200]}"
                )
        except Exception as e:
            print(f"⚠️  拉取 {feed_info['name']} 失败: {e}")

    if not collected_news:
        print("📭 今日无新动态，退出")
        return

    print(f"\n📰 共收集到 {len(collected_news)} 条新闻，正在生成 AI 总结...")
    summary = analyze_with_gemini("\n\n".join(collected_news))

    tz_cst = timezone(timedelta(hours=8))
    push_title = f"情报雷达 · {datetime.now(tz_cst).strftime('%Y-%m-%d')}"

    if not summary:
        # 即使没有 AI 总结，也生成一个简略版本用于通知
        print("⚠️  未能生成 AI 总结，使用原始新闻摘要...")
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
        push_bark(push_title + " ⚠️", summary)
        push_notion(push_title, summary, collected_news)
        print("\n✅ 通知已发送（无 AI 总结）")
        return

    print("\n📝 AI 总结 (前200字):\n", summary[:200], "...")

    push_bark(push_title, summary)
    push_notion(push_title, summary, collected_news)
    print("\n✅ 全部完成")

if __name__ == "__main__":
    main()
