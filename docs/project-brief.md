# MaoMeme 爆款结构迁移引擎

## 比赛要求理解

P0 要做出完整闭环：样例视频输入解析、结构拆解、新主题或素材输入、结构迁移生成、素材缺口识别与补全、迁移过程可视化，以及可验证的分镜/时间线/成片 demo。

比赛强调不是复刻样例内容，而是迁移创作方法。因此本项目把猫 meme 作为垂直赛道：爆款样例负责提供节奏、段落和包装结构；本地猫动画与背景库负责承接新剧本。

## 当前素材库

- `assets/cat-motions/`：27 个 960x544 猫 meme 动画视频，含震惊、哭哭、打工、跳舞、开车、演奏等情绪动作。
- `assets/backgrounds/`：78 张背景图，覆盖办公室、学校、城市、街道、车内、室内、窗边等场景。
- `assets/cat-motions/descriptions.json` 与 `assets/backgrounds/**/descriptions.json`：素材中文描述，可直接用于 Agent 的素材检索与解释。

## 工程骨架

- `frontend/`：评审展示页，展示候选剧本、结构迁移时间线、缺口补全状态和 Agent 规划。
- `scripts/index-assets.mjs`：扫描本地猫动画和背景图库，生成 `data/assets-index.json`。
- `scripts/generate-demo-plan.mjs`：本地 mock Agent，先生成可解释的剧本/分镜/时间线。
- `scripts/render-demo-video.mjs`：用 FFmpeg 将背景图、猫动画和字幕合成为 demo 视频。
- `data/structures/cat-meme-protocol.json`：结构迁移协议，后续接豆包时让模型稳定输出 JSON。

## 是否需要完整爆款猫 meme 视频

需要，但不是搭骨架的前置条件。

当前素材库已经足够跑通“新内容生成 -> 素材匹配 -> 缺口补全 -> 合成 demo”。不过如果要在评分项里拿到更高的“样例解析”“结构拆解”“前后对比”分数，建议下载 3-5 条完整爆款猫 meme 视频，最好覆盖不同结构：

- 反差开头型：前 1-2 秒强表情或强字幕 hook。
- 情绪递进型：平静、崩溃、反转、收束。
- 卡点鬼畜型：高频切镜、重复动作、音乐节奏明显。
- 对话吐槽型：字幕密集，梗靠台词推进。
- 场景迁移型：同一个猫动作在不同背景/语境下变梗。

这些视频用于提取真实镜头时长、转场密度、字幕样式和高潮位置。下载后放到 `samples/viral/`，后续解析脚本可以补上自动 ffprobe、抽帧、ASR/OCR、多模态理解。
