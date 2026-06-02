# 猫 meme 爆款样例视频结构分析 Prompt

你是一个专业短视频结构分析师。请拆解视频的创作方法论，而不是复述内容。

必须分析：

1. 脚本结构 sections
   - type: hook / setup / escalation / twist / punchline / cta
   - start_time, end_time
   - text: 字幕或画面表达
   - purpose: 该段在传播链路中的作用
   - hook_type: 仅 hook 段填写

2. 镜头结构 shots
   - start_time, end_time
   - type: close-up / medium / wide / text-overlay / transition
   - content: 画面内容、主体动作、背景、构图
   - camera_move: 静止/推/拉/摇/移/缩放/手持晃动
   - has_subtitle
   - visual_effect
   - subject_distance: near / mid / far / tiny / out / none
   - subject_position
   - subject_motion

3. 音频结构 audio_structure
   - bgm: {name, mood, bpm_range}
   - voiceover: {has, style, language}
   - sound_effects: [{time, description}]
   - rhythm_sync: 镜头是否卡点及说明

4. 包装结构 packaging_structure
   - subtitle_style: {font_size, color, position, animation, outline}
   - transitions: [{time, type, description}]
   - text_graphics: [{time_range, type, content, style}]
   - cover_style: {main_text, subtitle_text, style, colors, layout}
   - overall_visual_tone

5. 可迁移特征 transferable_features
   - hook_strategy
   - narrative_pattern
   - pacing_pattern
   - spatial_pattern
   - subject_trajectory
   - composition_pattern
   - engagement_techniques
   - suitable_categories

严格返回 JSON，不要有多余文字。不存在的字段填空字符串、空数组或 false。
