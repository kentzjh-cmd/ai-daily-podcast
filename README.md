# 🎙️ AI Daily Podcast

每日自动抓取 [AIHOT](https://aihot.news) 汇聚好的 24 小时 AI 要闻 → LLM 撰写口播稿 → 小米 MiMo TTS 语音合成 → 生成单主播播客音频制品。全程由 GitHub Actions 定时驱动，产物以 Actions artifact 形式产出（不再部署 GitHub Pages），本地无需开机。

## 工作流程

```
AIHOT 公开 API 抓取（唯一数据源，无需 API Key）
        │  /api/v1/items        24h 精选条目（评分 / 中文摘要 / 入选理由）
        │  /api/v1/hot-topics   多源聚合热点（同一事件多家报道）
        │  /api/v1/stories/{id} 事件的跨源聚合 digest（头条素材）
        ▼
LLM 选题（1 次调用）
        │  从素材中挑 8-14 条（数量由模型判断），指定板块与今日头条
        │  同时给出本期标题与开场白
        ▼
LLM 逐条写稿（N 次调用，并发）
        │  每条独立成段：3-5 句口语化内容
        │  注入 config/style.yaml（去 AI 味规则）+ config/pronunciation.yaml（多音字替代字）
        ▼
LLM 逐段风格复核（N 次调用，并发）
        │  按风格规则把机器味改回人话，只改说法不改事实
        ▼
组装：开场 → 今日头条 → 模型与产品 / 行业与资本 / 研究与实践 → 结束语 + 署名（资讯整理自AIhot社区）
        ▼
MiMo TTS 合成（mimo-v2.5-tts）
        │  风格指令用 user 消息传（控制语气与停顿）
        │  wav 无损合成 → 裁掉每段首尾静音 → 段间插入可控停顿
        ▼
ffmpeg 拼接 + 轻微提速 → 128kbps MP3 → data/episodes/YYYY-MM-DD.mp3
        │
        ▼
GitHub Actions 上传 artifact（音频 + 口播稿/选题清单，保留 30 天）
```

## 板块结构

| 板块 | 内容 |
|---|---|
| 今日头条 | 1-2 条多源聚合的重点事件（附跨源摘要） |
| 模型与产品 | 模型发布、能力更新、AI 产品与 Agent 应用上线 |
| 行业与资本 | 融资并购、政策监管、公司战略、市场格局、算力硬件 |
| 研究与实践 | 论文研究、评测基准、实战技巧与方法论 |

## 首次配置（只需做一次）

### 1. 配置 Secrets

仓库 Settings → Secrets and variables → Actions → New repository secret：

| Secret 名称 | 说明 |
|---|---|
| `MIMO_API_KEY` | 小米 MiMo 开放平台 API Key（[platform.xiaomimimo.com](https://platform.xiaomimimo.com) 注册创建，`sk-` 开头；mimo-v2.5-tts 当前限时免费） |
| `SCRIPT_LLM_API_KEY` | 口播稿 LLM 的 API Key（当前配置为 MiniMax 平台，[api.minimax.cn](https://api.minimax.cn/v1)，模型 `MiniMax-M3.1-Flash`；也可换成任意 OpenAI 兼容服务） |

### 2. 修改 config/config.yaml

- `script_llm.base_url` / `model`：口播稿 LLM，当前为 MiniMax（`https://api.minimax.cn/v1` + `MiniMax-M3.1-Flash`）
- `aihot.*`：AIHOT 数据源参数（`base_url` / `window`=24h|7d / `limit` / `user_agent`，无需 API Key）
- `podcast.min_items` / `max_items`：每期条数区间（默认 8-14），实际数量由写稿模型判断
- `podcast.llm_workers`：逐条写稿的并发数（默认 4）
- `podcast.voice`：MiMo 预置音色（冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean）
- `config/style.yaml`：口播风格规则（去 AI 味：禁总结句/对比句式/升华句/宣传腔/黑话、句子完整不写成清单、数字阿拉伯化），同时注入写稿提示词与风格复核
- `config/pronunciation.yaml`：朗读规范（多音字与英文术语 → 替代写法），写稿提示词与落盘前都会应用
- `podcast.style_review`：是否做逐段风格复核（默认开，多一轮 LLM 调用）
- `tts.style`：传给 MiMo TTS 的风格指令（语气/节奏），`tts.speed`：整体提速倍率（atempo）

### 3. 首次运行

Actions 页面选 "Daily AI Podcast" → Run workflow 手动触发。之后每天北京时间 8:00 自动运行（GitHub 定时任务可能延迟 15-30 分钟）。跑完后在本次运行的 Artifacts 区域下载 `ai-daily-podcast-<run_number>`（音频）与 `ai-daily-podcast-script-<run_number>`（口播稿与选题清单）。

## 本地测试

```bash
pip install -r requirements.txt

# 只测抓取（不需要任何 Key）
python -m podcast.fetch_news

# 完整流程（需要 ffmpeg 在 PATH 中，并设置两个环境变量）
set MIMO_API_KEY=sk-xxx          # Windows CMD
set SCRIPT_LLM_API_KEY=sk-xxx
python -m podcast.main
```

## 注意事项

- **仓库体积**：音频不入库（`.gitignore` 忽略 `data/`），仓库保持极小；历史音频靠 Actions artifact 保留 30 天
- **定时任务休眠**：GitHub 仓库 60 天没有任何活动会自动停用 scheduled workflow；本项目每天都会产生一次运行，可偶尔手动触发一次以保活
- **产物获取**：不部署 Pages，音频只能从 Actions artifact 下载（单文件 100MB 限制不涉及）
- **TTS 限流**：MiMo 平台 RPM 100，本项目已内置 429 指数退避重试
- **音频处理**：TTS 用 wav 无损合成，拼接前裁掉每段自带的首尾静音，段间停顿固定 0.12~0.4s；「板块过渡句 + 首条内容」合并成一次合成请求，避免一顿一顿
- **参考的社区实现**：MiMo TTS 的风格指令与 wav 拼接方案参考了开源客户端 [dqsq2e2/mimo-tts](https://github.com/dqsq2e2/mimo-tts)（有声书工具）、[bmbxwbh/mimo-tts-web](https://github.com/bmbxwbh/mimo-tts-web)；去 AI 味的风格规则参考 [deslop-zh](https://github.com/ninehills/public-skills/blob/main/deslop-zh/SKILL.md)
- **不保留模板兜底**：LLM 写稿失败会直接报错终止（单条失败重试 3 次，仍失败则跳过该条；有效内容不足 3 条直接放弃），由 GitHub Actions 的重试步骤兜住
- **数据来源与署名**：唯一数据源为 [AIHOT](https://aihot.news)（免费公开只读 API，无需 Key）。按其使用条款，口播结尾固定署名「资讯整理自AIhot社区」；对外商用/再分发需先取得授权

## 目录结构

```
├── .github/workflows/daily-podcast.yml   # 定时任务 + 音频制品上传
├── config/
│   ├── config.yaml                       # 播客/LLM/TTS 配置
│   └── pronunciation.yaml                # 朗读规范（多音字 → 替代写法）
├── podcast/
│   ├── fetch_news.py                     # 抓取入口（调用 AIHOT）
│   ├── fetch_aihot.py                    # AIHOT 公开 API 客户端与素材组装
│   ├── write_script.py                   # 选题 + 逐条写稿 + 组装
│   ├── tts.py                            # MiMo TTS 客户端
│   ├── audio.py                          # ffmpeg 拼接/时长
│   └── main.py                           # 主流程编排
└── data/                                 # 中间产物（不入库）
    ├── news.json                         # AIHOT 素材
    ├── script.json                       # 口播稿分段
    ├── picks.json                        # 本期选题清单
    └── episodes/YYYY-MM-DD.mp3           # 音频制品
```
