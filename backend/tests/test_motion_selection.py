from __future__ import annotations

import asyncio
import unittest

from app.services.asset_index import load_assets
from app.services.maomeme_agent import (
    blueprint_script_seeds,
    build_timeline_slot,
    choose_motion_for_beat,
    director_agent,
    generate_script_candidates,
    local_shot_critic,
    motion_allocation_plan,
    motion_quality_flags,
    motion_profile_for_context,
    motion_tags_for_asset,
    plan_from_candidate,
)


FAMILY_THEME = (
    "小时候家里开烧烤店，父亲转身招呼客人时，我偷偷拿最右边两串。"
    "多年后才知道那是父亲专门留给我的，长大后才懂家人的支持一直都在。"
)


class MotionSelectionTest(unittest.TestCase):
    def test_blueprint_script_seed_does_not_copy_reference_cat_actions_into_emotion_hints(self):
        blueprint = {
            "primary_reference": {"id": "ref", "title": "参考爆款", "structure_tags": ["家庭关系反转"]},
            "shots": [
                {
                    "slot": "hook",
                    "cat_action_requirement": "8只香蕉猫站后，主角cheems猫在前说话",
                    "transfer_role": "强 hook",
                },
                {
                    "slot": "setup",
                    "cat_action_requirement": "香蕉猫爸爸和主角站奔驰前",
                    "transfer_role": "铺垫",
                },
                {
                    "slot": "twist",
                    "cat_action_requirement": "蓝衣猫敲电脑",
                    "transfer_role": "反转",
                },
                {
                    "slot": "punchline",
                    "cat_action_requirement": "众香蕉猫欢呼表情",
                    "transfer_role": "收束",
                },
            ],
        }

        scripts = blueprint_script_seeds(FAMILY_THEME, blueprint, {"beat_seed": {}})

        self.assertTrue(scripts)
        self.assertNotRegex(" ".join(scripts[0].get("emotion", [])), r"香蕉猫|奔驰|敲电脑|欢呼")

    def test_structured_motion_tags_participate_in_matching(self):
        index = {
            "cat_motions": [
                {
                    "id": "quiet-peek",
                    "file": "quiet.mp4",
                    "description": "自定义猫素材，无描述关键词。",
                    "motion_tags": {
                        "actions": ["偷看", "探头"],
                        "emotions": ["安静", "试探"],
                        "contexts": ["回忆"],
                    },
                },
                {
                    "id": "computer",
                    "file": "computer.mp4",
                    "description": "蓝衣灰猫坐在笔记本前敲电脑。",
                    "motion_tags": {
                        "actions": ["敲电脑"],
                        "contexts": ["办公"],
                    },
                },
            ]
        }
        beat = {
            "id": "01-setup",
            "role": "setup",
            "theme": "童年回忆",
            "caption": "我躲在门后偷看",
            "intent": "试探怕被发现",
            "scene_keywords": [],
            "theme_keywords": [],
            "emotion_keywords": ["偷看"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "motion_profile": motion_profile_for_context("童年回忆", "我躲在门后偷看", "setup", "试探怕被发现"),
            "layout": "single",
            "dialogue": [],
        }

        self.assertIn("偷看", motion_tags_for_asset(index["cat_motions"][0]).get("actions", []))
        self.assertEqual(choose_motion_for_beat(index, beat).get("id"), "quiet-peek")

    def test_motion_matching_generalizes_to_new_theme_wording(self):
        index = load_assets()
        speech_beat = {
            "id": "01-setup",
            "role": "setup",
            "theme": "第一次上台路演，躲在幕布后偷看观众，结束后终于能喘口气",
            "caption": "我躲在幕布后偷看观众",
            "intent": "不是亲情或求职，而是紧张前的隐蔽观察",
            "scene_keywords": [],
            "theme_keywords": [],
            "emotion_keywords": ["隐蔽观察", "偷看", "紧张"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "motion_profile": motion_profile_for_context("第一次上台路演，躲在幕布后偷看观众", "我躲在幕布后偷看观众", "setup", "紧张前的隐蔽观察"),
            "layout": "single",
            "dialogue": [],
        }
        hospital_beat = {
            "id": "02-pressure",
            "role": "pressure",
            "theme": "在医院门口等检查报告，手心冒汗，想开口求助",
            "caption": "检查报告还没出来",
            "intent": "病痛等待和求助压力",
            "scene_keywords": [],
            "theme_keywords": [],
            "emotion_keywords": ["病痛求助", "等待结果", "求助"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "motion_profile": motion_profile_for_context("在医院门口等检查报告，手心冒汗，想开口求助", "检查报告还没出来", "pressure", "病痛等待和求助压力"),
            "layout": "single",
            "dialogue": [],
        }

        speech_motion = choose_motion_for_beat(index, speech_beat)
        hospital_motion = choose_motion_for_beat(index, hospital_beat)
        speech_tags = " ".join(
            value
            for values in motion_tags_for_asset(speech_motion).values()
            for value in values
        )
        hospital_tags = " ".join(
            value
            for values in motion_tags_for_asset(hospital_motion).values()
            for value in values
        )

        self.assertRegex(speech_tags, r"探头|偷看|试探")
        self.assertRegex(hospital_tags, r"委屈|求救|可怜|强忍|叫唤")
        self.assertNotRegex(hospital_tags, r"敲电脑|跳舞|弹电子琴")

    def test_motion_profile_handles_boundary_refusal_without_defaulting_to_computer(self):
        index = load_assets()
        beat = {
            "id": "03-twist",
            "role": "twist",
            "theme": "工作群连续弹会议通知，我假装没看见",
            "caption": "我把工作群静音了",
            "intent": "边界拒绝和免打扰反讽",
            "scene_keywords": [],
            "theme_keywords": [],
            "emotion_keywords": ["边界拒绝", "免打扰", "假装没看见"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "motion_profile": motion_profile_for_context(
                "工作群连续弹会议通知，我假装没看见",
                "我把工作群静音了",
                "twist",
                "边界拒绝和免打扰反讽",
            ),
            "layout": "single",
            "dialogue": [],
        }

        motion = choose_motion_for_beat(index, beat)
        tags = " ".join(
            value
            for values in motion_tags_for_asset(motion).values()
            for value in values
        )

        self.assertRegex(tags, r"摆手|假装没听见|拒绝|免打扰|边界")
        self.assertNotRegex(tags, r"敲电脑|笔记本")

    def test_structured_double_cat_tags_prevent_extra_secondary_motion(self):
        index = load_assets()
        motion = next(item for item in index["cat_motions"] if str(item.get("id")) == "16")

        self.assertTrue(motion_quality_flags(motion)["natural_double"])

        beat = {
            "id": "02-setup",
            "start": 2.0,
            "end": 5.0,
            "role": "setup",
            "theme": "朋友吐槽上周刚过618，这周又开始大促",
            "caption": "啥？上周不是刚过618？",
            "intent": "两只猫对话吐槽促销节奏",
            "scene_keywords": ["卧室", "窗边"],
            "theme_keywords": [],
            "emotion_keywords": ["双猫", "对话", "吐槽"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "allocated_motion_id": "16",
            "motion_profile": motion_profile_for_context(
                "朋友吐槽上周刚过618，这周又开始大促",
                "啥？上周不是刚过618？",
                "setup",
                "两只猫对话吐槽促销节奏",
            ),
            "layout": "dialogue",
            "dialogue": [
                {"speaker": "left", "text": "猫：这事不对劲"},
                {"speaker": "right", "text": "旁白猫：先别急"},
            ],
        }

        slot, _ = build_timeline_slot(beat, beat["theme"], index)

        self.assertEqual(slot["motion"]["id"], "16")
        self.assertIsNone(slot["secondary_motion"])
        self.assertIsNone(slot["secondary_motion_clip"])
        self.assertIn("内置双猫素材", slot["visual_summary"])
        self.assertIn("对话气泡", slot["visual_summary"])
        self.assertNotIn("右侧副猫", slot["visual_summary"])

    def test_motion_allocation_plan_diversifies_before_building_slots(self):
        index = load_assets()
        script = {
            "beats": [
                ("hook", "小时候我总偷最右边两串", "偷吃开场"),
                ("setup", "父亲转身招呼客人", "亲子铺垫"),
                ("pressure", "我以为自己瞒天过海", "孩子视角"),
                ("twist", "多年后才知道真相", "父亲默许"),
                ("proof", "长大后压力也想自己扛", "当下呼应"),
                ("punchline", "原来家人的支持一直都在", "温暖收束"),
            ],
            "scene": ["家庭饭桌", "小店"],
            "theme_keywords": ["童年", "父亲", "亲情", "偷吃"],
            "emotion": [],
        }
        beats = director_agent(script, FAMILY_THEME, "medium")

        plan = motion_allocation_plan(index, beats, FAMILY_THEME)
        motion_ids = list(plan.values())

        self.assertEqual(len(motion_ids), len(beats))
        self.assertLessEqual(max(motion_ids.count(item) for item in set(motion_ids)), 2)
        for motion_id in motion_ids:
            motion = next(item for item in index["cat_motions"] if str(item.get("id")) == str(motion_id))
            self.assertNotRegex(motion.get("description", ""), r"敲电脑|笔记本|香蕉猫|跳舞|弹电子琴")

    def test_local_critic_penalizes_story_semantic_mismatch_even_when_keyword_matches(self):
        beat = {
            "id": "01-setup",
            "role": "setup",
            "theme": FAMILY_THEME,
            "caption": "父亲转身招呼客人",
            "intent": "亲情回忆铺垫",
            "emotion_keywords": ["电脑", "父亲转身招呼客人"],
            "scene_keywords": [],
            "motion_profile": motion_profile_for_context(FAMILY_THEME, "父亲转身招呼客人", "setup", "亲情回忆铺垫"),
            "layout": "single",
        }
        slot = {
            "motion": {
                "id": "1",
                "description": "蓝衣灰猫坐在绿幕笔记本前敲电脑，表情木然。",
            },
            "background": {"id": "b", "description": "暖光小店"},
            "layout": "single",
            "dialogue": [],
            "overlay_actions": [],
        }

        result = local_shot_critic(FAMILY_THEME, beat, slot, set(), set())

        self.assertIn("motion_story_mismatch", result["issues"])
        self.assertLess(result["score"], 0.85)

    def test_family_plan_uses_allocated_quiet_motion_set(self):
        async def run_case():
            candidates = await generate_script_candidates(FAMILY_THEME, use_doubao=False, duration_mode="medium")
            return await plan_from_candidate(FAMILY_THEME, candidates[0], use_doubao=False, duration_mode="medium")

        plan = asyncio.run(run_case())
        motion_text = " ".join(slot.motion.description for slot in plan.timeline)
        motion_ids = [str(slot.motion.id) for slot in plan.timeline]

        self.assertLessEqual(max(motion_ids.count(item) for item in set(motion_ids)), 2)
        self.assertNotRegex(motion_text, r"敲电脑|笔记本|香蕉猫|跳舞|弹电子琴")


if __name__ == "__main__":
    unittest.main()
