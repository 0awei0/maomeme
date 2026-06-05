from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CatLayoutAnalysisTest(unittest.TestCase):
    def test_structured_motion_description_exposes_objective_text_and_tag_metadata(self):
        script_path = PROJECT_ROOT / "scripts/analyze-cat-layout.py"
        spec = importlib.util.spec_from_file_location("analyze_cat_layout", script_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        description, metadata = module.normalize_description_entry({
            "description": "眯眼猫半身站立，画面边缘有黑边。",
            "motion_tags": {
                "quality": ["needs_crop"],
                "contexts": ["边界拒绝"],
            },
        })

        self.assertEqual(description, "眯眼猫半身站立，画面边缘有黑边。")
        self.assertIn("needs_crop", metadata)
        self.assertIn("边界拒绝", metadata)


if __name__ == "__main__":
    unittest.main()
