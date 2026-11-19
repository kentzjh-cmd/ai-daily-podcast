"""抓取各资讯源 RSS，筛选最近 48h 的条目，输出 data/news.json"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 各源最多取的条目数（避免单源刷屏）
PER_SOURCE_LIMIT = 15


def load_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sources():
    with open(ROOT / "config" / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_dt(entry):
    raw = entry.get("published_parsed") or entry.get("updated_parsed")
    if not raw:
        return None
    try:
        return datetime(*raw[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def fetch_source(src):
    """抓取单个源，失败时返回空列表（单个源挂掉不影响整体）"""
    try:
        feed = feedparser.parse(src["url"])
        items = []
        for e in feed.entries[:PER_SOURCE_LIMIT]:
            title = (e.get("title") or "").strip()
            if not title:
                continue
            items.append({
                "source": src["name"],
                "lang": src.get("lang", "zh"),
                "title": title,
                "link": e.get("link", ""),
                "summary": strip_html(e.get("summary") or e.get("description") or "")[:300],
                "published": parse_dt(e).isoformat() if parse_dt(e) else None,
            })
        return items
    except Exception as exc:
        print(f"[fetch] 源 {src['name']} 抓取失败: {exc}")
        return []


def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = hashlib.md5(it["title"].lower().encode("utf-8")).hexdigest()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def select_items(items, max_items):
    """轮询各源轮流取，保证来源多样性"""
    by_src = {}
    for it in items:
        by_src.setdefault(it["source"], []).append(it)
    selected = []
    while len(selected) < max_items:
        progressed = False
        for lst in by_src.values():
            if lst and len(selected) < max_items:
                selected.append(lst.pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def main():
    cfg = load_config()
    max_items = cfg["podcast"].get("max_items", 12)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    all_items = []
    for src in load_sources():
        all_items.extend(fetch_source(src))
        print(f"[fetch] {src['name']}: 累计 {len(all_items)} 条")

    recent = [it for it in all_items if it["published"] and datetime.fromisoformat(it["published"]) >= cutoff]
    # 太老或无日期的条目丢弃一半以上来源不可靠时，回退到不过滤，保证每天有产出
    if len(recent) < 5:
        recent = all_items
        print(f"[fetch] 48h 内条目不足，回退使用全部 {len(recent)} 条")

    recent = dedupe(recent)
    selected = select_items(recent, max_items)
    print(f"[fetch] 最终选用 {len(selected)} 条")

    DATA.mkdir(exist_ok=True)
    with open(DATA / "news.json", "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "items": selected},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
