from __future__ import annotations

import re
import unittest
import asyncio

from app.services.maomeme_agent import build_migration_blueprint, generate_script_candidates, motion_profile_for_context, plan_from_candidate, screenwriter_agent
from app.services.text_materials import topic_for_agent
from app.services.viral_structure_library import infer_theme_category, viral_references_for_theme


CHILDHOOD_BBQ_THEME = (
    "小时候家里开烧烤店，父亲手艺精湛、生意兴隆。"
    "主角趁父亲短暂离开时，偷偷拿最右边的2串烧烤，以为未被发现。"
    "多年后父亲提起，才知道最右边的2串其实是专门为他烤的，偷吃早被父亲默许。"
    "长大后拼搏百天，想起童年的贪吃和父亲无声的爱。"
)

FAMILY_SUPPORT_PRESSURE_THEME = (
    "小时候自以为偷吃烧烤瞒天过海，实则父亲早已知情默许；"
    "长大后以为独自扛升学就业压力，实则家人的支持一直都在"
)


class FamilyMemoryGenerationTest(unittest.TestCase):
    def test_childhood_father_bbq_theme_is_family_not_street_food_or_career(self):
        self.assertEqual(infer_theme_category(CHILDHOOD_BBQ_THEME), "family")

    def test_childhood_father_bbq_uses_family_references(self):
        refs = viral_references_for_theme(CHILDHOOD_BBQ_THEME, topic_for_agent(CHILDHOOD_BBQ_THEME))
        joined = " ".join(
            f"{ref.get('title', '')} {ref.get('topic', '')} {' '.join(ref.get('structure_tags', []))}"
            for ref in refs
        )

        self.assertIn("家庭", joined)
        self.assertNotRegex(joined, r"招聘|求职|岗位|HR|烤肠摊|摊位内卷|老干妈|室友")

    def test_childhood_father_bbq_screenwriter_keeps_father_twist(self):
        text_context = topic_for_agent(CHILDHOOD_BBQ_THEME)
        refs = viral_references_for_theme(CHILDHOOD_BBQ_THEME, text_context)
        blueprint = build_migration_blueprint(CHILDHOOD_BBQ_THEME, refs, {}, text_context)
        scripts = screenwriter_agent(CHILDHOOD_BBQ_THEME, text_context, refs, {}, blueprint)
        self.assertTrue(scripts)

        first = scripts[0]
        joined = " ".join(str(beat[1]) for beat in first.get("beats", []))
        self.assertRegex(joined, r"父亲|爸爸")
        self.assertRegex(joined, r"最右边|两串|2串")
        self.assertRegex(joined, r"默许|专门|留给")
        self.assertRegex(joined, r"长大|拼搏|百天|无声")
        self.assertIsNone(re.search(r"招聘|求职|岗位|简历|HR|三年经验|摊位费|老干妈|室友", joined))

    def test_childhood_father_bbq_storyboard_does_not_inherit_reference_scenes(self):
        async def run_case():
            candidates = await generate_script_candidates(CHILDHOOD_BBQ_THEME, use_doubao=False, duration_mode="short")
            plan = await plan_from_candidate(CHILDHOOD_BBQ_THEME, candidates[0], use_doubao=False, duration_mode="short")
            return candidates[0], plan

        candidate, plan = asyncio.run(run_case())
        hints = " ".join(str(item) for item in candidate.asset_hints.get("backgrounds", []))
        background_text = " ".join(
            f"{slot.background.id} {slot.background.description}"
            for slot in plan.timeline
        )

        self.assertNotRegex(hints, r"招聘|岗位|烤肠摊|摊位内卷|别墅|苹果|奔驰|4S")
        self.assertNotRegex(background_text, r"大学生工作难找|招聘|岗位|烤肠摊|摊位内卷|预算|账单|彩礼|买房|房贷")
        self.assertRegex(background_text, r"家庭|家里|温馨|小店|店铺|暖光|饭桌")

    def test_family_support_pressure_theme_does_not_load_job_hunt_facts(self):
        text_context = topic_for_agent(FAMILY_SUPPORT_PRESSURE_THEME)

        self.assertEqual(text_context.get("id"), "childhood_family_memory")
        self.assertNotRegex(" ".join(text_context.get("facts", [])), r"高校毕业生|1270万|1222万|简历|岗位")

    def test_family_support_pressure_medium_script_keeps_support_as_core_theme(self):
        async def run_case():
            return await generate_script_candidates(FAMILY_SUPPORT_PRESSURE_THEME, use_doubao=False, duration_mode="medium")

        candidates = asyncio.run(run_case())
        joined = " ".join(item.get("text", "") for item in candidates[0].script)
        note_text = " ".join(candidates[0].notes)

        self.assertEqual(len(candidates[0].script), 6)
        self.assertRegex(joined, r"小时候|偷|最右边|两串|烧烤")
        self.assertRegex(joined, r"父亲|家人")
        self.assertRegex(joined, r"升学就业|压力|自己扛")
        self.assertRegex(joined, r"支持|一直都在|兜底|无声的爱")
        self.assertNotRegex(joined, r"考研|考公|投简历|简历|岗位|招聘|HR|高校毕业生|千万级")
        self.assertNotRegex(note_text, r"高校毕业生|1270万|1222万")
        self.assertNotRegex(" ".join(candidates[0].asset_hints.get("keywords", [])), r"职场|求职|岗位|招聘|请假")

    def test_family_support_pressure_candidate_metadata_comes_from_theme_not_reference(self):
        async def run_case():
            return await generate_script_candidates(FAMILY_SUPPORT_PRESSURE_THEME, use_doubao=False, duration_mode="medium")

        candidates = asyncio.run(run_case())
        metadata = f"{candidates[0].social_topic} {candidates[0].tension}"

        self.assertIn("偷吃烧烤", metadata)
        self.assertIn("家人的支持", metadata)
        self.assertNotRegex(metadata, r"压岁钱|春节|妈妈微信|充气城堡|文本素材|高校毕业生|岗位|招聘")

    def test_motion_profile_is_derived_from_semantic_cues_not_only_fixed_category(self):
        profile = motion_profile_for_context(
            theme="第一次独自去外地复试，躲在候场区偷看观众，结束后终于松一口气",
            caption="我躲在幕布后偷看观众",
            role="setup",
        )

        preferred = " ".join(profile["prefer"])
        avoided = " ".join(profile["avoid"])
        self.assertRegex(preferred, r"偷看|探头|试探")
        self.assertRegex(preferred, r"安静|发呆|松一口气|休息")
        self.assertRegex(avoided, r"电脑|跳舞|香蕉猫")

    def test_family_motion_selection_prefers_quiet_memory_actions(self):
        async def run_case():
            candidates = await generate_script_candidates(CHILDHOOD_BBQ_THEME, use_doubao=False, duration_mode="medium")
            return await plan_from_candidate(CHILDHOOD_BBQ_THEME, candidates[0], use_doubao=False, duration_mode="medium")

        plan = asyncio.run(run_case())
        def motion_tag_text(slot):
            tags = getattr(slot.motion, "motion_tags", {}) or {}
            return " ".join(
                str(value)
                for values in tags.values()
                for value in (values if isinstance(values, list) else [values])
            )

        motion_text = " ".join(f"{slot.motion.id} {slot.motion.description} {motion_tag_text(slot)}" for slot in plan.timeline)
        motion_ids = [str(slot.motion.id) for slot in plan.timeline]
        setup_motion = next(motion_tag_text(slot) for slot in plan.timeline if slot.role == "setup")
        ending_motion = motion_tag_text(plan.timeline[-1])

        self.assertRegex(setup_motion, r"探头|偷看|试探|回头|安静|发呆")
        self.assertRegex(ending_motion, r"抱奶茶|发呆|休息|松一口气|安静|回头")
        self.assertLessEqual(max(motion_ids.count(item) for item in set(motion_ids)), 2)
        self.assertNotRegex(ending_motion, r"蹦跳|跳舞|庆祝|弹电子琴")
        self.assertNotRegex(motion_text, r"笔记本|敲电脑|香蕉猫|跳舞|弹电子琴")


if __name__ == "__main__":
    unittest.main()
