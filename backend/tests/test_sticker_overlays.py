from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.services.agent_tools import cat_casting_tool, normalize_agent_overlay_action, overlay_design_tool, safe_sticker_motion, shot_critic_tool
from app.services.maomeme_agent import normalize_overlay_actions


ROOT = Path(__file__).resolve().parents[2]


class StickerOverlayTest(unittest.TestCase):
    def test_overlay_design_adds_real_sticker_action(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/001-mouse",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/001-mouse.png",
                    "description": "透明底猫meme贴纸，主体是鼠标。关键词：鼠标、电脑、办公。",
                },
                {
                    "id": "digital-communication/011-computer",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/011-computer.png",
                    "description": "透明底猫meme贴纸，主体是电脑。关键词：电脑、工作办公。",
                },
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                }
            ]
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, {}, {})

        stickers = [action for action in actions if action.get("type") == "sticker"]
        self.assertTrue(stickers)
        self.assertEqual(stickers[0]["file"], "assets/stickers/digital-communication/005-phone.png")
        self.assertEqual(stickers[0]["anchor"], "near_cat")
        self.assertEqual(stickers[0]["motion"], "static")
        self.assertLessEqual(stickers[0]["x"], 430)
        self.assertGreaterEqual(stickers[0]["y"], 390)

    def test_phone_context_skips_mouse_when_no_phone_or_chat_sticker_exists(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/001-mouse",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/001-mouse.png",
                    "description": "透明底猫meme贴纸，主体是鼠标。关键词：鼠标、电脑、办公。",
                },
                {
                    "id": "digital-communication/011-computer",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/011-computer.png",
                    "description": "透明底猫meme贴纸，主体是电脑。关键词：电脑、工作办公。",
                },
            ]
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, {}, {})

        self.assertFalse(any(action.get("type") == "sticker" for action in actions))

    def test_phone_context_prefers_phone_in_hand_over_plain_phone(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                },
                {
                    "id": "digital-communication/020-phone-in-hand",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/020-phone-in-hand.png",
                    "description": "透明底猫meme贴纸，主体是手持手机这一数码通讯道具。关键词：手持手机、聊天、消息、办公。",
                },
            ]
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/digital-communication/020-phone-in-hand.png")

    def test_phone_context_composes_hand_and_phone_when_phone_in_hand_missing(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                },
                {
                    "id": "emotion-effects/027-hand",
                    "type": "sticker",
                    "category": "emotion-effects",
                    "file": "assets/stickers/emotion-effects/027-hand.png",
                    "description": "透明底猫meme贴纸，主体是手。关键词：手、震惊、无语、生气、尴尬、崩溃。",
                },
            ]
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["sticker_id"], "composite/phone-in-hand")
        self.assertEqual(sticker["composite"], "phone_in_hand")
        self.assertFalse(sticker.get("file"))
        self.assertEqual([component["role"] for component in sticker["components"]], ["phone", "hand"])
        self.assertEqual(sticker["components"][0]["file"], "assets/stickers/digital-communication/005-phone.png")
        self.assertEqual(sticker["components"][1]["file"], "assets/stickers/emotion-effects/027-hand.png")

    def test_phone_sticker_position_follows_cat_layout_gaze_direction(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                }
            ]
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }
        left_gaze_motion = {
            "id": "left-cat",
            "cat_layout": {
                "body_box": {"x": 430, "y": 210, "w": 250, "h": 280},
                "head_box": {"x": 450, "y": 230, "w": 150, "h": 150},
                "face_direction": "left",
            },
        }
        right_gaze_motion = {
            "id": "right-cat",
            "cat_layout": {
                "body_box": {"x": 180, "y": 220, "w": 230, "h": 260},
                "head_box": {"x": 220, "y": 235, "w": 140, "h": 140},
                "face_direction": "right",
            },
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            left_actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, left_gaze_motion, {})
            right_actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, right_gaze_motion, {})

        left_sticker = next(action for action in left_actions if action.get("type") == "sticker")
        right_sticker = next(action for action in right_actions if action.get("type") == "sticker")
        self.assertLess(left_sticker["x"], left_gaze_motion["cat_layout"]["body_box"]["x"])
        self.assertGreater(right_sticker["x"], right_gaze_motion["cat_layout"]["body_box"]["x"] + right_gaze_motion["cat_layout"]["body_box"]["w"])
        self.assertGreater(left_sticker["y"], left_gaze_motion["cat_layout"]["head_box"]["y"] + 80)
        self.assertGreater(right_sticker["y"], right_gaze_motion["cat_layout"]["head_box"]["y"] + 80)

    def test_cat_casting_preserves_layout_for_sticker_positioning(self):
        fake_index = {
            "cat_motions": [
                {
                    "id": "layout-cat",
                    "file": "assets/cat-motions/layout-cat.mp4",
                    "description": "黑猫瞪大圆眼震惊，适合 hook。",
                    "duration": 3,
                    "cat_layout": {
                        "body_box": {"x": 440, "y": 230, "w": 230, "h": 260},
                        "head_box": {"x": 460, "y": 240, "w": 150, "h": 150},
                        "face_direction": "left",
                    },
                }
            ],
            "stickers": [
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                }
            ],
        }
        beat = {
            "role": "hook",
            "caption": "打开招聘APP那秒",
            "intent": "用招聘软件切入求职压力",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            motion = cat_casting_tool(fake_index, "大学生工作难找，投简历像进黑洞", beat)[0]
            actions = overlay_design_tool("大学生工作难找，投简历像进黑洞", beat, motion, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertIn("cat_layout", motion)
        self.assertLess(sticker["x"], fake_index["cat_motions"][0]["cat_layout"]["body_box"]["x"])

    def test_requirement_shot_prefers_emotion_sticker_beside_card(self):
        fake_index = {
            "stickers": [
                {
                    "id": "digital-communication/005-phone",
                    "type": "sticker",
                    "category": "digital-communication",
                    "file": "assets/stickers/digital-communication/005-phone.png",
                    "description": "透明底猫meme贴纸，主体是手机。关键词：手机、招聘、消息、工作办公。",
                },
                {
                    "id": "emotion-effects/001-question",
                    "type": "sticker",
                    "category": "emotion-effects",
                    "file": "assets/stickers/emotion-effects/001-question.png",
                    "description": "透明底猫meme贴纸，主体是问号和感叹号。关键词：问号、感叹号、离谱、震惊、汗滴、崩溃。",
                },
            ]
        }
        beat = {
            "role": "setup",
            "caption": "点开要求三年经验",
            "intent": "岗位要求离谱，规则突然加码",
            "scene_keywords": ["招聘软件"],
            "emotion_keywords": ["震惊", "离谱"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，岗位要求越来越离谱", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/emotion-effects/001-question.png")
        self.assertEqual(sticker["anchor"], "beside_card")
        self.assertEqual(sticker["motion"], "static")
        self.assertLess(sticker["x"], 560)

    def test_stall_shot_prefers_food_sticker_on_sign_anchor(self):
        fake_index = {
            "stickers": [
                {
                    "id": "emotion-effects/001-question",
                    "type": "sticker",
                    "category": "emotion-effects",
                    "file": "assets/stickers/emotion-effects/001-question.png",
                    "description": "透明底猫meme贴纸，主体是问号。关键词：问号、离谱、震惊。",
                },
                {
                    "id": "food-drinks/081-sausage",
                    "type": "sticker",
                    "category": "food-drinks",
                    "file": "assets/stickers/food-drinks/081-sausage.png",
                    "description": "透明底猫meme贴纸，主体是烤肠价签。关键词：烤肠、价签、小吃摊、摊位、食物饮品。",
                },
            ]
        }
        beat = {
            "role": "twist",
            "caption": "他转身研究烤肠摊",
            "intent": "现实规则离谱，脑洞转向街边摊",
            "scene_keywords": ["烤肠", "摊位"],
            "emotion_keywords": ["荒诞"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，最后研究烤肠摊", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/food-drinks/081-sausage.png")
        self.assertEqual(sticker["anchor"], "on_sign")
        self.assertEqual(sticker["motion"], "static")
        self.assertLess(sticker["x"], 540)
        self.assertGreater(sticker["y"], 250)

    def test_street_food_context_finds_late_filename_skewer_match(self):
        fillers = [
            {
                "id": f"home-daily/{index:03d}-chair",
                "type": "sticker",
                "category": "home-daily",
                "file": f"assets/stickers/home-daily/{index:03d}-chair.png",
                "description": "透明底猫meme贴纸，主体是椅子。关键词：家居、椅子。",
            }
            for index in range(90)
        ]
        fake_index = {
            "stickers": [
                *fillers,
                {
                    "id": "food-drinks/081-lamb-skewer",
                    "type": "sticker",
                    "category": "food-drinks",
                    "file": "assets/stickers/food-drinks/081-lamb-skewer.png",
                    "description": "透明底猫meme贴纸，主体是羊肉串这一食物饮品道具。关键词：羊肉串、夜宵。",
                },
            ]
        }
        beat = {
            "role": "twist",
            "caption": "他转身研究烤肠摊",
            "intent": "现实规则离谱，脑洞转向街边摊",
            "scene_keywords": ["烤肠", "摊位"],
            "emotion_keywords": ["荒诞"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，最后研究烤肠摊", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/food-drinks/081-lamb-skewer.png")

    def test_street_food_context_skips_generic_snack_sticker(self):
        fake_index = {
            "stickers": [
                {
                    "id": "food-drinks/021-chips",
                    "type": "sticker",
                    "category": "food-drinks",
                    "file": "assets/stickers/food-drinks/021-chips.png",
                    "description": "透明底猫meme贴纸，主体是薯片零食。关键词：零食、食物饮品。",
                },
            ]
        }
        beat = {
            "role": "twist",
            "caption": "他转身研究烤肠摊",
            "intent": "现实规则离谱，脑洞转向街边摊",
            "scene_keywords": ["烤肠", "摊位"],
            "emotion_keywords": ["荒诞"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，最后研究烤肠摊", beat, {}, {})

        self.assertFalse(any(action.get("type") == "sticker" for action in actions))

    def test_medical_leave_theme_can_choose_medical_sticker(self):
        fake_index = {
            "stickers": [
                {
                    "id": "emotion-effects/001-heart",
                    "type": "sticker",
                    "category": "emotion-effects",
                    "file": "assets/stickers/emotion-effects/001-heart.png",
                    "description": "透明底猫meme贴纸，主体是爱心。关键词：可爱、开心。",
                },
                {
                    "id": "medical-emergency/008-mask",
                    "type": "sticker",
                    "category": "medical-emergency",
                    "file": "assets/stickers/medical-emergency/008-mask.png",
                    "description": "透明底猫meme贴纸，主体是口罩和药箱。关键词：口罩、药箱、医疗急救。",
                },
            ]
        }
        beat = {
            "role": "setup",
            "caption": "老板让我先证明",
            "intent": "请假过程变成荒诞流程",
            "scene_keywords": ["办公室"],
            "emotion_keywords": ["无语"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("医疗请假像闯关", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/medical-emergency/008-mask.png")
        self.assertEqual(sticker["anchor"], "corner")
        self.assertEqual(sticker["motion"], "static")

    def test_exam_theme_can_choose_exam_sticker(self):
        fake_index = {
            "stickers": [
                {
                    "id": "campus-study/003-backpack",
                    "type": "sticker",
                    "category": "campus-study",
                    "file": "assets/stickers/campus-study/003-backpack.png",
                    "description": "透明底猫meme贴纸，主体是书包。关键词：校园、学生。",
                },
                {
                    "id": "campus-study/011-pencil",
                    "type": "sticker",
                    "category": "campus-study",
                    "file": "assets/stickers/campus-study/011-pencil.png",
                    "description": "透明底猫meme贴纸，主体是铅笔和试卷。关键词：铅笔、试卷、考试。",
                },
            ]
        }
        beat = {
            "role": "setup",
            "caption": "猫坐到考场第一排",
            "intent": "考试压力突然具象化",
            "scene_keywords": ["考场"],
            "emotion_keywords": ["紧张"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("考试前夜突然破防", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["file"], "assets/stickers/campus-study/011-pencil.png")
        self.assertEqual(sticker["motion"], "static")

    def test_exam_theme_skips_generic_paper_sticker(self):
        fake_index = {
            "stickers": [
                {
                    "id": "home-daily/027-toilet-paper",
                    "type": "sticker",
                    "category": "home-daily",
                    "file": "assets/stickers/home-daily/027-toilet-paper.png",
                    "description": "透明底猫meme贴纸，主体是卫生纸。关键词：卫生纸、家里、宿舍。",
                },
            ]
        }
        beat = {
            "role": "setup",
            "caption": "猫坐到考场第一排",
            "intent": "考试压力突然具象化",
            "scene_keywords": ["考场"],
            "emotion_keywords": ["紧张"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("考试前夜突然破防", beat, {}, {})

        self.assertFalse(any(action.get("type") == "sticker" for action in actions))

    def test_punchline_sticker_can_remain_static_on_sign(self):
        fake_index = {
            "stickers": [
                {
                    "id": "food-drinks/081-sausage",
                    "type": "sticker",
                    "category": "food-drinks",
                    "file": "assets/stickers/food-drinks/081-sausage.png",
                    "description": "透明底猫meme贴纸，主体是烤肠价签。关键词：烤肠、价签、小吃摊、摊位、食物饮品。",
                }
            ]
        }
        beat = {
            "role": "punchline",
            "caption": "摊位写熟练工优先",
            "intent": "荒诞收束，街边摊也开始卷要求",
            "scene_keywords": ["烤肠", "摊位"],
            "emotion_keywords": ["荒诞"],
        }

        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = overlay_design_tool("大学生工作难找，最后研究烤肠摊", beat, {}, {})

        sticker = next(action for action in actions if action.get("type") == "sticker")
        self.assertEqual(sticker["anchor"], "on_sign")
        self.assertEqual(sticker["motion"], "static")

    def test_static_motion_and_new_anchors_are_preserved(self):
        self.assertEqual(safe_sticker_motion("static"), "static")
        fake_index = {
            "stickers": [
                {
                    "id": "emotion-effects/001-question",
                    "type": "sticker",
                    "category": "emotion-effects",
                    "file": "assets/stickers/emotion-effects/001-question.png",
                    "description": "透明底猫meme贴纸，主体是问号和感叹号。关键词：问号、感叹号、离谱。",
                }
            ]
        }
        with patch("app.services.agent_tools.load_assets", return_value=fake_index):
            actions = normalize_overlay_actions(
                [
                    {
                        "type": "sticker",
                        "file": "assets/stickers/emotion-effects/001-question.png",
                        "motion": "static",
                        "anchor": "above_cat",
                        "x": 510,
                        "y": 120,
                        "scale": 0.8,
                    }
                ],
                "岗位要求离谱",
                {"role": "setup", "caption": "点开要求三年经验", "intent": "岗位要求离谱"},
            )

        self.assertEqual(actions[0]["motion"], "static")
        self.assertEqual(actions[0]["anchor"], "above_cat")

    def test_empty_overlay_actions_do_not_penalize_critic_score(self):
        critic = shot_critic_tool(
            "猫咪普通日常",
            {"role": "setup", "caption": "猫看了一眼镜头", "intent": "", "scene_keywords": [], "emotion_keywords": []},
            {"motion": {}, "background": {}, "overlay_actions": []},
            [],
            [],
        )

        self.assertEqual(critic["score"], 1.0)
        self.assertNotIn("缺少贴图/弹窗包装", critic["issues"])

    def test_generated_sticker_request_is_dropped_without_high_confidence_local_match(self):
        with patch("app.services.agent_tools.load_assets", return_value={"stickers": []}):
            action = normalize_agent_overlay_action(
                {"type": "generated_sticker", "text": "随便贴个装饰"},
                "猫咪普通日常",
                {"role": "setup", "caption": "猫看了一眼镜头", "intent": ""},
            )

        self.assertIsNone(action)

    def test_agent_slot_normalization_drops_generated_sticker(self):
        actions = normalize_overlay_actions(
            [{"type": "generated_sticker", "text": "随便贴个装饰"}],
            "猫咪普通日常",
            {"role": "setup", "caption": "猫看了一眼镜头", "intent": ""},
        )

        self.assertEqual(actions, [])

    def test_make_overlay_frames_draws_sticker_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sticker_path = tmp_path / "sticker.png"
            Image.new("RGBA", (80, 60), (255, 0, 0, 255)).save(sticker_path)
            out_dir = tmp_path / "frames"
            actions = [
                {
                    "type": "sticker",
                    "file": str(sticker_path),
                    "start": 0,
                    "duration": 0.4,
                    "motion": "static",
                    "x": 120,
                    "y": 90,
                    "scale": 1.0,
                }
            ]

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "make-overlay-frames.py"),
                    "--actions",
                    json.dumps(actions),
                    "--out-dir",
                    str(out_dir),
                    "--duration",
                    "0.4",
                    "--fps",
                    "5",
                    "--width",
                    "320",
                    "--height",
                    "240",
                ],
                check=True,
                cwd=str(ROOT),
            )

            frame = Image.open(out_dir / "0001.png").convert("RGBA")
            self.assertIsNotNone(frame.getchannel("A").getbbox())
            self.assertEqual(
                frame.getchannel("A").getbbox(),
                Image.open(out_dir / "0002.png").convert("RGBA").getchannel("A").getbbox(),
            )

    def test_make_overlay_frames_draws_composite_sticker_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            phone_path = tmp_path / "phone.png"
            hand_path = tmp_path / "hand.png"
            Image.new("RGBA", (50, 80), (40, 90, 230, 255)).save(phone_path)
            Image.new("RGBA", (80, 50), (250, 210, 180, 255)).save(hand_path)
            out_dir = tmp_path / "frames"
            actions = [
                {
                    "type": "sticker",
                    "composite": "phone_in_hand",
                    "components": [
                        {"role": "phone", "file": str(phone_path), "x": 0, "y": -12, "scale": 0.95, "rotation": -4},
                        {"role": "hand", "file": str(hand_path), "x": -8, "y": 24, "scale": 0.9, "rotation": 8},
                    ],
                    "start": 0,
                    "duration": 0.4,
                    "motion": "static",
                    "x": 160,
                    "y": 120,
                    "scale": 1.0,
                }
            ]

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "make-overlay-frames.py"),
                    "--actions",
                    json.dumps(actions),
                    "--out-dir",
                    str(out_dir),
                    "--duration",
                    "0.4",
                    "--fps",
                    "5",
                    "--width",
                    "320",
                    "--height",
                    "240",
                ],
                check=True,
                cwd=str(ROOT),
            )

            frame = Image.open(out_dir / "0001.png").convert("RGBA")
            self.assertIsNotNone(frame.getchannel("A").getbbox())
            colors = frame.convert("RGB").getcolors(maxcolors=100000) or []
            self.assertTrue(any(color == (40, 90, 230) for _, color in colors))
            self.assertTrue(any(color == (250, 210, 180) for _, color in colors))

    def test_make_overlay_frames_crops_sticker_whitespace_before_scaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sticker_path = tmp_path / "padded-sticker.png"
            padded = Image.new("RGB", (300, 300), (255, 255, 255))
            for x in range(115, 185):
                for y in range(100, 200):
                    padded.putpixel((x, y), (20, 80, 220))
            padded.save(sticker_path)
            out_dir = tmp_path / "frames"
            actions = [
                {
                    "type": "sticker",
                    "file": str(sticker_path),
                    "start": 0,
                    "duration": 0.4,
                    "motion": "static",
                    "x": 160,
                    "y": 120,
                    "scale": 1.0,
                }
            ]

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "make-overlay-frames.py"),
                    "--actions",
                    json.dumps(actions),
                    "--out-dir",
                    str(out_dir),
                    "--duration",
                    "0.4",
                    "--fps",
                    "5",
                    "--width",
                    "320",
                    "--height",
                    "240",
                ],
                check=True,
                cwd=str(ROOT),
            )

            alpha_bbox = Image.open(out_dir / "0001.png").convert("RGBA").getchannel("A").getbbox()
            self.assertIsNotNone(alpha_bbox)
            self.assertGreaterEqual(alpha_bbox[2] - alpha_bbox[0], 90)
            self.assertGreaterEqual(alpha_bbox[3] - alpha_bbox[1], 120)


if __name__ == "__main__":
    unittest.main()
