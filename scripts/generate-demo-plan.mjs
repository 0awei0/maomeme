import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const theme = process.argv.slice(2).join(' ') || '一只打工猫周一早上打开电脑，发现会议从 9 点排到晚上，最后靠装可爱逃过加班。';

function pickMotion(index, keywords, fallbackId) {
  return index.cat_motions.find((asset) => keywords.some((word) => asset.description.includes(word)))
    ?? index.cat_motions.find((asset) => asset.id === fallbackId)
    ?? index.cat_motions[0];
}

function pickBackground(index, scene, fallbackScene) {
  return index.backgrounds.find((asset) => asset.scene === scene)
    ?? index.backgrounds.find((asset) => asset.scene === fallbackScene)
    ?? index.backgrounds[0];
}

async function main() {
  const indexPath = path.join(root, 'data/assets-index.json');
  const index = JSON.parse(await fs.readFile(indexPath, 'utf8'));

  const plan = {
    id: `demo-${Date.now()}`,
    theme,
    source_structure: {
      sample_status: 'template_until_viral_samples_arrive',
      script_structure: ['强 hook', '场景化冲突', '情绪升级', '反转收束'],
      rhythm_structure: ['2 秒内给表情点', '中段 2-3 秒一切', '高潮使用重复/放大', '结尾冻结字幕'],
      packaging_structure: ['大字标题', '底部吐槽字幕', '高潮贴纸/放大', '结尾标题条']
    },
    timeline: []
  };

  const slots = [
    {
      id: 'hook',
      start: 0,
      end: 2.2,
      role: 'hook',
      intent: '先用强表情让观众停住',
      copy: '周一早上打开电脑的我',
      motion: pickMotion(index, ['震惊', '瞪圆', '错愕'], '19'),
      background: pickBackground(index, 'office', 'real_office'),
      gap: { status: 'matched', strategy: 'direct_match', reason: '素材库有震惊猫和办公室背景' }
    },
    {
      id: 'setup',
      start: 2.2,
      end: 5.1,
      role: 'setup',
      intent: '把主题落到打工场景',
      copy: '9:00 周会｜10:00 复盘｜11:00 对齐',
      motion: pickMotion(index, ['电脑', '笔记本'], '1'),
      background: pickBackground(index, 'real_office', 'office'),
      gap: { status: 'matched', strategy: 'direct_match', reason: '打电脑猫可直接承接工作主题' }
    },
    {
      id: 'escalation',
      start: 5.1,
      end: 8.4,
      role: 'escalation',
      intent: '用夸张情绪制造笑点',
      copy: '下午：继续开会。晚上：还是开会。',
      motion: pickMotion(index, ['哭', '委屈', '可怜'], '9'),
      background: pickBackground(index, 'office', 'real_office'),
      gap: { status: 'supplemented', strategy: 'reuse_crop_zoom', reason: '缺少真实会议镜头，用哭哭猫重复放大补强情绪' }
    },
    {
      id: 'punchline',
      start: 8.4,
      end: 12.0,
      role: 'punchline',
      intent: '用反转和记忆点收束',
      copy: '老板：算了，你先下班吧',
      motion: pickMotion(index, ['蹦跳', '欢快', '跳舞'], '13'),
      background: pickBackground(index, 'window', 'city'),
      gap: { status: 'supplemented', strategy: 'subtitle_card', reason: '缺少老板出镜，用字幕卡表达对话反转' }
    }
  ];

  plan.timeline = slots.map((slot) => ({
    ...slot,
    motion: { id: slot.motion.id, file: slot.motion.file, description: slot.motion.description },
    background: { id: slot.background.id, file: slot.background.file, description: slot.background.description },
    packaging: ['large_caption', 'bottom_subtitle', slot.id === 'punchline' ? 'freeze_end' : 'quick_cut']
  }));

  await fs.mkdir(path.join(root, 'data/runs'), { recursive: true });
  await fs.writeFile(path.join(root, 'data/runs/latest-plan.json'), JSON.stringify(plan, null, 2));
  console.log(`Generated plan: data/runs/latest-plan.json`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
