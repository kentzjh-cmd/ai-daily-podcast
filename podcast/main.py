"""主流程编排：抓取 → 写稿 → TTS 合成 → 拼接 → 更新元数据 → 清理 → 生成站点

用法：
  python -m podcast.main              # 完整流程
  python -m podcast.main --no-tts     # 跳过 TTS 与合成（无 MiMo Key 时本地测试用）
  python -m podcast.main --no-llm     # 跳过 LLM 写稿，用兜底模板
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from podcast.audio import fmt_duration, get_duration_sec, merge_mp3
from podcast.build_page import build_site
from podcast.cleanup import cleanup, load_episodes, save_episodes
from podcast.fetch_news import main as fetch_news
from podcast.tts import synthesize_segments
from podcast.write_script import main as write_script

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def main():
    skip_tts = "--no-tts" in sys.argv
    skip_llm = "--no-llm" in sys.argv

    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    podcast_cfg = cfg["podcast"]

    # 1. 抓取资讯 + 2. 生成口播稿
    fetch_news()
    write_script(skip_llm=skip_llm)

    with open(DATA / "script.json", encoding="utf-8") as f:
        script = json.load(f)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    episodes = load_episodes()
    if any(ep["date"] == today for ep in episodes):
        print(f"[main] {today} 已有节目，本次运行将覆盖更新")

    if skip_tts:
        print("[main] --no-tts 模式：跳过 TTS 与音频合成")
        return

    # 3. TTS 合成（需要 MIMO_API_KEY）
    import os
    api_key = os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        raise RuntimeError("未设置 MIMO_API_KEY 环境变量")
    seg_dir = DATA / "segments"
    parts = synthesize_segments(script["segments"], cfg["tts"], api_key, seg_dir)

    # 4. 拼接成完整播客
    rel_file = f"episodes/{today}.mp3"
    out_path = DOCS / rel_file
    merge_mp3(parts, out_path, workdir=DATA)
    duration = get_duration_sec(out_path)
    print(f"[main] 播客合成完成：{rel_file}，时长 {fmt_duration(duration)}")

    # 5. 更新元数据（同一天重复运行时覆盖）
    episodes = [ep for ep in episodes if ep["date"] != today]
    episodes.insert(0, {
        "date": today,
        "title": script.get("title", "AI 每日资讯"),
        "file": rel_file,
        "duration_sec": duration,
        "duration_str": fmt_duration(duration),
        "size": out_path.stat().st_size,
        "segments": len(script["segments"]),
    })
    save_episodes(episodes)

    # 6. 清理过期音频 + 7. 生成站点
    cleanup(podcast_cfg.get("retention_days", 90))
    build_site(cfg, podcast_cfg)

    # 清理临时分段文件
    if seg_dir.exists():
        shutil.rmtree(seg_dir, ignore_errors=True)
    print("[main] 全部完成 ✅")


if __name__ == "__main__":
    main()
