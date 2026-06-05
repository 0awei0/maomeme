from __future__ import annotations

import json
import unittest

from app.services.asset_index import load_assets
from app.services.maomeme_agent import build_timeline_slot, dialogue_for_beat, screenwriter_agent
from app.services.viral_structure_library import build_migration_blueprint


EMOTIONAL_RELATIONSHIP_THEME = (
    "情侣吵架的本质：脑回路根本对不上。"
    "女生在意情绪价值输出，男生执着解决具体问题，双方完全不在沟通频道。"
)

NIGHT_MARKET_RELATIONSHIP_THEME = (
    "情侣在夜市刚和摊主争执完，女生希望男友站在自己这边、先安慰她，"
    "男生只顾着解释价格没算错，还递来加辣烤肠。核心是情绪价值和沟通错位。"
)


class RelationshipStoryboardTest(unittest.TestCase):
    def test_emotional_relationship_slot_does_not_inherit_bill_overlays_or_rental_background(self):
        index = load_assets()
        beat = {
            "id": "03-escalation",
            "start": 5.0,
            "end": 8.4,
            "role": "escalation",
            "theme": EMOTIONAL_RELATIONSHIP_THEME,
            "caption": "我要的是态度！你问那么多...",
            "intent": "强化情侣沟通频道错位，不是金钱账单冲突",
            "scene_keywords": [
                "generated/preset-rental-bill-room/1780413047.png",
                "出租屋账单桌面",
                "账单",
            ],
            "theme_keywords": ["情侣", "情绪价值", "沟通频道"],
            "emotion_keywords": ["委屈", "对话反差", "强反应"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "layout": "single",
            "dialogue": [],
        }

        slot, _ = build_timeline_slot(beat, EMOTIONAL_RELATIONSHIP_THEME, index)
        overlay_text = json.dumps(slot["overlay_actions"], ensure_ascii=False)
        background_text = f"{slot['background'].get('id', '')} {slot['background'].get('description', '')}"

        self.assertNotRegex(overlay_text, r"bill_card|bill_stack|账单|现实账")
        self.assertNotRegex(background_text, r"rental-bill|rental_bill|账单|房租|押金")

    def test_financial_relationship_slot_can_still_use_bill_overlay(self):
        index = load_assets()
        theme = "情侣准备结婚，彩礼、买房首付和共同预算谈不拢"
        beat = {
            "id": "02-pressure",
            "start": 3.0,
            "end": 6.0,
            "role": "pressure",
            "theme": theme,
            "caption": "彩礼和首付都摆在桌上",
            "intent": "现实财务压力具体化",
            "scene_keywords": ["室内", "账单"],
            "theme_keywords": ["情侣", "彩礼", "买房", "预算"],
            "emotion_keywords": ["委屈", "压力"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "layout": "single",
            "dialogue": [],
        }

        slot, _ = build_timeline_slot(beat, theme, index)
        overlay_text = json.dumps(slot["overlay_actions"], ensure_ascii=False)

        self.assertRegex(overlay_text, r"bill_card|现实账")

    def test_emotional_relationship_blueprint_does_not_use_bill_transfer_or_source_background(self):
        references = [
            {
                "id": "ref-rent",
                "title": "租房账单爆款",
                "storyboard": [
                    {
                        "shot_id": "3",
                        "beat": "pressure",
                        "background": "出租屋账单桌面",
                        "joke_point": "账单比猫先到",
                    }
                ],
            }
        ]

        blueprint = build_migration_blueprint(EMOTIONAL_RELATIONSHIP_THEME, references, {}, {})
        shot_text = json.dumps(
            [
                {
                    "slot": shot.get("slot"),
                    "transfer_role": shot.get("transfer_role"),
                    "background_requirement": shot.get("background_requirement"),
                }
                for shot in blueprint.get("shots", [])
            ],
            ensure_ascii=False,
        )

        self.assertNotRegex(shot_text, r"账单|房租|出租屋|预算")
        self.assertRegex(shot_text, r"情绪|沟通|对话|室内")

    def test_night_market_relationship_dialogue_does_not_become_stall_competition(self):
        dialogue = dialogue_for_beat(
            "setup",
            "（夜市刚和摊主争执完）",
            NIGHT_MARKET_RELATIONSHIP_THEME,
            "女生要的是被站队和安慰，男生还在解释具体问题",
        )
        dialogue_text = json.dumps(dialogue, ensure_ascii=False)

        self.assertRegex(dialogue_text, r"态度|安慰|站|沟通|频道|想办法")
        self.assertNotRegex(dialogue_text, r"买一送一|今天卖烤肠|摊位费|隔壁|今日特价")

    def test_night_market_relationship_storyboard_uses_relationship_packaging_not_stall_promo(self):
        index = load_assets()
        beat = {
            "id": "01-setup",
            "start": 0.0,
            "end": 3.8,
            "role": "setup",
            "theme": NIGHT_MARKET_RELATIONSHIP_THEME,
            "caption": "（夜市刚和摊主争执完）",
            "intent": "情侣刚发生摊主争执后的情绪沟通错位",
            "scene_keywords": ["夜市", "摊主", "烤肠摊"],
            "theme_keywords": ["情侣", "情绪价值", "沟通错位"],
            "emotion_keywords": ["紧张", "委屈", "对话反差"],
            "must_keywords": [],
            "forbidden_keywords": [],
            "layout": "dialogue",
            "dialogue": dialogue_for_beat("setup", "（夜市刚和摊主争执完）", NIGHT_MARKET_RELATIONSHIP_THEME, "女生要男友站队"),
        }

        slot, _ = build_timeline_slot(beat, NIGHT_MARKET_RELATIONSHIP_THEME, index)
        slot_text = json.dumps(
            {
                "dialogue": slot["dialogue"],
                "overlay_actions": slot["overlay_actions"],
                "background": slot["background"],
            },
            ensure_ascii=False,
        )

        self.assertRegex(slot_text, r"沟通频道|态度|安慰|站|想办法")
        self.assertNotRegex(slot_text, r"stall_sign|买一送一|今天卖烤肠|摊位费|隔壁|今日特价|大学生工作难找|摊位内卷")

    def test_night_market_relationship_screenwriter_does_not_use_stall_involution_template(self):
        scripts = screenwriter_agent(NIGHT_MARKET_RELATIONSHIP_THEME, {}, [], {}, {})
        joined = json.dumps(scripts[:1], ensure_ascii=False)

        self.assertRegex(joined, r"情侣|情绪|沟通|安慰|态度|站")
        self.assertNotRegex(joined, r"买一送一|摊位费|隔壁又降|校门口烤肠开张|小摊也内卷")


if __name__ == "__main__":
    unittest.main()
