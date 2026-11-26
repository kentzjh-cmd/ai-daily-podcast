"""抓取资讯（唯一数据源：AIHOT），输出 data/news.json"""
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from podcast.fetch_aihot import build_news

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    cfg = load_config()
    payload = build_news(cfg)
    payload["fetched_at"] = datetime.now(timezone.utc).isoformat()

    DATA.mkdir(exist_ok=True)
    with open(DATA / "news.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[fetch] 已写入 data/news.json（条目 {len(payload.get('items') or [])} 条，"
          f"头条候选 {len(payload.get('leads') or [])} 个）")


if __name__ == "__main__":
    main()
