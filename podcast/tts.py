"""MiMo TTS 客户端：分段调用小米 MiMo 开放平台语音合成 API，带限流重试与静音裁剪"""
import base64
import re
import time
from pathlib import Path

import requests

from podcast.audio import trim_silence

# 不同段落之间的停顿（秒）
GAP_BY_KIND = {
    "opening": 0.25,
    "transition": 0.40,
    "lead": 0.16,
    "item": 0.12,
    "closing": 0.20,
    "attribution": 0.0,
}


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


def build_messages(text: str, style: str) -> list:
    """MiMo TTS 支持用 user 消息传风格指令（语速/语气），社区客户端普遍这么用"""
    msgs = []
    if style:
        msgs.append({"role": "user", "content": style})
    msgs.append({"role": "assistant", "content": text})
    return msgs


def call_tts(text: str, tts_cfg: dict, api_key: str) -> bytes:
    """单次 TTS 请求，429/5xx 指数退避重试；音色被拒时自动回退默认音色。

    MiMo 接口：文本放 assistant 消息。音色 id 写错会返回 400，
    此时改用 mimo_default 重试，保证不因音色问题整期失败。
    """
    url = tts_cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    voices = [tts_cfg.get("voice", "mimo_default"), "mimo_default"]
    seen = []
    for v in voices:
        if v and v not in seen:
            seen.append(v)

    last_err = None
    voice = seen[0]
    for attempt in range(5):
        try:
            body = {
                "model": tts_cfg.get("model", "mimo-v2.5-tts"),
                "messages": build_messages(text, tts_cfg.get("style", "")),
                "audio": {
                    "format": tts_cfg.get("format", "wav"),
                    "voice": voice,
                },
            }
            resp = requests.post(url, headers=headers, json=body, timeout=180)
            if resp.status_code == 400 and len(seen) > 1:
                print(f"[tts] 音色 {voice} 被拒（400）: {resp.text[:200]}")
                voice = seen[1]
                continue
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


def group_utterances(segments: list, kinds: list) -> list:
    """把「板块过渡句 + 第一条内容」合成一次合成请求。

    过渡句单独成段会多一次请求、多一段静音，听起来一顿一顿；
    合并后语气连贯，也少一半的请求数。
    """
    groups = []
    i = 0
    while i < len(segments):
        kind = kinds[i] if i < len(kinds) else "item"
        text = segments[i]
        if kind == "transition" and i + 1 < len(segments):
            nxt = kinds[i + 1] if i + 1 < len(kinds) else "item"
            if nxt in ("item", "lead"):
                groups.append({"text": text + segments[i + 1], "kind": nxt,
                               "gap": GAP_BY_KIND.get(nxt, 0.12)})
                i += 2
                continue
        groups.append({"text": text, "kind": kind, "gap": GAP_BY_KIND.get(kind, 0.12)})
        i += 1
    return groups


def synthesize_segments(segments: list, tts_cfg: dict, api_key: str, out_dir: Path,
                       kinds: list | None = None) -> list:
    """合成所有段落，返回按顺序排列的分段信息（含段后停顿）"""
    out_dir.mkdir(parents=True, exist_ok=True)
    max_chars = tts_cfg.get("max_chars_per_chunk", 2000)
    fmt = tts_cfg.get("format", "wav")
    kinds = kinds or ["item"] * len(segments)
    groups = group_utterances(segments, kinds)
    parts = []
    for gi, g in enumerate(groups):
        for ci, chunk in enumerate(split_text(g["text"], max_chars)):
            data = call_tts(chunk, tts_cfg, api_key)
            raw = out_dir / f"seg{gi:02d}_{ci:00d}_raw.{fmt}"
            raw.write_bytes(data)
            final = trim_silence(raw, out_dir / f"seg{gi:02d}_{ci:00d}.{fmt}")
            if final != raw:
                raw.unlink(missing_ok=True)
            parts.append({"path": final, "gap": g["gap"], "kind": g["kind"]})
            print(f"[tts] 段{gi}({g['kind']}) 分片{ci} 完成 ({len(data)} bytes)")
    return parts
