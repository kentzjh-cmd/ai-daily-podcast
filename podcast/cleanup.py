"""保留期清理：删除超过 retention_days 的旧音频并同步 episodes.json，防止仓库无限膨胀"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
EPISODES_JSON = DOCS / "episodes.json"


def load_episodes() -> list:
    if EPISODES_JSON.exists():
        with open(EPISODES_JSON, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_episodes(episodes: list):
    DOCS.mkdir(exist_ok=True)
    with open(EPISODES_JSON, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)


def cleanup(retention_days: int) -> int:
    """删除超期音频文件和元数据，返回删除的期数"""
    episodes = load_episodes()
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    kept, removed = [], []
    for ep in episodes:
        try:
            ep_date = datetime.fromisoformat(ep["date"]).date()
        except Exception:
            kept.append(ep)
            continue
        if ep_date >= cutoff:
            kept.append(ep)
        else:
            removed.append(ep)
            audio = ROOT / "docs" / ep.get("file", "")
            if audio.exists():
                audio.unlink()
                print(f"[cleanup] 删除过期音频 {ep['date']}: {ep.get('file')}")
            report = ROOT / "docs" / (ep.get("report") or "")
            if ep.get("report") and report.exists():
                report.unlink()
                print(f"[cleanup] 删除过期日报 {ep['date']}: {ep.get('report')}")
    if removed:
        save_episodes(kept)
    return len(removed)


if __name__ == "__main__":
    import yaml
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    n = cleanup(cfg["podcast"].get("retention_days", 90))
    print(f"[cleanup] 共清理 {n} 期过期音频")
