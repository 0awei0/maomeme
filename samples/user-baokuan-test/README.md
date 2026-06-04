# 用户爆款上传测试样例

这个目录用于模拟比赛要求里的“用户在线上传爆款猫 meme 参考视频”场景。

- `raw/`: 9 条用户爆款参考视频样例，来自本地目录 `/Users/a1-6/Desktop/code/douyin/videos/user-baokuan-test`。
- 用途：测试上传、Doubao 爆款拆解、剧本迁移、分镜生成和使用项目内置素材复刻视频的完整闭环。
- 边界：这些视频只作为用户上传参考样例，不作为自动复用的猫动作或背景素材库；系统只抽取剧本结构、镜头需求、字幕包装和声音节奏。
- 验收记录：全量测试结果写入 `docs/user-baokuan-test-report.md`。

公共爆款 raw 视频库体积较大，不随仓库提交；公共爆款的可复用结构化拆解结果保存在 `data/viral-structures/baokuan-maomeme/`。
