"""唯一数据源：AIHOT 公开只读 API（https://aihot.news）

AIHOT 已经过 LLM 筛选、打分、聚簇，并给出中文标题/摘要/入选理由与多源聚合
digest，比自己抓 RSS 再筛选质量高得多。接口无需 API Key：

- GET /api/v1/dailies/latest      当日日报（头条 + 分栏目条目）
- GET /api/v1/items               24h 精选条目（带评分、入选理由）
- GET /api/v1/hot-topics          多源聚合热点（带报道来源数）
- GET /api/v1/stories/{publicId}  单个事件的跨源聚合 digest（用于头条）

站点会校验 User-Agent，缺失时返回 403。
使用条款要求署名：数据来源标注为 AIHOT（https://aihot.news）。
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

# 播客三大板块 + AIHOT 栏目映射
SECTIONS = ["模型与产品", "行业与资本", "研究与实践"]

SECTION_MAP = {
    # 日报栏目标签
    "模型发布/更新": "模型与产品",
    "产品发布/更新": "模型与产品",
    "行业动态": "行业与资本",
    "论文研究": "研究与实践",
    "技巧与观点": "研究与实践",
    # API 分类值
    "ai-models": "模型与产品",
    "ai-products": "模型与产品",
    "industry": "行业与资本",
    "paper": "研究与实践",
    "tip": "研究与实践",
    "opinion": "研究与实践",
}

DEFAULT_UA = "ai-daily-podcast/1.0 (podcast bot; +https://aihot.news)"


# ---------------------------------------------------------------- HTTP

def _headers(ua: str) -> dict:
    return {"User-Agent": ua, "Accept": "application/json", "Accept-Encoding": "gzip, deflate"}


def _get_json(url: str, ua: str, retries: int = 3, timeout: int = 30):
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=_headers(ua), timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"AIHOT 请求失败 {url}: {last_err}")


# ---------------------------------------------------------------- 清洗

def clean_source(name: str) -> str:
    """把 AIHOT 的来源名清洗成适合口播的短名"""
    s = (name or "").strip()
    s = re.sub(r"（[^）]{0,8}）", "", s)          # 去掉（RSS）（网页）（公众号）
    s = re.sub(r"\([^)]*\)", "", s)               # 去掉括号注释
    s = re.sub(r"@\S+", "", s)                    # 去掉 @handle
    s = re.sub(r"^公众号[：:]\s*", "", s)
    s = re.sub(r"^X[：:]\s*", "", s)
    s = re.sub(r"^微博[：:]\s*", "", s)
    s = re.sub(r"^[一-龥A-Za-z0-9 .·]{2,14}[：:]\s*(AI(新闻)?|人工智能|资讯|科技)?\s*$", "", s)
    s = re.sub(r"[·・]\s*(人工智能|AI|资讯|科技).*$", "", s)
    s = re.sub(r"^AI\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ·-—_")
    if not s:
        return "AIHOT"
    return s if len(s) <= 16 else s[:16]


def clean_text(text: str) -> str:
    """去掉链接、残留 markdown 与英文标点，便于朗读"""
    t = text or ""
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------- 抓取

def fetch_daily(base_url: str, ua: str):
    data = _get_json(f"{base_url.rstrip('/')}/api/v1/dailies/latest?lang=zh", ua)
    return data.get("report") or {}


def fetch_items(base_url: str, ua: str, window: str = "24h", limit: int = 60):
    url = (f"{base_url.rstrip('/')}/api/v1/items"
           f"?mode=selected&window={window}&limit={limit}&lang=zh&by=timeline")
    data = _get_json(url, ua)
    return data.get("items") or []


def fetch_hot_topics(base_url: str, ua: str, limit: int = 10):
    data = _get_json(f"{base_url.rstrip('/')}/api/v1/hot-topics?lang=zh", ua)
    return (data.get("items") or [])[:limit]


def fetch_story(base_url: str, ua: str, public_id: str):
    data = _get_json(f"{base_url.rstrip('/')}/api/v1/stories/{public_id}?lang=zh", ua)
    return data.get("story") or {}


# ---------------------------------------------------------------- 归一化

def _norm_item(raw: dict, section: str = "", digest: str = "", source_count: int = 0,
               is_lead: bool = False) -> dict:
    src = (raw.get("source") or {}).get("name") or "AIHOT"
    links = raw.get("links") or {}
    return {
        "id": raw.get("id") or "",
        "title": clean_text(raw.get("title") or ""),
        "summary": clean_text(raw.get("summary") or ""),
        "reason": clean_text(raw.get("reason") or ""),
        "score": raw.get("score") or 0,
        "source": src,
        "source_short": clean_source(src),
        "link": links.get("original") or "",
        "aihot_link": links.get("aihot") or "",
        "section": section,
        "category": raw.get("category") or "",
        "published": raw.get("publishedAt") or "",
        "digest": clean_text(digest),
        "source_count": source_count,
        "is_lead": is_lead,
    }


def build_news(cfg: dict) -> dict:
    """抓取并组装 data/news.json 的内容"""
    aihot_cfg = (cfg.get("aihot") or {})
    base_url = aihot_cfg.get("base_url", "https://aihot.news")
    ua = aihot_cfg.get("user_agent") or DEFAULT_UA
    window = aihot_cfg.get("window", "24h")
    limit = int(aihot_cfg.get("limit", 60))

    daily = {}
    try:
        daily = fetch_daily(base_url, ua)
        print(f"[aihot] 日报 {daily.get('date')} 已获取")
    except Exception as exc:  # noqa: BLE001
        print(f"[aihot] 日报抓取失败: {exc}")

    raw_items = []
    try:
        raw_items = fetch_items(base_url, ua, window=window, limit=limit)
        print(f"[aihot] 24h 精选条目 {len(raw_items)} 条")
    except Exception as exc:  # noqa: BLE001
        print(f"[aihot] 条目抓取失败: {exc}")

    hot_raw = []
    try:
        hot_raw = fetch_hot_topics(base_url, ua)
        print(f"[aihot] 多源热点 {len(hot_raw)} 个")
    except Exception as exc:  # noqa: BLE001
        print(f"[aihot] 热点抓取失败: {exc}")

    # 热点事件：并行取跨源聚合 digest，作为头条素材
    stories = {}
    story_ids = [(h.get("storyPublicId"), h.get("id")) for h in hot_raw if h.get("storyPublicId")]
    if story_ids:
        def _load(pair):
            pid, item_id = pair
            try:
                return pid, fetch_story(base_url, ua, pid).get("digest") or ""
            except Exception:  # noqa: BLE001
                return pid, ""

        with ThreadPoolExecutor(max_workers=5) as pool:
            for pid, digest in pool.map(_load, story_ids):
                stories[pid] = digest

    # 1) 24h 精选条目（主料）
    items = []
    seen_titles = set()
    for raw in raw_items:
        section = SECTION_MAP.get(raw.get("category") or "", "")
        it = _norm_item(raw, section)
        key = it["title"].lower()
        if not it["title"] or key in seen_titles:
            continue
        seen_titles.add(key)
        items.append(it)

    # 2) 多源热点 → 头条候选（带聚合 digest），只保留时间窗内的
    window_hours = 24 if window == "24h" else 168
    now = datetime.now(timezone.utc)
    leads = []
    for h in hot_raw:
        latest = h.get("latestAt") or ""
        if latest:
            try:
                ts = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if (now - ts).total_seconds() > window_hours * 3600:
                    continue
            except Exception:  # noqa: BLE001
                pass
        pid = h.get("storyPublicId") or ""
        digest = stories.get(pid, "")
        section = SECTION_MAP.get(h.get("category") or "", "")
        it = _norm_item(h, section, digest=digest,
                        source_count=h.get("sourceCount") or 0, is_lead=True)
        if it["title"] and it["title"].lower() not in seen_titles:
            seen_titles.add(it["title"].lower())
            leads.append(it)

    # 3) 日报头条（lead）作为补充头条候选
    lead_info = daily.get("lead") or {}
    if lead_info.get("title"):
        it = _norm_item(
            {"id": "daily-lead", "title": lead_info.get("title"),
             "summary": lead_info.get("leadParagraph"), "source": {"name": "AIHOT 日报"},
             "links": {"aihot": (daily.get("links") or {}).get("aihot")}},
            "", is_lead=True)
        it["source_short"] = "AIHOT 日报"
        if it["title"].lower() not in seen_titles:
            seen_titles.add(it["title"].lower())
            leads.append(it)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "aihot",
        "attribution": {"name": "AIHOT", "url": "https://aihot.news"},
        "daily": {"date": daily.get("date"), "url": (daily.get("links") or {}).get("aihot")},
        "sections": SECTIONS,
        "items": items,
        "leads": leads,
    }
    print(f"[aihot] 组装完成：条目 {len(items)} 条，头条候选 {len(leads)} 个")
    return payload
