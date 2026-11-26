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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SECTIONS = ["模型与产品", "行业与资本", "研究与实践"]

SECTION_TRANSITION = {
    "模型与产品": "模型和产品这边，有几条新消息。",
    "行业与资本": "接着看行业和资本这一块。",
    "研究与实践": "最后是研究和技术实践的部分。",
}

LEAD_TRANSITION = "先说今天最值得关注的。"
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
- opening：开场白，2 句。第一句问候并写明今天是 {date}（用这个日期，不要自己换算），第二句概括今天 AI 圈整体在关注什么（不要逐条罗列）

硬性要求：只使用素材中的事实，不得杜撰数字、日期、产品名或结论；素材信息不足就少选几条。
公司名、产品名保留英文原名（Anthropic、OpenAI、Google、Meta、xAI、Character.AI、Fireworks 等），不要音译也不要意译成“人工智能企业”之类的说法。

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
1. 3-5 句，独立成段；听众只听音频也能明白发生了什么（普通条目 200-450 字，头条不超过 700 字）
1b. 句子要完整自然，像人在讲一件事；不要写成关键词清单或一串短句；关键信息（谁、什么时候、做了什么、关键数字）要留住
2. 第 1 句讲清楚「发生了什么」，后面补充「为什么值得关注 / 会影响谁」
3. 严格限定在上面的素材内，禁止补充素材中没有的数字、日期、产品名、结论
4. 口语化，不要「据悉」「综上所述」这类书面语；不要复述标题原文，用自己的话讲
5. 不得出现链接、编号、括号注释、Markdown 符号、表情
6. 素材只有标题没有要点时，就只讲标题里明确写出的信息，不要展开想象
7. 朗读规范（必须遵守，见下表）：凡出现表中的词，必须改用「替代写法」再输出；表里没有注明的多音字，请在句子中换成没有歧义的表达
8. 英文术语一律直接用中文说法（智能体、显卡、基准测试、混合专家架构…），不要保留英文缩写，也不要加括号注释
9. 公司名、产品名保留英文原名（Anthropic、OpenAI、Google、Meta、xAI、Character.AI、Fireworks、Claude、GPT 等），不要音译、也不要意译

【风格规则】（必须遵守，这是去 AI 味的关键）
{style}

【朗读规范表】
{pronunciation}

输出 JSON：{{"text":"这段口播稿"}}"""

SPEECH_SYSTEM = "你是一位播客开场白写手。只输出 JSON。"

OPENING_HINT = ""


# ---------------------------------------------------------------- 工具

REVIEW_SYSTEM = (
    "你是一位中文播客文字编辑，擅长把机器写出来的稿子改成真人说话的样子。"
    "只输出 JSON，不解释。"
)

REVIEW_PROMPT = """下面是一段 AI 播客口播稿，请按风格规则改写得更像人在说话。

【原文】
{text}

【风格规则】（逐条对照）
{style}

改写要求：
1. 事实、数字、日期、产品名、结论一个都不能改，只能改说法和句子结构
2. 把长句拆成短句，每句 15 到 30 个字；但必须保持完整的句子，不能拆成一串只有几个字的短句（那样像念 PPT）
2b. 不能因为改写而丢掉关键信息：谁做的、什么时候、做了什么、关键数字
3. 去掉总结句、升华句、对比句式、宣传腔、模糊归因、书面语填充词
4. 数字改成中文读法，术语按朗读规范表处理
5. 不要加素材里没有的信息，也不要加评论和感想
6. 改写后整体长度不超过原文的 1.2 倍
7. 如果原文已经符合规则，就原样返回

输出 JSON：{{"text":"改写后的口播稿"}}"""


def load_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_pronunciation():
    path = ROOT / "config" / "pronunciation.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("rules") or []


def load_style_rules():
    path = ROOT / "config" / "style.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("rules") or []


def format_style(rules):
    if not rules:
        return "（无额外规则）"
    return "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))


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


CN_NUM_CHARS = "零〇○一二三四五六七八九十百千两"   # 数字部分
CN_BIG_UNITS = "万亿"


def normalize_cn_numbers(text: str) -> str:
    """把「百分之九十五」「两千一百亿」「二零二六年」这类中文数字改成阿拉伯数字。

    只处理带明确量词的组合，避免误伤「一起」「一下」这类词。
    """
    t = text

    # 完整中文日期：二零二六年十一月二十六日 → 2026年11月26日
    def full_date(m):
        return f"{cn_to_int(m.group(1))}年{cn_to_int(m.group(2))}月{cn_to_int(m.group(3))}日"

    t = CN_DATE_RE.sub(full_date, t)

    # 百分之九十五 → 95%
    def pct(m):
        return f"{cn_to_int(m.group(1))}%"

    t = re.sub(r"百分之([" + CN_NUM_CHARS + r"]+)", pct, t)

    # 两千一百亿 → 2100亿
    def unit_num(m):
        return f"{cn_to_int(m.group(1))}{m.group(2)}"

    t = re.sub(r"([" + CN_NUM_CHARS + r"]+)([" + CN_BIG_UNITS + r"])", unit_num, t)

    # 二零二六年 → 2026年
    def year(m):
        return f"{cn_to_int(m.group(1))}年"

    t = re.sub(r"([零〇○一二三四五六七八九]{4})年", year, t)

    # 一百八十倍 → 180倍；五千四百一十四家 → 5414家（两个以上字才转，避免误伤「一起」「一下」）
    def counted(m):
        return f"{cn_to_int(m.group(1))}{m.group(0)[len(m.group(1)):] or ''}"

    t = re.sub(r"([" + CN_NUM_CHARS + r"]{2,})((?:倍|个|家|次|行|颗|条|人|台|美元|元|分钟|小时|天|周))",
               counted, t)
    return t


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
        if not term or not rewrite or rewrite == term:
            continue
        if re.fullmatch(r"[A-Za-z0-9 ._\-]+", term):
            # 英文术语按词边界替换，避免 MAGI-2 被 AGI 规则误伤
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")
            t = pat.sub(rewrite, t)
        else:
            t = t.replace(term, rewrite)
    t = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", t)
    t = normalize_cn_numbers(t)
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

def stage_select(cfg, news, api_key, date_cn: str):
    podcast_cfg = cfg["podcast"]
    min_items = podcast_cfg.get("min_items", 8)
    max_items = podcast_cfg.get("max_items", 14)
    prompt = SELECT_PROMPT.format(
        min_items=min_items, max_items=max_items, date=date_cn,
        leads=render_leads_for_prompt(news.get("leads") or []),
        items=render_items_for_prompt(news.get("items") or []),
    )
    plan = parse_json_block(call_llm(cfg["script_llm"], api_key, SELECT_SYSTEM, prompt))
    picks = plan.get("picks") or []
    if not picks:
        raise ValueError("选题结果为空")
    print(f"[script] 选题完成：{len(picks)} 条，标题「{plan.get('title', '')}」")
    return plan


def stage_write_items(cfg, news, picks, api_key, rules, style_text):
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
            style=style_text,
        )
        data = parse_json_block(call_llm(cfg["script_llm"], api_key, ITEM_SYSTEM, prompt,
                                        temperature=0.7))
        text = sanitize(data.get("text") or "", rules)
        if not text:
            raise ValueError(f"条目 {it['id']} 生成内容为空")
        limit = 600 if pick.get("is_lead") else 480
        text = truncate_at_sentence(text, limit)
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


DATE_PATTERN = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日")


CN_DIGITS = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    """中文数字转阿拉伯数字，支持 十/百/千/万/亿（两千一百亿、十一、二十六、二零二六）"""
    if not text:
        return 0
    if not any(u in text for u in "十百千万亿"):
        # 纯数字串，按位拼接：二零二六 → 2026
        val = 0
        for ch in text:
            val = val * 10 + CN_DIGITS.get(ch, 0)
        return val
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in CN_DIGITS:
            number = CN_DIGITS[ch]
        elif ch in units:
            u = units[ch]
            if u >= 10000:
                total += (section + number) * u
                section = 0
            else:
                section += (number if number else 1) * u
            number = 0
    return total + section + number


CN_DATE_RE = re.compile(
    r"([零〇○一二三四五六七八九十两]{2,4})年"
    r"([零〇○一二三四五六七八九十两]{1,3})月"
    r"([零〇○一二三四五六七八九十两]{1,3})日"
)


def _cn_date_to_digits(text: str) -> str:
    """把「二零二六年十一月二十六日」这类中文数字日期转成阿拉伯数字，便于比对"""

    def repl(m):
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{cn_to_int(y)}年{cn_to_int(mo)}月{cn_to_int(d)}日"

    return CN_DATE_RE.sub(repl, text)


def fix_opening_date(opening: str, date_cn: str) -> str:
    """开场白里的日期必须与当天一致（模型偶尔会写错日期）"""
    if not opening or not date_cn:
        return opening
    target = re.sub(r"\s+", "", date_cn)
    norm = re.sub(r"\s+", "", _cn_date_to_digits(opening))
    if target in norm:
        return opening
    fixed = DATE_PATTERN.sub(date_cn, opening)
    norm2 = re.sub(r"\s+", "", _cn_date_to_digits(fixed))
    if target not in norm2:
        fixed = f"今天是{date_cn}。" + fixed
    print(f"[script] 开场白日期已校正为 {date_cn}")
    return fixed


def truncate_at_sentence(text: str, limit: int) -> str:
    """超长时在最后一个句号处截断（不截半句），并补上句号"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"), head.rfind("."))
    if cut > limit * 0.4:
        head = head[:cut + 1]
    else:
        head = head.rstrip("，。、 ")
    if head and head[-1] not in "。！？!?":
        head += "。"
    return head


def stage_review(cfg, results, api_key, rules, style_text):
    """逐段风格复核：把机器味重写成口语（只改说法，不改事实）"""
    workers = int(cfg["podcast"].get("llm_workers", 4))
    changed = 0

    def _review(item):
        prompt = REVIEW_PROMPT.format(text=item["text"], style=style_text)
        data = parse_json_block(call_llm(cfg["script_llm"], api_key, REVIEW_SYSTEM, prompt,
                                        temperature=0.5))
        new_text = sanitize(data.get("text") or "", rules)
        if not new_text or len(new_text) > len(item["text"]) * 1.25:
            return item
        return {**item, "text": new_text}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_review, r): r for r in results}
        for fut in futures:
            item = futures[fut]
            try:
                new_item = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[script] 风格复核失败，保留原文: {exc}")
                continue
            if new_item["text"] != item["text"]:
                changed += 1
                item["text"] = new_item["text"]
    print(f"[script] 风格复核：{changed}/{len(results)} 段被改写")
    return results


def stage_assemble(plan, results, date_cn: str = ""):
    """按板块组装成最终分段口播稿"""
    segments = []
    kinds = []
    opening = sanitize(plan.get("opening") or "", [])
    opening = fix_opening_date(opening, date_cn)
    if opening:
        segments.append(opening)
        kinds.append("opening")

    leads = [r for r in results if r.get("is_lead")]
    others = [r for r in results if not r.get("is_lead")]
    if leads:
        segments.append(LEAD_TRANSITION)
        kinds.append("transition")
        for r in leads:
            segments.append(r["text"])
            kinds.append("lead")

    for section in SECTIONS:
        group = [r for r in others if r.get("section") == section]
        if not group:
            continue
        segments.append(SECTION_TRANSITION[section])
        kinds.append("transition")
        for r in group:
            segments.append(r["text"])
            kinds.append("item")

    segments.append(CLOSING)
    kinds.append("closing")
    segments.append(ATTRIBUTION)
    kinds.append("attribution")
    return segments, kinds


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
    style_text = format_style(load_style_rules())
    # 播客按北京时间口径出稿（Action 在北京时间 8:00 跑）
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    today_cn = f"{now_cn.year}年{now_cn.month}月{now_cn.day}日"
    plan = stage_select(cfg, news, api_key, today_cn)
    picks = plan.get("picks") or []
    results = stage_write_items(cfg, news, picks, api_key, rules, style_text)
    if cfg["podcast"].get("style_review", True):
        results = stage_review(cfg, results, api_key, rules, style_text)
        if plan.get("opening"):
            reviewed = stage_review(cfg, [{"id": "opening", "text": plan["opening"]}],
                                    api_key, rules, style_text)
            plan["opening"] = reviewed[0]["text"]
    segments, kinds = stage_assemble(plan, results, today_cn)

    n_chars = sum(len(s) for s in segments)
    print(f"[script] 口播稿完成：{len(segments)} 段，{n_chars} 字")

    with open(DATA / "script.json", "w", encoding="utf-8") as f:
        json.dump({"title": plan.get("title") or "AI 每日资讯",
                   "segments": segments,
                   "kinds": kinds,
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
