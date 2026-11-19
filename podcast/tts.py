"""MiMo TTS 客户端：分段调用小米 MiMo 开放平台语音合成 API，带限流重试"""
import base64
import re
import time
from pathlib import Path

import requests


def split_text(text: str, max_chars: int) -> list:
    """按句子标点切分；超长句子硬切，保证每段不超过 max_chars"""
    sentences = re.split(r"(?<=[。！？.!?;；\n])\s*", text.strip())
    chunks, buf = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(buf) + len(s) <= max_chars:
            buf += s
        else:
            if buf:
                chunks.append(buf)
            while len(s) > max_chars:
                chunks.append(s[:max_chars])
                s = s[max_chars:]
            buf = s
    if buf:
        chunks.append(buf)
    return chunks or [text.strip()]


def call_tts(text: str, tts_cfg: dict, api_key: str) -> bytes:
    """单次 TTS 请求，429/5xx 指数退避重试。MiMo 接口：文本放 assistant 消息"""
    url = tts_cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    body = {
        "model": tts_cfg.get("model", "mimo-v2.5-tts"),
        "messages": [{"role": "assistant", "content": text}],
        "audio": {
            "format": tts_cfg.get("format", "mp3"),
            "voice": tts_cfg.get("voice", "mimo_default"),
        },
    }
    last_err = None
    for attempt in range(5):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=180)
            if resp.status_code in (429, 503) or resp.status_code >= 500:
                wait = 5 * (2 ** attempt)
                print(f"[tts] 限流/服务错误 {resp.status_code}，{wait}s 后重试")
                time.sleep(wait)
                last_err = f"HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
            audio_b64 = resp.json()["choices"][0]["message"]["audio"]["data"]
            return base64.b64decode(audio_b64)
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_err = str(exc)
            if attempt == 4:
                break
            time.sleep(5 * (2 ** attempt))
    raise RuntimeError(f"TTS 合成失败: {last_err}")


def synthesize_segments(segments: list, tts_cfg: dict, api_key: str, out_dir: Path) -> list:
    """合成所有段落，返回按顺序排列的分段 mp3 路径"""
    out_dir.mkdir(parents=True, exist_ok=True)
    max_chars = tts_cfg.get("max_chars_per_chunk", 900)
    paths = []
    for si, seg in enumerate(segments):
        for ci, chunk in enumerate(split_text(seg, max_chars)):
            data = call_tts(chunk, tts_cfg, api_key)
            p = out_dir / f"seg{si:02d}_{ci:02d}.mp3"
            p.write_bytes(data)
            paths.append(p)
            print(f"[tts] 段{si} 分片{ci} 完成 ({len(data)} bytes)")
    return paths
