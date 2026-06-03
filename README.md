# MaoMeme

猫 meme 垂直方向的爆款结构迁移引擎。用户输入社会现实主题后，系统生成 3 个剧本候选，用户选择后生成分镜时间线，并匹配本地猫动画、背景图、字幕和原素材声音，最后通过渲染任务输出视频。

## 方案概览

当前方案采用前后端分离：

- 前端 `frontend/`：React/Vite 工作台，负责主题输入、候选剧本展示、分镜预览、自然语言调整、渲染任务轮询和视频播放。
- 后端 `backend/`：FastAPI 服务，负责剧本候选生成、分镜规划、素材匹配、自然语言修订和渲染 job 队列。
- 素材与文本库：`assets/` 放猫动画和背景图，`data/text-materials/` 放社会现实主题素材，`data/assets-index.json` 是素材索引。
- 预制场景：`data/preset-scenes/social-scenes.json` 维护招聘会、会议室、自习室、出租屋、家庭预算桌、通勤站台等高频社会现实背景语义。
- 渲染：默认使用 FFmpeg + Pillow 字幕图，保留猫动画原素材声音；猫素材可先预抠绿生成本地透明缓存，HyperFrames 作为后续 Agent 友好的 HTML 包装增强。
- Agent 策略：编剧 Agent 默认走豆包异步流式生成候选；选择剧本后，ShotPlannerAgent 会对每个分镜并发调用白名单工具，决定猫素材、背景、裁剪、动态贴图、字幕包装和 HyperFrames preset，CriticAgent 最多修订 2 轮，AssemblerAgent 做全片去重和一致性检查。
- 并行策略：候选剧本默认用一次协调式流式请求生成 3 个差异化方案，避免三路请求重复；批量非流式接口可按角度并发生成。分镜 Agent、素材工具、字幕/overlay 生成、视频片段渲染会并行执行，再按时间线顺序合成和展示。

正常前端请求默认走真实 Agent。只有显式传 `use_doubao=false`，或访问前端调试参数 `?agent=false`，才会走本地预设，主要用于测试。无 `ARK_API_KEY` 或真实 Agent 超时时会回退本地 fallback，保证演示闭环可跑；配置 `backend/.env` 后可走豆包。真实 `.env` 不要提交，也不要打印或复制其中内容。

模型速度可以通过环境变量切换：`ARK_MODEL` 放正式/pro 模型，`ARK_LITE_MODEL` 放快测/lite 模型，测试时设置 `ARK_MODEL_MODE=lite` 即可；未配置 lite 时会自动继续使用 `ARK_MODEL`。目前 Ark SDK 和 OpenAI Agents SDK 都可以用 Ark API key/base URL 跑工具调用；本项目默认 `AGENT_RUNTIME=auto`，优先 Ark SDK，OpenAI Agents SDK 作为可切换后备。

## 完整 Workflow

1. 用户在前端输入主题，例如“大学生工作难找，投简历像进黑洞”，并选择短版、30 秒或 1 分钟。
2. 前端调用 `/api/maomeme/candidates-stream`，默认走 Doubao Agent 流式生成；测试时可用 `?agent=false` 或 `use_doubao=false` 走本地预设。
3. 后端读取 `data/text-materials/social-reality.json` 的现实议题、梗角度、分镜种子，同时读取 `data/assets-index.json` 的猫动画和背景描述。
4. 编剧 Agent 通过一次协调式流式请求生成 3 个候选剧本；前端候选卡片会逐步展示标题、现实矛盾、字幕段落和素材匹配分。
5. 用户选择一个候选后，前端调用 `/api/maomeme/select-stream`。导演 Agent 将剧本拆成 timeline；素材 Agent 会并行预匹配每个镜头的猫动画、背景、裁剪时长、双猫布局、转场和 overlay 动作，再按时间顺序流式返回。
6. 如果缺少具体背景，例如“烤肠摊”“小吃摊”，分镜会记录 `background_prompt` 和补图状态；已有 Seedream 生成素材会优先复用，分镜阶段不等待慢速补图。
7. 用户可用自然语言调用 `/api/maomeme/revise` 调整，例如“更讽刺一点”“结尾更温暖”“增加双猫对话”。
8. 用户点击生成视频后，前端调用 `/api/maomeme/render-jobs`，后端创建异步渲染任务，前端轮询 `/api/maomeme/render-jobs/{job_id}`。
9. 渲染器用 FFmpeg/Pillow/HyperFrames 包装执行：裁剪猫素材、保留并混合原素材音频、优先使用预抠透明猫素材并在缺失时实时抠绿、叠背景、加字幕/气泡/飞物件/盖章/转场，最后输出 mp4。
10. 最终视频写入 `output/jobs/`，前端可预览和下载；中间计划与运行文件写入 `backend/outputs/`。

## Agent 与固定 Workflow

目前建议保留“Agent + 固定 workflow”的混合架构，而不是让 Agent 直接操控所有脚本。

- Agent 负责创意决策：生成剧本、拆分镜、选择素材、决定猫素材裁剪、转场、双猫对话、动态 overlay 和 HyperFrames 包装。
- 固定 workflow 负责稳定执行：读取索引、校验计划、生成字幕图、生成 overlay 帧、调用 FFmpeg/HyperFrames 合成视频。
- 这样做的好处是创意可变，渲染可控；Agent 输出结构化 JSON，渲染器只执行可验证字段，避免每次生成一段不可控代码。

后端当前用到的脚本和工具边界：

| 文件 | 用途 | 类型 |
| --- | --- | --- |
| `backend/app/services/agent_tools.py` | 素材检索、裁剪规划、转场规划、overlay 规划、背景补图决策 | Agent 工具函数 |
| `backend/app/services/agent_runtime.py` | Ark/OpenAI Agents SDK 可切换的多轮工具调用 runtime | Agent 编排 |
| `scripts/index-assets.mjs` | 扫描猫动画和背景描述，生成 `data/assets-index.json` | 固定 workflow |
| `scripts/clean-background-green-bands.py` | 裁掉生成背景图底部误出现的绿幕色块 | 固定素材清理 |
| `scripts/preprocess-cat-green-screen.mjs` | 把猫绿幕 mp4 预处理成本地透明 mov 缓存 | 固定素材清理 |
| `scripts/render-demo-video.mjs` | FFmpeg/Pillow 视频渲染执行器，支持片段并行 | 固定 workflow |
| `scripts/make-caption.py` | 生成字幕 PNG | 渲染辅助脚本 |
| `scripts/make-overlay-frames.py` | 生成飞物件、盖章、弹窗等 overlay 帧 | 渲染辅助脚本 |
| `backend/scripts/smoke_test.py` | 后端 API smoke test | 手动测试 |
| `backend/scripts/seedream_smoke.py` | Seedream 生图 smoke test | 手动测试 |
| `backend/scripts/smoke_agent_runtimes.py` | 对比 Ark SDK 与 OpenAI Agents SDK 工具调用 | 手动测试 |
| `backend/scripts/generate_preset_backgrounds.py` | 用 Seedream 生成高频预制背景 | 手动素材补全 |
| `backend/scripts/generate_plan.py` | 命令行生成 plan | 手动 demo |

可调并发环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ARK_AGENT_CONCURRENCY` | `3` | 非流式批量候选的 Doubao 并发上限 |
| `AGENT_RUNTIME` | `auto` | `auto` 优先 Ark SDK，`ark` 固定 Ark，`openai_agents` 固定 OpenAI Agents SDK，`workflow` 只走本地流程 |
| `OPENAI_AGENTS_PROVIDER` | `auto` | OpenAI Agents SDK 的模型提供方，`auto` 可用 OpenAI key 或 Ark key，`ark` 强制用 Ark base URL |
| `SHOT_AGENT_CONCURRENCY` | `6` | 分镜 ShotPlannerAgent 并发上限 |
| `SHOT_AGENT_MAX_REVISIONS` | `2` | CriticAgent 每个分镜最多修订轮数 |
| `STORYBOARD_MATCH_CONCURRENCY` | `6` | 分镜素材预匹配并发上限 |
| `RENDER_SEGMENT_CONCURRENCY` | `2` | 视频片段渲染并发上限 |

## 手工素材补充指南

如果要手动补充网络梗、剧本或素材，优先补下面几类：

- 社会现实与网络梗文本：编辑 `data/text-materials/social-reality.json`。新增 topic 时建议包含 `id`、`title`、`keywords`、`facts`、`tensions`、`meme_angles`、`beat_seed`、`preferred_assets`。其中 `meme_angles` 写网络梗角度，`beat_seed` 写 hook/setup/escalation/punchline 的短字幕。
- 预制场景：编辑 `data/preset-scenes/social-scenes.json`。每个场景写 `triggers`、`keywords`、`recommended_backgrounds`、`seedream_prompt` 和 `use_cases`，让 Agent 更容易把“招聘会等待区”“烤肠摊”“自习室”“出租屋账单”这类具体背景选出来。
- 猫动画描述：把新 mp4 放到 `assets/cat-motions/`，并更新 `assets/cat-motions/descriptions.json`。描述要写清动作、情绪、道具、适合场景，例如“橘猫探头碎碎念，对面猫生无可恋，适合双猫对话”。
- 背景图描述：把图片放到 `assets/backgrounds/<scene>/`，更新对应 `descriptions.json`。具体场景越好，例如“真实校门口烤肠摊，摊车、烤肠机、夜市灯光，无可读文字”，比“街道背景”更容易被 Agent 命中。
- 爆款参考视频：把完整猫 meme 视频放到 `samples/viral/`。这些用于后续抽取节奏、字幕密度、转场和叙事结构。
- 结构协议和项目说明：`data/structures/` 和 `docs/` 可放比赛要求、剧本结构协议、爆款拆解笔记。

补充素材后运行索引：

```bash
cd frontend
npm run index:assets
```

如果 Seedream 或人工素材里出现明显的绿色底条，先清理背景图再重新索引：

```bash
cd frontend
npm run assets:clean-backgrounds
npm run index:assets
```

如果猫素材边缘绿幕明显，可以生成本地预抠缓存。缓存位于 `assets/processed/cat-motions-keyed/`，体积较大、可再生，不提交 Git：

```bash
cd frontend
npm run assets:preprocess-cats
```

绿幕处理分两类：

- 背景图如果有明显绿色底条，`assets:clean-backgrounds` 会只裁掉底部疑似绿幕色块，不改主体画面。
- 猫动画默认在渲染时用 FFmpeg `colorkey + despill` 实时抠绿；如果已经运行过 `assets:preprocess-cats`，渲染器会优先使用 `assets/processed/cat-motions-keyed/*.mov` 的透明缓存，速度更稳、边缘更干净。

这些 `.mov` 是带透明通道的中间缓存，主要给 FFmpeg 叠背景使用，不是最终预览文件；VS Code 或部分播放器可能显示黑底、无法打开或看起来很怪。要检查效果，请看 `output/jobs/*.mp4` 的最终成片。

## 素材上传策略

当前仓库尽量直接上传可复现主流程所需素材：`assets/cat-motions/` 的猫动作 mp4、`assets/backgrounds/` 的背景图、`assets/generated/backgrounds/` 的预制 Seedream 背景，以及所有 `descriptions.json` 和 `data/assets-index.json`。

`assets/processed/` 是本地预处理缓存，例如预抠绿后的透明猫素材，默认忽略不上传；团队成员可用 `npm run assets:preprocess-cats` 从原始猫素材重新生成。

如果后续素材太大导致 GitHub 上传不稳定，优先保证这些信息被提交：

- 素材描述：猫动作、背景图和生成背景的 `descriptions.json`。
- 素材路径：记录期望放置路径，例如 `assets/cat-motions/27.mp4` 或 `samples/viral/<name>.mp4`。
- 素材用途：说明适合什么主题、情绪、动作、背景或分镜。
- 可替代方案：如果原视频不上传，说明是否可用已有素材裁剪、Seedream 背景、字幕包装替代。

图片素材通常体积可控，尽量上传。猫动作视频目前单个文件都在 GitHub 普通上传范围内，也已纳入版本库。`samples/viral/` 是爆款参考视频目录，不是主素材库；如果是未使用的原始参考视频或太大的完整视频，可以不提交，只在 `samples/viral/README.md` 或文档里写清楚本地路径和用途。

## 启动命令

首次安装前端依赖：

```bash
cd frontend
npm install
```

生成素材索引：

```bash
cd frontend
npm run index:assets
```

启动后端：

```bash
cd backend
conda run -n cv python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动前端：

```bash
cd frontend
npm run dev
```

默认访问：

- 前端：`http://localhost:5173/`
- 后端：`http://localhost:8000/health`

本地预设调试入口：

- `http://localhost:5173/?agent=false`

如果调试时临时启动了后端或前端进程，结束调试后请关闭进程，避免下次启动出现端口占用。常用检查命令：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

## 测试与演示

前端构建：

```bash
cd frontend
npm run build
```

后端 smoke test，必须使用 conda `cv` 环境：

```bash
cd backend
conda run -n cv python scripts/smoke_test.py
```

本地完整 demo，包含素材索引、后端计划生成、计划质检和 FFmpeg 合成：

```bash
cd frontend
npm run demo
```

批量生成示例视频：

```bash
cd frontend
npm run examples
```

Seedream 背景生成 smoke test，成功后会写入 `assets/generated/backgrounds/` 并刷新素材索引：

```bash
cd backend
conda run -n cv python scripts/seedream_smoke.py
```

Agent runtime 对比 smoke test，不会打印 API key，只输出是否配置、provider、耗时和是否拿到分镜 JSON：

```bash
cd backend
conda run -n cv python scripts/smoke_agent_runtimes.py --provider ark
conda run -n cv python scripts/smoke_agent_runtimes.py --provider openai_agents --openai-provider ark
```

当前本地测试结论：Ark SDK 和 OpenAI Agents SDK 通过 Ark base URL 都能完成工具调用；速度接近，Ark SDK 日志更干净，所以默认保留 Ark SDK。需要验证 OpenAI Agents SDK 时设置 `AGENT_RUNTIME=openai_agents`。

lite/pro 单镜头工具调用实测：

- `ARK_MODEL_MODE=pro`：约 56 秒，结构完整，overlay 2 个。
- `ARK_MODEL_MODE=lite`：约 45 秒，结构完整，overlay 2 个。
- 建议前端交互和日常调试默认用 lite；最终批量高质量生成或重要样片再切 pro。

批量生成高频预制背景，成功后会写入 `assets/generated/backgrounds/preset-*` 并自动刷新索引：

```bash
cd backend
conda run -n cv python scripts/generate_preset_backgrounds.py
```

## 爆款猫 Meme 拆解库

爆款参考视频按“本地 raw + 可提交分析结果”的方式管理。原视频复制到 `samples/viral-structure/baokuan-maomeme/raw/`，该目录不提交；清单和分析结果提交，方便团队共享剧本、分镜、背景、猫素材和声音设计。

导入 43 条爆款视频：

```bash
conda run -n cv python backend/scripts/import_viral_maomeme.py
```

并发调用 Doubao 视频理解分析，默认使用 base64 `video_url`，默认并发为 8，可按火山引擎额度调大到 16：

```bash
conda run -n cv python backend/scripts/analyze_viral_maomeme.py --concurrency 8 --resume
```

小批量验证：

```bash
conda run -n cv python backend/scripts/analyze_viral_maomeme.py --limit 3 --concurrency 3
```

无 API 或调试本地链路：

```bash
conda run -n cv python backend/scripts/analyze_viral_maomeme.py --limit 2 --use-doubao false
```

分析结果位于 `data/viral-structures/baokuan-maomeme/`。每条视频会生成 `structure.json`、`asset_plan.json`、`storyboard.md`、`contact_sheet.jpg` 和抽帧；本地参考音频 `audio.m4a` 与原始 Doubao 响应 `raw_doubao_response.json` 默认忽略不提交。`asset_plan.storyboard` 是后续 Agent 最重要的输入，每个分镜包含具体剧本、梗点、背景、猫素材关键词、BGM/配音/音效和 Seedream prompt。

HyperFrames 包装模板位于 `hyperframes/templates/packaging-presets.json`。当前主视频合成仍由稳定 FFmpeg/Pillow 执行，HyperFrames 负责在合成前生成每个分镜的包装 manifest，并补齐更具体的镜头内道具和动作，例如：

- 求职分镜：手机招聘信息流、岗位要求卡、招聘消息栈。
- 上班分镜：工作群弹窗、会议同步提示。
- 考研考公分镜：三选一焦虑面板。
- 生活成本分镜：账单卡、预算表。
- 摆摊/烤肠分镜：小摊价签和摊位提示。

渲染器会读取 HyperFrames manifest，把这些结构化 `overlay_actions` 交给 Pillow 生成透明帧，再由 FFmpeg 叠到猫动画和背景上。这样可以提高单个分镜的可读性和爆款感，同时不让 Agent 自由写渲染代码。

## 主要接口

- `GET /health`
- `GET /api/maomeme/assets`
- `POST /api/maomeme/candidates`：输入主题，返回 3 个剧本候选。
- `POST /api/maomeme/select`：选择候选后生成完整分镜 plan。
- `POST /api/maomeme/revise`：自然语言调整候选或 plan。
- `POST /api/maomeme/render-jobs`：提交异步渲染任务。
- `GET /api/maomeme/render-jobs/{job_id}`：轮询渲染进度。
- `POST /api/maomeme/generate-background`：当 `allow_ai_fill=true` 时用 Seedream 生成缺失背景素材。
- `POST /api/analyze/structure`：参考视频结构分析。

## 目录说明

```text
frontend/                  React/Vite 前端工作台与 npm 脚本入口
backend/                   FastAPI、Agent、素材匹配和渲染 job 队列
scripts/                   跨项目 CLI 工具：素材索引、质检、字幕图、FFmpeg 渲染
assets/cat-motions/        猫 meme 动画素材
assets/backgrounds/        背景图素材
assets/generated/          Seedream 等工具生成的补充素材
assets/archives/           原始素材压缩包
data/text-materials/       社会现实文本素材库
data/preset-scenes/        高频社会现实背景和 Seedream prompt 预设
data/structures/           结构迁移协议
data/runs/                 运行生成的 plan/audit JSON
samples/viral/             爆款参考视频样例
samples/viral-structure/   爆款猫 meme 本地 raw 视频与 manifest
docs/                      比赛文档和项目说明
output/                    最终生成视频和公开产物
backend/outputs/           后端运行中间文件
```

## 备注

- Python 不使用系统环境，也不创建 `backend/.venv`，统一通过 `conda run -n cv ...` 执行。
- `backend/.env.example` 只说明变量名；真实 key 只放 `backend/.env` 或系统环境变量。
- `.env`、`backend/.env`、`assets/archives/`、`output/`、`backend/outputs/`、`node_modules/` 不会上传 GitHub。
- 缺素材时默认先用字幕包装、裁切复用和结构重排补足；Seedream 只作为显式开关的后续增强。
- 建议继续补充 3-5 条完整猫 meme 爆款视频到 `samples/viral/`，用于抽取真实镜头节奏、字幕密度和转场风格。
