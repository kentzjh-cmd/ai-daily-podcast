"""内容编排核心：
1) LLM 将资讯分类到四大栏目（模型厂商/Agent与应用/硬件与行业/模型排行）并生成结构化日报 JSON
2) 渲染 Markdown 日报（摘要 + 重点信息 + 原文链接）
3) 第二次 LLM 调用按 5-10 分钟时长预算生成 TTS 口播稿
LLM 不可用时有完整的关键词分类 + 模板兜底
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CATEGORIES = ["模型厂商", "Agent与应用", "硬件与行业", "模型排行"]

CLASSIFY_SYSTEM = (
    "你是一名资深的 AI 领域新闻编辑，负责为每日 AI 资讯播客编排内容。"
    "你会将原始资讯准确分类到指定栏目，并为每条资讯提炼摘要与要点。"
    "严格按要求的 JSON 格式输出，不要输出任何多余内容。"
)

CLASSIFY_PROMPT = """请将以下今日 AI 资讯（共 {count} 条）分类编排。

{digest}

分类到以下四个栏目（每条资讯只归入一个栏目，价值低或重复的可以舍弃）：
- 模型厂商：新模型发布、厂商重大动向、重要融资与商业化
- Agent与应用：Agent 产品、AI 应用、开发者工具、实际落地案例
- 硬件与行业：芯片、算力、数据中心、行业政策、市场规模与格局
- 模型排行：评测榜单成绩、benchmark 表现、竞技场排名变化

每条资讯输出：
- summary：1-2 句话摘要
- points：2-3 条重点信息（每条一句话）
- url：原文链接（来自资讯原始数据，不要编造）
- source：来源名称

输出 JSON：
{{
  "title": "本期标题（10字以内）",
  "highlights": ["今日最重要 3 条的一句话摘要"],
  "sections": [
    {{"name": "模型厂商", "items": [{{"title": "...", "summary": "...", "points": ["..."], "url": "...", "source": "..."}}]}}
  ],
  "discarded_count": 被舍弃的低价值条数
}}

sections 只包含有内容的栏目，按重要程度排序。"""

SCRIPT_SYSTEM = (
    "你是一名专业的科技播客主播，风格专业、简洁、有活力。"
    "你会根据编辑好的栏目要点撰写中文口播稿。要求口语化短句、逻辑清晰，"
    "严格按要求的 JSON 格式输出，不要输出多余内容。"
)

SCRIPT_PROMPT = """基于以下栏目要点撰写播客口播稿（单主播、中文）：

{outline}

时长要求：全文总字数控制在 {min_chars}-{max_chars} 字之间（对应约 {min_min}-{max_min} 分钟，中文播报语速约 {cpm} 字/分钟）。

写作要求：
1. 开场一句欢迎语（说明今天日期，是 AI 每日资讯播客）
2. 按栏目顺序播报，每个栏目用一句自然的过渡语衔接（如"接下来看 Agent 和应用方面的动态"）
3. 每条资讯用 1-3 句话讲清楚核心内容，英文内容必须转成中文播报
4. 结尾固定语："以上就是今天的全部内容，我们明天见。"
5. 输出 JSON：{{"segments": ["第一段", "第二段", "..."]}}，每段不超过 {seg_max} 字，共 5-9 段"""

# 关键词兜底分类（LLM 不可用时）
KEYWORD_RULES = {
    "模型厂商": ["openai", "anthropic", "gemini", "claude", "gpt", "deepseek", "qwen", "通义", "千问",
                 "kimi", "minimax", "豆包", "文心", "混元", "发布", "模型", "llm", "多模态", "训练"],
    "Agent与应用": ["agent", "智能体", "copilot", "cursor", "claude code", "应用", "app", "工具",
                    "编程", "manus", "自动化", "workflow", "助手"],
    "硬件与行业": ["芯片", "gpu", "nvidia", "英伟达", "算力", "数据中心", "asic", "融资",
                   "估值", "投资", "市场", "政策", "监管", "行业", "就业", "垄断"],
    "模型排行": ["榜单", "排行", "benchmark", "评测", "竞技场", "leaderboard", "arena", "swe-bench",
                 "mmlu", "跑分", "夺冠", "登顶", "第一"],
}


def classify_by_keywords(item):
    text = f"{item['title'].lower()} {(item.get('summary') or '').lower()}"
    best_cat, best_hits = None, 0
    for cat, kws in KEYWORD_RULES.items():
        hits = sum(1 for kw in kws if kw in text)
        if hits > best_hits:
            best_cat, best_hits = cat, hits
    return best_cat or "硬件与行业"  # 无命中时归入行业


def build_digest(items):
    lines = []
    for i, it in enumerate(items, 1):
        line = f"{i}. [{it['source']}] {it['title']}"
        if it.get("summary"):
            line += f" —— {it['summary'][:120]}"
        if it.get("link"):
            line += f" | 链接: {it['link']}"
        lines.append(line)
    return "\n".join(lines)


def call_llm(cfg, api_key, system, user):
    base = cfg["base_url"].rstrip("/")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.6,
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_json_block(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 输出中未找到 JSON")
    return json.loads(text[start:end + 1])


def render_report_md(title, highlights, sections, date_str):
    md = [f"# AI 每日资讯 · {date_str}", ""]
    if title:
        md.append(f"**本期主题：{title}**")
        md.append("")
    if highlights:
        md.append("## 📌 今日重点")
        md.append("")
        for h in highlights:
            md.append(f"- {h}")
        md.append("")
    for sec in sections:
        md.append(f"## {sec['name']}")
        md.append("")
        for i, it in enumerate(sec.get("items") or [], 1):
            md.append(f"### {i}. {it['title']}")
            md.append("")
            if it.get("source"):
                md.append(f"> 来源：{it['source']}")
                md.append("")
            if it.get("summary"):
                md.append(f"**摘要**：{it['summary']}")
                md.append("")
            points = it.get("points") or []
            if points:
                md.append("**重点信息**：")
                md.append("")
                for p in points:
                    md.append(f"- {p}")
                md.append("")
            if it.get("url"):
                md.append(f"**原文链接**：{it['url']}")
                md.append("")
            md.append("---")
            md.append("")
    return "\n".join(md)


def classify_fallback(news):
    sections = {c: [] for c in CATEGORIES}
    for it in news:
        sections[classify_by_keywords(it)].append({
            "title": it["title"],
            "summary": (it.get("summary") or "")[:150],
            "points": [(it.get("summary") or "")[:100]] if it.get("summary") else [],
            "url": it.get("link", ""),
            "source": it["source"],
        })
    out = [{"name": c, "items": items} for c, items in sections.items() if items]
    return {"title": "AI 要闻速览",
            "highlights": [it["title"] for it in news[:3]],
            "sections": out}


def enforce_char_budget(segments, max_chars):
    """超出预算时丢弃中间段（保留开头与结束语），保证口播不超过时长上限"""
    while segments and sum(len(s) for s in segments) > max_chars and len(segments) > 2:
        del segments[-2]  # 倒数第二段开始丢，最后一段是结束语
    return segments


def llm_pipeline(cfg, news, api_key):
    podcast_cfg = cfg["podcast"]
    script_cfg = cfg["script_llm"]
    cpm = podcast_cfg.get("chars_per_minute", 240)
    min_min = podcast_cfg.get("duration_min_minutes", 5)
    max_min = podcast_cfg.get("duration_max_minutes", 10)
    min_chars, max_chars = min_min * cpm, max_min * cpm
    seg_max = script_cfg.get("segment_max_chars", 400)

    # 调用 1：分类 + 结构化日报
    plan = parse_json_block(call_llm(
        script_cfg, api_key, CLASSIFY_SYSTEM,
        CLASSIFY_PROMPT.format(count=len(news), digest=build_digest(news)),
    ))
    sections = [s for s in plan.get("sections", []) if s.get("items")]
    if not sections:
        raise ValueError("LLM 分类结果为空")

    # 调用 2：口播稿
    outline = "\n".join(
        f"【{s['name']}】" + "；".join(
            f"{it['title']}" + (f"：{it['summary']}" if it.get('summary') else "")
            for it in s["items"]
        ) for s in sections
    )
    script = parse_json_block(call_llm(
        script_cfg, api_key, SCRIPT_SYSTEM,
        SCRIPT_PROMPT.format(outline=outline, min_chars=min_chars, max_chars=max_chars,
                             min_min=min_min, max_min=max_min, cpm=cpm, seg_max=seg_max),
    ))
    segments = [s.strip() for s in script.get("segments") if s and s.strip()]
    if not segments:
        raise ValueError("LLM 口播稿为空")
    segments = enforce_char_budget(segments, max_chars)

    return {"title": plan.get("title") or "AI 每日资讯",
            "highlights": plan.get("highlights") or [],
            "sections": sections, "segments": segments}


def fallback_pipeline(cfg, news):
    podcast_cfg = cfg["podcast"]
    cpm = podcast_cfg.get("chars_per_minute", 240)
    max_chars = podcast_cfg.get("duration_max_minutes", 10) * cpm
    seg_max = cfg["script_llm"].get("segment_max_chars", 400)

    plan = classify_fallback(news)
    segments = ["大家好，欢迎收听今天的 AI 每日资讯，我是你的 AI 主播。今天的主要内容如下："]
    transitions = {
        "模型厂商": "首先来看各大模型厂商的最新动向。",
        "Agent与应用": "接下来关注 Agent 产品和 AI 应用方面的消息。",
        "硬件与行业": "再来看硬件和行业层面的动态。",
        "模型排行": "最后看看模型评测榜单上的新变化。",
    }
    buf = ""
    for sec in plan["sections"]:
        segments.append(transitions.get(sec["name"], ""))
        for it in sec["items"]:
            line = f"{it['source']}报道：{it['title']}。"
            if it.get("summary"):
                line += f"{it['summary']}。"
            if len(buf) + len(line) > seg_max:
                if buf:
                    segments.append(buf)
                buf = line
            else:
                buf += line
            if sum(len(s) for s in segments) + len(buf) > max_chars:
                break
        if sum(len(s) for s in segments) > max_chars:
            break
    if buf:
        segments.append(buf)
    segments = [s for s in segments if s]
    segments.append("以上就是今天的全部内容，我们明天见。")
    return {"title": plan["title"], "highlights": plan["highlights"],
            "sections": plan["sections"], "segments": enforce_char_budget(segments, max_chars)}


def main(skip_llm=False):
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(DATA / "news.json", encoding="utf-8") as f:
        news = json.load(f)["items"]
    if not news:
        raise RuntimeError("没有抓到任何资讯，无法编排内容")

    api_key = os.environ.get("SCRIPT_LLM_API_KEY", "")
    result = None
    if api_key and not skip_llm:
        try:
            result = llm_pipeline(cfg, news, api_key)
        except Exception as exc:
            print(f"[script] LLM 编排失败，使用关键词兜底: {exc}")

    if result is None:
        result = fallback_pipeline(cfg, news)

    n_chars = sum(len(s) for s in result["segments"])
    est_min = n_chars / cfg["podcast"].get("chars_per_minute", 240)
    print(f"[script] 编排完成：{result['title']}，{len(result['sections'])} 个栏目，"
          f"口播 {n_chars} 字 ≈ {est_min:.1f} 分钟，{len(result['segments'])} 段")

    # 渲染 Markdown 日报
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md = render_report_md(result["title"], result["highlights"], result["sections"], date_str)
    (DATA / "report.md").write_text(md, encoding="utf-8")

    # 口播稿（供 TTS 使用）
    with open(DATA / "script.json", "w", encoding="utf-8") as f:
        json.dump({"title": result["title"], "segments": result["segments"]}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
