"""调用 OpenAI 兼容 LLM 生成单主播口播稿，输出 data/script.json"""
import json
import re
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SYSTEM_PROMPT = (
    "你是一名专业的科技新闻播客主播，风格专业、简洁、有活力。"
    "你会根据当天收集到的 AI 资讯，写一份中文口播稿。"
    "要求：1) 口语化，避免书面长句；2) 每条资讯用 2-3 句话概括核心内容，"
    "英文来源的内容要翻译成中文播报；3) 论文类条目用一句话讲清'做了什么+有什么用'；"
    "4) 开头是欢迎语，结尾是固定结束语'以上就是今天的全部内容，我们明天见。'；"
    "5) 严格按要求的 JSON 格式输出，不要输出多余内容。"
)

USER_PROMPT_TEMPLATE = """请基于以下今日 AI 资讯（{count} 条）撰写播客口播稿。

资讯列表：
{digest}

输出 JSON 格式：
{{
  "title": "本期播客标题（10字以内，不要日期）",
  "segments": ["第一段", "第二段", "..."]
}}

要求：开头一段欢迎语（介绍今天是哪年哪月哪日的 AI 每日资讯）；
中间每 1-2 条资讯为一段，每段不超过 {seg_max} 个字符；
最后一段为结束语。共 6-10 段。"""


def build_digest(items):
    lines = []
    for i, it in enumerate(items, 1):
        line = f"{i}. [{it['source']}] {it['title']}"
        if it.get("summary"):
            line += f" —— {it['summary'][:150]}"
        lines.append(line)
    return "\n".join(lines)


def call_llm(cfg, api_key, prompt):
    base = cfg["base_url"].rstrip("/")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_script_json(text):
    """兼容模型把 JSON 包在 ```json ... ``` 里的情况"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 输出中未找到 JSON")
    obj = json.loads(text[start:end + 1])
    if not obj.get("segments"):
        raise ValueError("缺少 segments 字段")
    return obj


def fallback_script(news, cfg):
    """LLM 不可用时的兜底稿：直接拼标题摘要，保证当天仍有产出"""
    seg_max = cfg["script_llm"].get("segment_max_chars", 400)
    segments = [f"大家好，欢迎收听今天的 AI 每日资讯，我是你的 AI 主播。以下是要闻速览。"]
    buf = ""
    for it in news:
        line = f"{it['source']}报道：{it['title']}。"
        if buf and len(buf) + len(line) > seg_max:
            segments.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        segments.append(buf)
    segments.append("以上就是今天的全部内容，我们明天见。")
    return {"title": "AI 要闻速览", "segments": segments}


def main(skip_llm=False):
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(DATA / "news.json", encoding="utf-8") as f:
        news = json.load(f)["items"]

    if not news:
        raise RuntimeError("没有抓到任何资讯，无法生成稿件")

    import os
    api_key = os.environ.get("SCRIPT_LLM_API_KEY", "")
    script = None

    if api_key and not skip_llm:
        try:
            prompt = USER_PROMPT_TEMPLATE.format(
                count=len(news),
                digest=build_digest(news),
                seg_max=cfg["script_llm"].get("segment_max_chars", 400),
            )
            raw = call_llm(cfg["script_llm"], api_key, prompt)
            script = parse_script_json(raw)
            print(f"[script] LLM 生成成功：{script['title']}，{len(script['segments'])} 段")
        except Exception as exc:
            print(f"[script] LLM 生成失败，使用兜底模板: {exc}")

    if script is None:
        script = fallback_script(news, cfg)
        print(f"[script] 使用兜底模板：{script['title']}")

    with open(DATA / "script.json", "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
