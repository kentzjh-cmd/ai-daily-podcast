# 🎙️ AI Daily Podcast

每日自动抓取 AI 资讯（中英文混合源）→ LLM 撰写口播稿 → 小米 MiMo TTS 语音合成 → 生成单主播播客，发布到 GitHub Pages。全程由 GitHub Actions 定时驱动，本地无需开机。

架构参考 [AgentSafety-Papers](https://github.com/NY1024/AgentSafety-Papers) 的"Actions 定时任务 + 仓库自更新 + Pages 展示"模式。

## 工作流程

```
RSS 抓取（机器之心/量子位/HN/TechCrunch/arXiv/Reddit）
        │  48h 内条目，各源轮询选 top 12
        ▼
LLM 写稿（任意 OpenAI 兼容 API）
        │  输出 6-10 段口播稿
        ▼
MiMo TTS 合成（mimo-v2.5-tts，分段调用 + 限流重试）
        │
        ▼
ffmpeg 拼接 128kbps MP3 → docs/episodes/YYYY-MM-DD.mp3
        │
        ▼
更新 docs/episodes.json → 生成 index.html 播放器 + feed.xml 播客 RSS
        │
        ▼
commit 回仓库 → 部署 GitHub Pages
```

## 首次配置（只需做一次）

### 1. 配置 Secrets

仓库 Settings → Secrets and variables → Actions → New repository secret：

| Secret 名称 | 说明 |
|---|---|
| `MIMO_API_KEY` | 小米 MiMo 开放平台 API Key（[platform.xiaomimimo.com](https://platform.xiaomimimo.com) 注册创建，`sk-` 开头；mimo-v2.5-tts 当前限时免费） |
| `SCRIPT_LLM_API_KEY` | 口播稿 LLM 的 API Key（DeepSeek / OpenAI 等任意 OpenAI 兼容服务） |

### 2. 修改 config/config.yaml

- `script_llm.base_url` / `model`：改成你的 LLM 服务商（默认 DeepSeek）
- `podcast.voice`：MiMo 预置音色（冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean）
- `podcast.site_base_url`：Pages 部署成功后改成实际地址（影响 RSS 中音频链接）
- `podcast.retention_days`：旧音频保留天数（默认 90，防止仓库无限膨胀）

### 3. 启用 GitHub Pages

仓库 Settings → Pages → Source 选 **GitHub Actions**。

### 4. 首次运行

Actions 页面选 "Daily AI Podcast" → Run workflow 手动触发。之后每天北京时间 8:00 自动运行（GitHub 定时任务可能延迟 15-30 分钟）。

## 本地测试

```bash
pip install -r requirements.txt

# 无需任何 Key，只测抓取 + 兜底模板写稿
python -m podcast.main --no-tts --no-llm

# 完整流程（需要 ffmpeg 在 PATH 中，并设置两个环境变量）
set MIMO_API_KEY=sk-xxx          # Windows CMD
set SCRIPT_LLM_API_KEY=sk-xxx
python -m podcast.main
```

## 注意事项

- **仓库体积**：每日 MP3 直接入库，`retention_days` 清理会自动删除超期音频；如果想无限存历史，可改为存 GitHub Release（单文件上限 2GB）
- **定时任务休眠**：GitHub 仓库 60 天没有任何活动会自动停用 scheduled workflow；本项目每天都会 commit，因此天然不会触发
- **Pages 限额**：站点 ≤ 1GB、月流量软限 100GB，按 90 天保留期计算绰绰有余
- **单文件上限**：git 单文件 100MB；一期 10 分钟播客约 10MB，远低于限制
- **TTS 限流**：MiMo 平台 RPM 100，本项目已内置 429 指数退避重试
- **单源失败容错**：任何一个 RSS 源挂掉不影响当天节目生成；LLM 失败时回退到模板拼接稿

## 目录结构

```
├── .github/workflows/daily-podcast.yml   # 定时任务 + Pages 部署
├── config/
│   ├── config.yaml                       # 播客/LLM/TTS 配置
│   └── sources.yaml                      # 资讯源列表
├── podcast/
│   ├── fetch_news.py                     # RSS 抓取与筛选
│   ├── write_script.py                   # LLM 口播稿生成（含兜底模板）
│   ├── tts.py                            # MiMo TTS 客户端
│   ├── audio.py                          # ffmpeg 拼接/时长
│   ├── cleanup.py                        # 保留期清理
│   ├── build_page.py                     # Pages 站点生成
│   └── main.py                           # 主流程编排
└── docs/                                 # GitHub Pages 站点（自动更新）
    ├── index.html                        # 播放器页面
    ├── feed.xml                          # 播客 RSS
    ├── episodes.json                     # 节目元数据
    └── episodes/                         # 每日 MP3
```
