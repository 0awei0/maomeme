from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.doubao_client import candidate_messages, generate_plan_with_doubao_context
from app.services.maomeme_agent import candidate_angles, script_to_candidate
from app.services.viral_structure_library import build_migration_blueprint


class CandidatePromptTest(unittest.TestCase):
    def test_candidate_prompt_tells_agent_to_learn_method_not_copy_plot(self):
        messages = candidate_messages(
            theme="小时候偷吃父亲留的烧烤，长大后才懂亲情",
            assets_summary="",
            text_context={
                "title": "童年亲情回忆",
                "facts": ["2026届高校毕业生规模预计1270万人。"],
            },
            viral_reference_text="参考视频：大学生找工作难，岗位要求离谱，转去烤肠摊。",
        )
        prompt = "\n".join(str(message.get("content", "")) for message in messages)

        self.assertIn("学习创作方法", prompt)
        self.assertIn("脚本结构", prompt)
        self.assertIn("镜头节奏", prompt)
        self.assertIn("字幕样式", prompt)
        self.assertIn("画面包装", prompt)
        self.assertIn("转场", prompt)
        self.assertIn("BGM卡点", prompt)
        self.assertIn("不得复制参考视频的剧情母题", prompt)
        self.assertIn("新主题的核心人物关系、场景和情绪落点", prompt)
        self.assertIn("剧情内容必须从用户填入的新视频主题", prompt)
        self.assertIn("只能提取并改写新主题里的事实、人物关系、物件和情绪", prompt)
        self.assertIn("参考视频不得提供剧情事实", prompt)
        self.assertIn("任何结构参考都不得变成剧情事实来源", prompt)
        self.assertNotIn("社会现实文本素材", prompt)
        self.assertNotIn("童年亲情回忆", prompt)
        self.assertNotIn("1270万人", prompt)

    def test_candidate_prompt_uses_storyboard_reference_not_text_material_payload(self):
        messages = candidate_messages(
            theme="小时候偷吃父亲留的烧烤，长大后才懂亲情",
            assets_summary="",
            text_context={
                "title": "社会现实文本素材库",
                "keywords": ["高校毕业生", "就业压力"],
                "tensions": ["岗位拥挤"],
                "beat_seed": {"proof": "高校毕业生规模千万级"},
            },
            viral_reference_text=(
                "## 强制迁移蓝图与 3 条 compact few-shot\n"
                '{"shot_scripts":[{"shot_id":"1","beat":"hook","script":"压岁钱先给镜头一个误会"},'
                '{"shot_id":"2","beat":"twist","script":"多年后再揭开真相"}]}'
            ),
        )
        prompt = "\n".join(str(message.get("content", "")) for message in messages)

        self.assertIn("shot_scripts", prompt)
        self.assertIn("压岁钱先给镜头一个误会", prompt)
        self.assertIn("多年后再揭开真相", prompt)
        self.assertNotIn("社会现实文本素材库", prompt)
        self.assertNotIn("高校毕业生", prompt)
        self.assertNotIn("岗位拥挤", prompt)
        self.assertNotIn("高校毕业生规模千万级", prompt)

    def test_plan_prompt_does_not_send_text_material_payload_to_ark(self):
        captured: dict[str, object] = {}

        class FakeCompletions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content='{"timeline": []}'))
                    ]
                )

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=FakeCompletions())

            async def close(self):
                return None

        async def run_case():
            with patch("app.services.doubao_client.get_async_ark_client", return_value=FakeClient()):
                return await generate_plan_with_doubao_context(
                    theme="小时候偷吃父亲留的烧烤，长大后才懂亲情",
                    source_summary="asset_plan.storyboard 压缩结构：hook -> setup -> twist",
                    assets_summary="本地猫素材",
                    text_context={
                        "title": "社会现实文本素材库",
                        "facts": ["2026届高校毕业生规模预计1270万人。"],
                        "keywords": ["岗位", "简历"],
                    },
                )

        result = asyncio.run(run_case())
        prompt = "\n".join(str(message.get("content", "")) for message in captured.get("messages", []))

        self.assertEqual(result, {"timeline": []})
        self.assertIn("asset_plan.storyboard 压缩结构", prompt)
        self.assertIn("新主题", prompt)
        self.assertNotIn("社会现实文本素材", prompt)
        self.assertNotIn("1270万人", prompt)
        self.assertNotIn("岗位", prompt)
        self.assertNotIn("简历", prompt)

    def test_candidate_angles_do_not_use_text_material_tensions(self):
        angles = candidate_angles(
            "小时候偷吃父亲留的烧烤，长大后才懂亲情",
            {
                "title": "社会现实文本素材库",
                "tensions": ["高校毕业生规模千万级，岗位更拥挤"],
                "meme_angles": ["考研考公投简历"],
            },
        )
        joined = "\n".join(angles)

        self.assertNotIn("高校毕业生", joined)
        self.assertNotIn("岗位", joined)
        self.assertNotIn("考研", joined)
        self.assertNotIn("投简历", joined)

    def test_candidate_metadata_does_not_expose_text_material_context(self):
        theme = "小时候偷吃父亲留的烧烤，长大后才懂亲情"
        candidate = script_to_candidate(
            {"name": "亲情版", "beats": [("hook", "小时候我总偷两串", "开场")]},
            theme,
            0.0,
            {
                "title": "社会现实文本素材库",
                "tensions": ["高校毕业生规模千万级，岗位更拥挤"],
            },
            1,
        )
        joined = "\n".join([candidate.social_topic, candidate.tension, *candidate.notes])

        self.assertIn(theme, candidate.social_topic)
        self.assertNotIn("社会现实文本素材库", joined)
        self.assertNotIn("高校毕业生", joined)
        self.assertNotIn("岗位", joined)
        self.assertNotIn("文本素材", joined)

    def test_candidate_blueprint_handles_career_to_street_food_theme_without_undefined_helper(self):
        theme = "大学生找工作难，最后去学校门口卖烤肠也要内卷"
        references = [
            {
                "id": "ref-career",
                "title": "求职压力爆款",
                "storyboard": [{"shot_id": str(index), "beat": role} for index, role in enumerate(["hook", "setup", "pressure", "twist", "punchline"], start=1)],
            }
        ]

        blueprint = build_migration_blueprint(theme, references, {}, {})
        requirements = " ".join(str(shot.get("background_requirement", "")) for shot in blueprint.get("shots", []))

        self.assertIn("烤肠", requirements)


if __name__ == "__main__":
    unittest.main()
