"""音频处理：ffmpeg 拼接分段 mp3、获取时长"""
import subprocess
from pathlib import Path


def merge_mp3(parts: list, out_path: Path, workdir: Path) -> Path:
    """用 ffmpeg concat 合并所有分段，统一转码为 128kbps 保证参数一致"""
    workdir.mkdir(parents=True, exist_ok=True)
    concat_list = workdir / "concat.txt"
    lines = []
    for p in parts:
        # ffmpeg concat demuxer 要求单引号转义
        lines.append(f"file '{str(p.resolve()).replace(chr(39), chr(39)*2)}'")
    concat_list.write_text("\n".join(lines), encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 合并失败: {result.stderr[-500:]}")
    return out_path


def get_duration_sec(path: Path) -> int:
    """用 ffprobe 读时长，失败时按文件大小粗估（128kbps ≈ 16KB/s）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return max(1, path.stat().st_size // 16000)


def fmt_duration(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"
