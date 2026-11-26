"""主流程编排：抓取 → 写稿 → TTS 合成 → 拼接 → 产出音频制品

用法：
  python -m podcast.main              # 完整流程（音频输出到 data/episodes/）
  python -m podcast.main --no-tts     # 跳过 TTS 与合成（无 MiMo Key 时本地测试用）
"""
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from podcast.audio import fmt_duration, get_duration_sec, merge_mp3
from podcast.fetch_news import main as fetch_news
from podcast.tts import synthesize_segments
from podcast.write_script import main as write_script

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EPISODES = DATA / "episodes"


def main():
    skip_tts = "--no-tts" in sys.argv

    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    podcast_cfg = cfg["podcast"]

    # 1. 抓取资讯 + 2. 生成口播稿
    fetch_news()
    write_script()

    with open(DATA / "script.json", encoding="utf-8") as f:
        script = json.load(f)

    now_cn = datetime.now(timezone(timedelta(hours=8)))
    today = f"{now_cn.year}-{now_cn.month:02d}-{now_cn.day:02d}"

    if skip_tts:
        print("[main] --no-tts 模式：跳过 TTS 与音频合成")
        return

    # 3. TTS 合成（需要 MIMO_API_KEY）
    api_key = os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        raise RuntimeError("未设置 MIMO_API_KEY 环境变量")
    tts_cfg = dict(cfg["tts"])
    tts_cfg.setdefault("voice", podcast_cfg.get("voice", "mimo_default"))
    seg_dir = DATA / "segments"
    parts = synthesize_segments(script["segments"], tts_cfg, api_key, seg_dir,
                                kinds=script.get("kinds"))

    # 4. 拼接成完整播客（音频制品）
    EPISODES.mkdir(parents=True, exist_ok=True)
    out_path = EPISODES / f"{today}.mp3"
    speed = float(cfg.get("tts", {}).get("speed", 1.0) or 1.0)
    merge_mp3(parts, out_path, workdir=DATA, speed=speed)
    duration = get_duration_sec(out_path)
    print(f"[main] 播客合成完成：{out_path}，时长 {fmt_duration(duration)}")

    # 5. 写出本期元数据（随制品一起归档）
    meta = {
        "date": today,
        "title": script.get("title", "AI 每日资讯"),
        "file": out_path.name,
        "duration_sec": duration,
        "duration_str": fmt_duration(duration),
        "size": out_path.stat().st_size,
        "segments": len(script["segments"]),
        "source": "AIHOT",
        "attribution": "资讯整理自AIhot社区",
    }
    with open(DATA / "episode.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 6. 清理临时分段文件
    if seg_dir.exists():
        shutil.rmtree(seg_dir, ignore_errors=True)
    print("[main] 全部完成 ✅")


if __name__ == "__main__":
    main()
