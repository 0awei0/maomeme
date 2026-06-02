# 文本素材库

`data/text-materials/social-reality.json` 给编剧 Agent 提供社会现实母题，避免剧本只停留在简单搞笑。

当前主题：

- 大学生工作难找
- 上班内卷与隐形加班
- 不是没岗位，是好工作更难找
- 考研考公还是就业

每个主题包含：

- `facts`：可作为现实锚点的数据或背景。
- `tensions`：社会矛盾和情绪压力。
- `meme_angles`：适合猫 meme 的反差角度。
- `beat_seed`：hook、setup、escalation、punchline 的分镜种子。
- `preferred_assets`：建议优先匹配的猫动作和背景。
- `sources`：资料来源链接，便于后续核验和扩展。

Agent 使用方式：

1. 根据用户主题匹配文本素材主题。
2. 编剧 Agent 从 `facts/tensions/meme_angles/beat_seed` 生成多个候选剧本。
3. 素材导演 Agent 根据 `preferred_assets` 和分镜文案匹配猫动画、背景图。
4. 质检 Agent 检查文案和素材是否冲突。

扩展建议：

- 每个主题至少补 3 个 `meme_angles`，避免剧本同质化。
- `beat_seed` 要尽量具体，能直接变成 4 镜头短视频。
- 不要把严肃事实硬塞成说教，结尾最好通过猫的行为完成轻反转。
