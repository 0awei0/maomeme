import os
import sys
import unittest
from unittest.mock import patch

from app.models.maomeme import MaoMemePlan
from app.services.render_jobs import prepare_plan_for_render, render_env


class RenderEnvTest(unittest.TestCase):
    def test_uses_current_python_when_conda_prefix_is_missing(self):
        with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            env = render_env()

        self.assertEqual(env.get("MAOMEME_PYTHON"), sys.executable)

    def test_render_plan_removes_extra_secondary_for_structured_double_cat_motion(self):
        plan = MaoMemePlan.model_validate({
            "id": "render-test",
            "theme": "618 大促吐槽",
            "timeline": [
                {
                    "id": "02-setup",
                    "start": 2.0,
                    "end": 5.0,
                    "role": "setup",
                    "intent": "双猫对话吐槽",
                    "copy": "啥？上周不是刚过618？",
                    "layout": "dialogue",
                    "motion": {
                        "id": "16",
                        "file": "assets/cat-motions/16.mp4",
                        "description": "画面中有两只猫，橘猫从一侧探头张嘴碎碎念，旁边灰白猫低头不动，表情疲惫。",
                        "motion_tags": {
                            "actions": ["双猫", "对话"],
                            "contexts": ["对话反差"],
                        },
                    },
                    "secondary_motion": {
                        "id": "1",
                        "file": "assets/cat-motions/1.mp4",
                        "description": "蓝衣灰猫坐在笔记本电脑前，两只前爪放在键盘附近反复敲动。",
                    },
                    "secondary_motion_clip": {"start": 1.2, "duration": 3.0},
                    "background": {"id": "window/1", "file": "assets/backgrounds/window/1.jpg", "description": "窗边卧室"},
                    "dialogue": [
                        {"speaker": "left", "text": "猫：这事不对劲"},
                        {"speaker": "right", "text": "旁白猫：先别急"},
                    ],
                }
            ],
        })

        prepared = prepare_plan_for_render(plan)
        slot = prepared.timeline[0]

        self.assertIsNone(slot.secondary_motion)
        self.assertIsNone(slot.secondary_motion_clip)
        self.assertTrue(slot.motion_quality.get("natural_double"))
        self.assertIn("内置双猫素材", slot.visual_summary)
        self.assertNotIn("右侧副猫", slot.visual_summary)

    def test_render_plan_strips_bill_leaks_from_emotional_relationship_slots(self):
        plan = MaoMemePlan.model_validate({
            "id": "relationship-render-test",
            "theme": "情侣吵架的本质：女生要情绪价值和态度，男生只想解决具体问题",
            "timeline": [
                {
                    "id": "03-escalation",
                    "start": 5.0,
                    "end": 8.4,
                    "role": "escalation",
                    "intent": "情侣沟通频道错位，不是金钱账单冲突",
                    "copy": "我要的是态度！你问那么多...",
                    "layout": "single",
                    "motion": {
                        "id": "9",
                        "file": "assets/cat-motions/9.mp4",
                        "description": "香蕉猫正面对镜头大哭。",
                    },
                    "background": {
                        "id": "generated/preset-rental-bill-room/1780413047.png",
                        "file": "output/generated-backgrounds/preset-rental-bill-room/1780413047.png",
                        "description": "出租屋账单背景，床边桌、账单、行李箱和简易衣架。",
                    },
                    "overlay_actions": [
                        {"type": "bill_card", "title": "现实账单", "items": ["房租"]},
                        {"type": "throw_object", "object": "bill_stack", "text": "账单 -2400"},
                    ],
                }
            ],
        })

        prepared = prepare_plan_for_render(plan)
        slot = prepared.timeline[0]
        overlay_text = str([action.model_dump() for action in slot.overlay_actions])
        background_text = f"{slot.background.id} {slot.background.description}"

        self.assertNotRegex(overlay_text, r"bill_card|bill_stack|账单|房租")
        self.assertNotRegex(background_text, r"rental-bill|出租屋|账单|房租")


if __name__ == "__main__":
    unittest.main()
