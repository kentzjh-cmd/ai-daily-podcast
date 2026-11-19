"""生成 GitHub Pages 站点：index.html（播放器列表）+ feed.xml（播客 RSS）"""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - 每日 AI 资讯播客</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         max-width: 720px; margin: 0 auto; padding: 24px 16px;
         background: #0f172a; color: #e2e8f0; }}
  h1 {{ font-size: 1.5rem; }} .meta {{ color: #94a3b8; font-size: .85rem; }}
  .episode {{ background: #1e293b; border-radius: 12px; padding: 16px;
              margin-bottom: 16px; }}
  .episode h2 {{ font-size: 1.1rem; margin: 0 0 4px; }}
  audio {{ width: 100%; margin-top: 10px; }}
  a {{ color: #38bdf8; text-decoration: none; }}
  footer {{ text-align: center; color: #64748b; font-size: .8rem; margin-top: 32px; }}
</style>
</head>
<body>
<h1>🎙️ {title}</h1>
<p class="meta">每日自动抓取 AI 资讯（模型厂商 / Agent与应用 / 硬件与行业 / 模型排行四大栏目），由 LLM 撰写图文日报与口播稿、MiMo TTS 语音合成。共 {count} 期。</p>
{episodes_html}
<footer>由 GitHub Actions 每日自动生成 · <a href="feed.xml">RSS 订阅</a></footer>
</body>
</html>
"""

EPISODE_ITEM = """<div class="episode">
<h2>{title}</h2>
<p class="meta">{date} · 时长 {duration} · <a href="{file}" download>下载 MP3</a>{report_link}</p>
<audio controls preload="none" src="{file}"></audio>
</div>
"""

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
<title>{title}</title>
<link>{site_url}</link>
<description>每日 AI 资讯播客：自动抓取、LLM 撰稿、MiMo TTS 合成</description>
<language>zh-cn</language>
<itunes:author>{author}</itunes:author>
{items}
</channel>
</rss>
"""

FEED_ITEM = """<item>
<title>{title}</title>
<enclosure url="{audio_url}" length="{size}" type="audio/mpeg"/>
<pubDate>{pubdate}</pubDate>
<guid>{audio_url}</guid>
</item>
"""


def build_site(site_cfg: dict, podcast_cfg: dict):
    episodes_file = DOCS / "episodes.json"
    episodes = []
    if episodes_file.exists():
        with open(episodes_file, encoding="utf-8") as f:
            episodes = json.load(f)

    title = html.escape(podcast_cfg.get("title", "AI 每日资讯"))
    author = html.escape(podcast_cfg.get("author", "AI Daily Podcast Bot"))
    site_url = podcast_cfg.get("site_base_url", "").rstrip("/")

    items_html = ""
    for ep in episodes:
        report_link = ""
        if ep.get("report"):
            report_link = f' · <a href="{ep["report"]}">📄 图文日报</a>'
        items_html += EPISODE_ITEM.format(
            title=html.escape(ep.get("title", "AI 每日资讯")),
            date=ep["date"],
            duration=ep.get("duration_str", "--:--"),
            file=ep.get("file", ""),
            report_link=report_link,
        )

    (DOCS / "index.html").write_text(
        INDEX_TEMPLATE.format(title=title, count=len(episodes), episodes_html=items_html),
        encoding="utf-8",
    )

    feed_items = ""
    for ep in episodes:
        audio_url = f"{site_url}/{ep.get('file', '')}" if site_url else ep.get("file", "")
        feed_items += FEED_ITEM.format(
            title=html.escape(ep.get("title", "AI 每日资讯")),
            audio_url=html.escape(audio_url),
            size=ep.get("size", 0),
            pubdate=datetime.fromisoformat(ep["date"]).replace(tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        )
    (DOCS / "feed.xml").write_text(
        FEED_TEMPLATE.format(title=title, author=author, site_url=site_url, items=feed_items),
        encoding="utf-8",
    )
    print(f"[page] 站点生成完成：{len(episodes)} 期")


if __name__ == "__main__":
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    build_site(cfg, cfg["podcast"])
