"""口播稿生成：AIHOT 素材 → 播客口播稿

三阶段：
1) 选题（1 次 LLM）：从 24h 素材中挑 8-14 条，指定板块、头条、标题与开场白
2) 逐条写稿（N 次 LLM，并发）：每条独立成段，套用朗读规范（多音字替代字）
3) 组装：开场 → 头条 → 三大板块 → 结束语 + AIHOT 署名

不保留模板兜底：LLM 不可用即报错，由 GitHub Actions 重试。
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SECTIONS = ["模型与产品", "行业与资本", "研究与实践"]

SECTION_TRANSITION = {
    "模型与产品": "接下来是模型和产品方面的消息。",
    "行业与资本": "再来看行业和资本层面的动态。",
    "研究与实践": "最后说说研究进展和技术实践方面的内容。",
}

LEAD_TRANSITION = "先说今天最值得关注的几件事。"
CLOSING = "以上就是今天的全部内容，我们明天见。"
ATTRIBUTION = "资讯整理自AIhot社区"

SELECT_SYSTEM = (
    "你是一名资深 AI 资讯播客编辑，负责从 24 小时要闻素材中挑选本期内容。"
    "你只使用素材中出现的事实，绝不杜撰。严格按要求的 JSON 格式输出。"
)

SELECT_PROMPT = """下面是过去 24 小时 AI 圈的要闻素材，来自 AIHOT 的每日精选与多源热点聚合。

【头条候选】（多个来源报道同一件事，含跨源聚合摘要）
{leads}

【常规条目】（含 AIHOT 评分与入选理由）
{items}

请完成三件事：
1. 挑选 {min_items}-{max_items} 条作为本期要闻（数量由你判断，宁精不滥；头条候选优先）
2. 其中指定 1-2 条作为「今日头条」（影响面大、多方报道的优先）
3. 为每条指定板块，只能是：模型与产品 / 行业与资本 / 研究与实践
   - 模型与产品：模型发布、模型能力更新、AI 产品与 Agent 应用上线
   - 行业与资本：融资并购、行业政策监管、公司战略、市场格局、算力与硬件
   - 研究与实践：论文研究、评测与基准、实战技巧与方法论

另外写：
- title：本期标题，12 字以内，不要用书名号
- opening：开场白，2 句。第一句问候并点明日期，第二句概括今天 AI 圈整体在关注什么（不要逐条罗列）

硬性要求：只使用素材中的事实，不得杜撰数字、日期、产品名或结论；素材信息不足就少选几条。

输出 JSON：
{{"title":"...","opening":"...","picks":[{{"id":"...","section":"模型与产品","is_lead":true}}]}}"""

ITEM_SYSTEM = (
    "你是一位中文科技播客主播。你把一条资讯写成可直接朗读的口播稿，"
    "口语化、有信息量、不念链接。严格按要求的 JSON 格式输出。"
)

ITEM_PROMPT = """把下面这一条 AI 资讯写成一段可直接朗读的中文口播稿。

【标题】{title}
【来源】{source}
【要点】{summary}
【入选理由】{reason}
【跨源聚合报道】{digest}

写作要求：
1. 2-4 句，独立成段；听众只听音频也能明白发生了什么
2. 第 1 句讲清楚「发生了什么」，后面补充「为什么值得关注 / 会影响谁」
3. 严格限定在上面的素材内，禁止补充素材中没有的数字、日期、产品名、结论
4. 口语化，不要「据悉」「综上所述」这类书面语；不要复述标题原文，用自己的话讲
5. 不得出现链接、编号、括号注释、Markdown 符号、表情
6. 素材只有标题没有要点时，就只讲标题里明确写出的信息，不要展开想象
7. 朗读规范（必须遵守，见下表）：凡出现表中的词，必须改用「替代写法」再输出；表里没有注明的多音字，请在句子中换成没有歧义的表达
8. 英文术语一律直接用中文说法（智能体、显卡、基准测试、混合专家架构…），不要保留英文缩写，也不要加括号注释

【朗读规范表】
{pronunciation}

输出 JSON：{{"text":"这段口播稿"}}"""

SPEECH_SYSTEM = "你是一位播客开场白写手。只输出 JSON。"

OPENING_HINT = ""


# ---------------------------------------------------------------- 工具

def load_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_pronunciation():
    path = ROOT / "config" / "pronunciation.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("rules") or []


def format_pronunciation(rules):
    if not rules:
        return "（无特殊规则）"
    lines = []
    for r in rules:
        term = r.get("term", "")
        rewrite = r.get("rewrite") or ""
        note = r.get("note") or ""
        if rewrite and rewrite != term:
            lines.append(f"- {term} → 改写为「{rewrite}」" + (f"（{note}）" if note else ""))
        elif note:
            lines.append(f"- {term}：{note}")
        else:
            lines.append(f"- {term}：保持原样")
    return "\n".join(lines)


def sanitize(text: str, rules) -> str:
    """清理稿件：去链接/括号噪声，并按朗读规范做确定性替换"""
    t = text or ""
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"（[^）]{0,20}）|\([^)]{0,20}\)", "", t)
    t = re.sub(r"(详见|链接见|原文见|点击查看)[：:，,、\s]*", "", t)
    t = re.sub(r"[，,]\s*[。.]", "。", t)
    t = re.sub(r"[，,、；;]{2,}", "，", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip("，,、；; ")
    for r in rules:
        term = r.get("term") or ""
        rewrite = r.get("rewrite") or ""
        if term and rewrite and rewrite != term and term in t:
            t = t.replace(term, rewrite)
    t = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", t)
    t = t.strip()
    if t and t[-1] not in "。！？!?":
        t += "。"
    return t


def call_llm(cfg, api_key, system, user, retries: int = 3, temperature: float = 0.6):
    base = cfg["base_url"].rstrip("/")
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": cfg["model"],
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": temperature,
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[script] LLM 调用失败（第 {attempt + 1} 次）: {exc}")
    raise RuntimeError(f"LLM 调用失败: {last_err}")


def parse_json_block(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 输出中未找到 JSON")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- 素材

def render_items_for_prompt(items):
    lines = []
    for it in items:
        bits = [f"- id={it['id']}", f"标题：{it['title']}"]
        if it.get("summary"):
            bits.append(f"要点：{it['summary']}")
        if it.get("reason"):
            bits.append(f"入选理由：{it['reason']}")
        if it.get("score"):
            bits.append(f"AIHOT 评分：{it['score']}")
        if it.get("category"):
            bits.append(f"AIHOT 分类：{it['category']}")
        bits.append(f"来源：{it.get('source_short', '')}")
        lines.append("\n".join(bits))
    return "\n".join(lines)


def render_leads_for_prompt(leads):
    lines = []
    for it in leads:
        bits = [f"- id={it['id']}", f"标题：{it['title']}"]
        if it.get("digest"):
            bits.append(f"跨源聚合摘要：{it['digest'][:600]}")
        if it.get("summary"):
            bits.append(f"要点：{it['summary']}")
        if it.get("source_count"):
            bits.append(f"报道来源数：{it['source_count']}")
        bits.append(f"来源：{it.get('source_short', '')}")
        lines.append("\n".join(bits))
    return "\n".join(lines)


# ---------------------------------------------------------------- 阶段

def stage_select(cfg, news, api_key):
    podcast_cfg = cfg["podcast"]
    min_items = podcast_cfg.get("min_items", 8)
    max_items = podcast_cfg.get("max_items", 14)
    prompt = SELECT_PROMPT.format(
        min_items=min_items, max_items=max_items,
        leads=render_leads_for_prompt(news.get("leads") or []),
        items=render_items_for_prompt(news.get("items") or []),
    )
    plan = parse_json_block(call_llm(cfg["script_llm"], api_key, SELECT_SYSTEM, prompt))
    picks = plan.get("picks") or []
    if not picks:
        raise ValueError("选题结果为空")
    print(f"[script] 选题完成：{len(picks)} 条，标题「{plan.get('title', '')}」")
    return plan


def stage_write_items(cfg, news, picks, api_key, rules):
    """逐条并发生成独立口播内容"""
    index = {}
    for it in (news.get("leads") or []) + (news.get("items") or []):
        index[it["id"]] = it

    pronunciation = format_pronunciation(rules)

    def _write(pick):
        it = index.get(pick.get("id"))
        if not it:
            return None
        prompt = ITEM_PROMPT.format(
            title=it.get("title", ""),
            source=it.get("source_short", "AIHOT"),
            summary=it.get("summary") or "（无）",
            reason=it.get("reason") or "（无）",
            digest=it.get("digest") or "（无）",
            pronunciation=pronunciation,
        )
        data = parse_json_block(call_llm(cfg["script_llm"], api_key, ITEM_SYSTEM, prompt,
                                        temperature=0.7))
        text = sanitize(data.get("text") or "", rules)
        if not text:
            raise ValueError(f"条目 {it['id']} 生成内容为空")
        if len(text) > 400:
            text = text[:400].rstrip("，。、 ") + "。"
        return {"id": it["id"], "section": pick.get("section") or it.get("section") or "模型与产品",
                "is_lead": bool(pick.get("is_lead")),
                "title": it.get("title", ""), "text": text}

    workers = int(cfg["podcast"].get("llm_workers", 4))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_write, p) for p in picks]
        for fut in futures:
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[script] 单条写稿失败，跳过: {exc}")
                res = None
            if res:
                results.append(res)
    if len(results) < 3:
        raise RuntimeError(f"有效口播内容不足（{len(results)} 条），放弃本期")
    print(f"[script] 逐条写稿完成：{len(results)}/{len(picks)} 条")
    return results


def stage_assemble(plan, results):
    """按板块组装成最终分段口播稿"""
    segments = []
    opening = sanitize(plan.get("opening") or "", [])
    if opening:
        segments.append(opening)

    leads = [r for r in results if r.get("is_lead")]
    others = [r for r in results if not r.get("is_lead")]
    if leads:
        segments.append(LEAD_TRANSITION)
        for r in leads:
            segments.append(r["text"])

    for section in SECTIONS:
        group = [r for r in others if r.get("section") == section]
        if not group:
            continue
        segments.append(SECTION_TRANSITION[section])
        for r in group:
            segments.append(r["text"])

    segments.append(CLOSING)
    segments.append(ATTRIBUTION)
    return segments


# ---------------------------------------------------------------- 入口

def main(skip_llm=False):
    cfg = load_config()
    with open(DATA / "news.json", encoding="utf-8") as f:
        news = json.load(f)
    items = news.get("items") or []
    leads = news.get("leads") or []
    if not items and not leads:
        raise RuntimeError("没有抓到任何资讯，无法编排内容")

    api_key = os.environ.get("SCRIPT_LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("未设置 SCRIPT_LLM_API_KEY 环境变量")
    if skip_llm:
        raise RuntimeError("已按要求移除兜底策略，无法跳过 LLM")

    rules = load_pronunciation()
    plan = stage_select(cfg, news, api_key)
    picks = plan.get("picks") or []
    results = stage_write_items(cfg, news, picks, api_key, rules)
    segments = stage_assemble(plan, results)

    n_chars = sum(len(s) for s in segments)
    print(f"[script] 口播稿完成：{len(segments)} 段，{n_chars} 字")

    with open(DATA / "script.json", "w", encoding="utf-8") as f:
        json.dump({"title": plan.get("title") or "AI 每日资讯",
                   "segments": segments,
                   "picks": [{"id": r["id"], "section": r["section"], "is_lead": r["is_lead"]}
                             for r in results]},
                  f, ensure_ascii=False, indent=2)

    # 素材清单（调试/审计用）
    with open(DATA / "picks.json", "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                   "title": plan.get("title"), "results": results},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
