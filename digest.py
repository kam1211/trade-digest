import os, re, json, logging, smtplib, hashlib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
import google.generativeai as genai
import feedparser
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

SOURCES = [
    {"name": "Brookings Institution", "url": "https://www.brookings.edu/topic/trade-and-global-economy/feed/", "category": "智庫"},
    {"name": "CSIS", "url": "https://www.csis.org/taxonomy/term/41/feed", "category": "智庫"},
    {"name": "PIIE", "url": "https://www.piie.com/rss.xml", "category": "智庫"},
    {"name": "CFR", "url": "https://www.cfr.org/rss/topic/economics-and-global-finance.xml", "category": "智庫"},
    {"name": "AEI", "url": "https://www.aei.org/topics/economics/international-economics/feed/", "category": "智庫"},
    {"name": "Heritage Foundation", "url": "https://www.heritage.org/international-economies/rss.xml", "category": "智庫"},
    {"name": "Cato Institute", "url": "https://www.cato.org/rss/recent-op-eds", "category": "智庫"},
    {"name": "USTR", "url": "https://ustr.gov/rss.xml", "category": "官方"},
    {"name": "Reuters", "url": "https://feeds.reuters.com/reuters/businessNews", "category": "媒體"},
    {"name": "WSJ", "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "category": "媒體"},
]

KEYWORDS = ["tariff","trade war","trade policy","trade agreement","Section 301","Section 232",
    "IEEPA","semiconductor","chip","CHIPS Act","export control","supply chain","Taiwan","台灣",
    "reciprocal tariff","customs","duty","CFIUS","reshoring","industrial policy"]

def fetch_articles(since_hours=24):
    all_articles = []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)
    for src in SOURCES:
        try:
            feed = feedparser.parse(src["url"])
            for e in feed.entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                title = e.get("title", "")
                summary = BeautifulSoup(e.get("summary","") or e.get("description",""), "html.parser").get_text()
                summary = re.sub(r"\s+", " ", summary).strip()[:600]
                link = e.get("link", "")
                text = (title + " " + summary).lower()
                if any(k.lower() in text for k in KEYWORDS):
                    all_articles.append({
                        "source": src["name"], "category": src["category"],
                        "title": title, "summary": summary, "link": link,
                        "published": pub_dt.isoformat() if pub else "",
                        "id": hashlib.md5(link.encode()).hexdigest()
                    })
            log.info(f"✓ {src['name']}")
        except Exception as ex:
            log.warning(f"✗ {src['name']}: {ex}")
    seen, unique = set(), []
    for a in all_articles:
        if a["id"] not in seen:
            seen.add(a["id"]); unique.append(a)
    log.info(f"共 {len(unique)} 篇符合條件")
    return unique

SYSTEM = "你是台灣政府經貿政策分析師，追蹤美國智庫與媒體的經貿政策動態，以繁體中文政府公文語體分析。"

def analyze(model, article):
    prompt = f"""分析以下文章，以繁體中文回應，僅輸出JSON不含其他文字：

來源：{article['source']}
標題：{article['title']}
摘要：{article['summary']}

格式：
{{"zh_title":"繁中標題30字內","summary":"3至5句摘要說明核心論點與政策意涵","taiwan_relevance":"與台灣的關聯（無則填低度相關）","policy_tags":["標籤1","標籤2"],"importance":1到5的數字}}

importance：5=直接影響台灣對美貿易，4=影響台灣產業鏈，3=美國貿易政策動向，2=背景分析，1=邊緣相關"""
    try:
        resp = model.generate_content(SYSTEM + "\n\n" + prompt)
        raw = resp.text.strip().replace("```json","").replace("```","")
        result = json.loads(raw)
        result.update({"source": article["source"], "category": article["category"],
                       "link": article["link"], "published": article["published"]})
        return result
    except Exception as ex:
        log.warning(f"分析失敗 [{article['title'][:40]}]: {ex}")
        return None

def digest_summary(model, analyzed):
    top = [a for a in analyzed if a.get("importance",0) >= 3][:8]
    if not top: return "（今日無高重要度文章）"
    slim = [{"title": a.get("zh_title"), "summary": a.get("summary")} for a in top]
    try:
        resp = model.generate_content(
            f"以下是今日美國經貿政策重要文章摘要，請以繁體中文條列3至5點今日最重要趨勢，每點不超過50字，僅輸出條列文字：\n{json.dumps(slim, ensure_ascii=False)}")
        return resp.text.strip()
    except:
        return "（整體摘要生成失敗）"

def tag_pill(tag):
    colors = {"Section 301":"#c0392b","Section 232":"#d35400","關稅":"#7d3c98",
              "半導體":"#1a5276","供應鏈":"#1e8449","台灣":"#154360","出口管制":"#6e2f1a"}
    c = colors.get(tag, "#555")
    return f'<span style="display:inline-block;margin:2px 3px;padding:2px 8px;border-radius:10px;font-size:11px;background:{c};color:#fff">{tag}</span>'

def render_card(art):
    imp = art.get("importance", 1)
    border = {5:"#c0392b",4:"#e67e22",3:"#f1c40f"}.get(imp,"#ddd")
    label = {5:"🔴 緊要",4:"🟠 重要",3:"🟡 關注",2:"⬜ 參考",1:"⬜ 背景"}.get(imp,"")
    tags = "".join(tag_pill(t) for t in art.get("policy_tags",[]))
    rel = art.get("taiwan_relevance","")
    pub = art.get("published","")[:10]
    rel_html = f'<p style="margin:4px 0;font-size:12px;color:#555"><strong>台灣關聯：</strong>{rel}</p>' if rel and rel != "低度相關" else ""
    return f"""<div style="margin:10px 0;padding:12px 16px;border-left:4px solid {border};background:#fafafa;border-radius:0 6px 6px 0">
<div style="font-size:11px;color:#888;margin-bottom:4px">{label} | {art.get('source','')} | {pub}</div>
<a href="{art.get('link','')}" style="font-size:15px;font-weight:600;color:#1a1a2e;text-decoration:none">{art.get('zh_title','')}</a>
<p style="margin:8px 0 6px;font-size:13px;color:#333;line-height:1.7">{art.get('summary','')}</p>
{rel_html}<div>{tags}</div></div>"""

def render_email(summary, analyzed, date_str):
    sorted_arts = sorted(analyzed, key=lambda a: (-a.get("importance",0)))
    sections = ""
    for cat, label in [("官方","📋 官方動態"),("智庫","🏛 智庫分析"),("媒體","📰 財經媒體")]:
        arts = [a for a in sorted_arts if a.get("category") == cat]
        if arts:
            sections += f'<h3 style="font-size:14px;font-weight:700;color:#2c3e50;border-bottom:1px solid #ddd;padding-bottom:6px;margin:20px 0 8px">{label}</h3>'
            sections += "".join(render_card(a) for a in arts)
    bullets = "\n".join(f'<li style="margin:6px 0;line-height:1.7">{l.lstrip("•·-– ").strip()}</li>'
                        for l in summary.split("\n") if l.strip())
    high = sum(1 for a in analyzed if a.get("importance",0) >= 4)
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0"><tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1)">
<tr><td style="background:#1a1a2e;padding:28px 32px;border-radius:8px 8px 0 0">
<div style="font-size:11px;color:#a0b4d0;letter-spacing:2px;margin-bottom:8px">DAILY INTELLIGENCE BRIEF</div>
<div style="font-size:22px;font-weight:700;color:#fff">美國經貿政策 AI 日報</div>
<div style="font-size:13px;color:#a0b4d0;margin-top:6px">{date_str}</div></td></tr>
<tr><td style="padding:24px 32px 8px">
<h2 style="font-size:15px;font-weight:700;color:#1a1a2e;margin:0 0 12px">📌 今日要點</h2>
<ul style="margin:0;padding-left:18px;color:#333;font-size:13px">{bullets}</ul></td></tr>
<tr><td style="padding:8px 32px">
<div style="background:#f7f8fa;border-radius:6px;padding:10px 16px;font-size:12px;color:#666">
共收錄 <strong>{len(analyzed)}</strong> 篇 ／ 高重要度（4-5分）：<strong style="color:#c0392b">{high}</strong> 篇</div></td></tr>
<tr><td style="padding:4px 32px 24px">{sections}</td></tr>
<tr><td style="background:#f7f8fa;padding:16px 32px;border-top:1px solid #eee;border-radius:0 0 8px 8px">
<p style="margin:0;font-size:11px;color:#999">本報告由 Python 自動抓取公開 RSS，經 Gemini AI 分析生成繁體中文摘要，內容僅供參考。</p>
</td></tr></table></td></tr></table></body></html>"""

def send_email(html, subject, cfg):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(cfg["recipients"])
    msg.attach(MIMEText("請以 HTML 格式查看。", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as s:
        s.login(cfg["smtp_user"], cfg["smtp_password"])
        s.sendmail(cfg["sender"], cfg["recipients"], msg.as_bytes())
    log.info(f"✉ 已寄送至 {cfg['recipients']}")

def main():
    cfg = {
        "gemini_api_key": os.environ["GEMINI_API_KEY"],
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
        "smtp_user": os.environ["SMTP_USER"],
        "smtp_password": os.environ["SMTP_PASSWORD"],
        "sender": os.environ.get("EMAIL_FROM", os.environ["SMTP_USER"]),
        "recipients": os.environ["EMAIL_TO"].split(","),
    }
    genai.configure(api_key=cfg["gemini_api_key"])
    model = genai.GenerativeModel("gemini-1.5-flash")
    days = ["週一","週二","週三","週四","週五","週六","週日"]
    now = datetime.now()
    date_str = f"{now.year} 年 {now.month} 月 {now.day} 日（{days[now.weekday()]}）"
    log.info(f"=== {date_str} 經貿政策日報 開始執行 ===")
    articles = fetch_articles(24)
    if not articles:
        log.warning("今日無符合條件的文章"); return
    analyzed = []
    for i, art in enumerate(articles, 1):
        log.info(f"[{i}/{len(articles)}] {art['title'][:50]}")
        r = analyze(model, art)
        if r: analyzed.append(r)
    if not analyzed:
        log.error("所有文章分析失敗"); return
    summary = digest_summary(model, analyzed)
    html = render_email(summary, analyzed, date_str)
    subject = f"【經貿政策日報】{date_str}｜{len(analyzed)} 篇摘要"
    send_email(html, subject, cfg)
    log.info("=== 完成 ===")

if __name__ == "__main__":
    main()
