from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.services.asset_index import assets_summary, load_assets, ref


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CatMotionDescriptionsTest(unittest.TestCase):
    def test_descriptions_are_structured_for_motion_matching(self):
        data = json.loads((PROJECT_ROOT / "assets/cat-motions/descriptions.json").read_text(encoding="utf-8"))

        for motion_id in ("1", "10", "11", "23"):
            item = data[motion_id]
            self.assertIsInstance(item, dict)
            self.assertIsInstance(item.get("description"), str)
            self.assertIsInstance(item.get("motion_tags"), dict)
            self.assertTrue(item["motion_tags"].get("actions") or item["motion_tags"].get("emotions"))

        self.assertIn("敲电脑", data["1"]["motion_tags"]["actions"])
        self.assertIn("办公", data["1"]["motion_tags"]["contexts"])
        self.assertIn("偷看", data["10"]["motion_tags"]["actions"])
        self.assertIn("回忆", data["10"]["motion_tags"]["contexts"])
        self.assertIn("回头", data["11"]["motion_tags"]["actions"])
        self.assertIn("发呆", data["23"]["motion_tags"]["actions"])
        self.assertIn("温暖收束", data["23"]["motion_tags"]["contexts"])

    def test_descriptions_cover_general_semantic_buckets(self):
        data = json.loads((PROJECT_ROOT / "assets/cat-motions/descriptions.json").read_text(encoding="utf-8"))
        contexts = {
            value
            for item in data.values()
            for value in item.get("motion_tags", {}).get("contexts", [])
        }

        for bucket in (
            "隐蔽观察",
            "认知转折",
            "温暖收束",
            "桌面操作",
            "交通通勤",
            "对话反差",
            "病痛求助",
            "高压崩溃",
            "边界拒绝",
            "荒诞失控",
        ):
            self.assertIn(bucket, contexts)

    def test_descriptions_are_objective_and_tags_hold_fit_signals(self):
        data = json.loads((PROJECT_ROOT / "assets/cat-motions/descriptions.json").read_text(encoding="utf-8"))
        forbidden_description_terms = (
            "适合",
            "不适合",
            "仅适合",
            "默认避用",
            "应避用",
            "避免用于",
            "需裁切",
            "需要裁切",
            "渲染时",
        )

        for motion_id, item in data.items():
            description = item.get("description", "")
            with self.subTest(motion_id=motion_id):
                self.assertFalse(
                    any(term in description for term in forbidden_description_terms),
                    description,
                )
                tags = item.get("motion_tags", {})
                self.assertIsInstance(tags.get("contexts", []), list)
                self.assertIsInstance(tags.get("avoid", []), list)

    def test_asset_index_preserves_motion_tags_from_descriptions(self):
        index = load_assets()
        by_id = {str(item.get("id")): item for item in index.get("cat_motions", [])}

        self.assertIn("motion_tags", by_id["10"])
        self.assertIn("偷看", by_id["10"]["motion_tags"]["actions"])
        self.assertIn("回忆", by_id["10"]["motion_tags"]["contexts"])
        self.assertIn("motion_tags", by_id["23"])
        self.assertIn("温暖收束", by_id["23"]["motion_tags"]["contexts"])

    def test_assets_summary_keeps_visual_description_and_tags_separate(self):
        summary = assets_summary({
            "cat_motions": [
                {
                    "id": "10",
                    "file": "assets/cat-motions/10.mp4",
                    "description": "虎斑猫从画面侧边慢慢探头，身体只露出一部分。",
                    "motion_tags": {
                        "actions": ["探头", "偷看"],
                        "contexts": ["隐蔽观察", "等待结果"],
                        "avoid": ["强庆祝"],
                    },
                }
            ],
            "backgrounds": [],
            "stickers": [],
        })

        self.assertIn("虎斑猫从画面侧边慢慢探头，身体只露出一部分。", summary)
        self.assertIn("标签: 探头 / 偷看 / 隐蔽观察 / 等待结果", summary)
        self.assertNotIn("适合", summary)

    def test_asset_refs_preserve_motion_tags_for_agent_tools(self):
        asset = {
            "id": "10",
            "file": "assets/cat-motions/10.mp4",
            "description": "虎斑猫从画面侧边慢慢探头，身体只露出一部分。",
            "motion_tags": {
                "actions": ["探头", "偷看"],
                "contexts": ["隐蔽观察"],
            },
        }

        self.assertEqual(ref(asset).get("motion_tags"), asset["motion_tags"])


if __name__ == "__main__":
    unittest.main()
