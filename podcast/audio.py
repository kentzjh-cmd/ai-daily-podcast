"""音频处理：ffmpeg 拼接分段 mp3、去除首尾静音、插入停顿、提速"""
import re
import subprocess
from pathlib import Path


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 执行失败: {result.stderr[-400:]}")
    return result


def get_duration_sec(path: Path) -> float:
    """用 ffprobe 读时长，失败时按文件大小粗估（128kbps ≈ 16KB/s）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return max(1, path.stat().st_size // 16000)


def fmt_duration(sec) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def trim_silence(src: Path, dst: Path, threshold: str = "-50dB", min_silence: float = 0.04,
                 head: float = 0.12, tail: float = 0.16) -> Path:
    """裁掉每个 TTS 分段自带的首尾静音，让整期听感更紧凑。

    MiMo 每段音频前后各带一小段静音；直接拼接会一共有几十秒的「空档」。
    这里先用 silencedetect 量出首尾静音长度，再用 atrim 精确裁掉。
    """
    try:
        duration = get_duration_sec(src)
        if duration <= 0.3:
            return src
        proc = subprocess.run(
            ["ffmpeg", "-i", str(src), "-af", f"silencedetect=noise={threshold}:d={min_silence}",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        events = re.findall(r"silence_(start|end):\s*([0-9.]+)", proc.stderr)

        cut_head = 0.0
        cut_tail = 0.0
        if events:
            if events[0][0] == "start" and float(events[0][1]) < 0.2:
                # 开头静音：从 0 到第一个 silence_end
                cut_head = float(events[1][1]) if len(events) > 1 else 0.0
            # 末尾静音：最后一个 silence_end 到文件结束
            if events[-1][0] == "end":
                cut_tail = max(0.0, duration - float(events[-1][1]))

        cut_head = min(cut_head, head)
        cut_tail = min(cut_tail, tail)
        if cut_head <= 0.01 and cut_tail <= 0.01:
            return src

        end = max(0.1, duration - cut_tail)
        _run(["ffmpeg", "-y", "-i", str(src), "-af", f"atrim=start={cut_head:.3f}:end={end:.3f}",
              "-c:a", "libmp3lame", "-b:a", "128k", str(dst)])
        return dst
    except Exception as exc:  # noqa: BLE001
        print(f"[audio] 静音裁剪失败，保留原文件: {exc}")
        return src


def make_silence(dst: Path, seconds: float) -> Path:
    """生成一段静音（用于段间停顿）"""
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
          "-t", f"{seconds:.3f}", "-c:a", "libmp3lame", "-b:a", "128k", str(dst)])
    return dst


def merge_mp3(parts: list, out_path: Path, workdir: Path, speed: float = 1.0) -> Path:
    """合并所有分段。

    parts: [{"path": Path, "gap": float}]，gap 为该段之后的停顿秒数
    speed: >1 表示整体提速（atempo，音调不变）
    """
    workdir.mkdir(parents=True, exist_ok=True)
    concat_list = workdir / "concat.txt"

    silence_cache = {}
    lines = []
    for part in parts:
        p = part["path"]
        lines.append(f"file '{str(p.resolve()).replace(chr(39), chr(39) * 2)}'")
        gap = float(part.get("gap") or 0)
        if gap > 0:
            key = round(gap, 2)
            if key not in silence_cache:
                silence_cache[key] = make_silence(workdir / f"gap_{key}.mp3", key)
            lines.append(f"file '{str(silence_cache[key].resolve())}'")

    concat_list.write_text("\n".join(lines), encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    filters = []
    if speed and abs(speed - 1.0) > 0.01:
        filters.append(f"atempo={speed}")
    af = f"-af {','.join(filters)}" if filters else ""

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list)]
    if af:
        cmd += af.split(" ")
    cmd += ["-c:a", "libmp3lame", "-b:a", "128k", str(out_path)]
    _run(cmd)
    return out_path
