import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const assets = JSON.parse(await fs.readFile(path.join(root, 'data/assets-index.json'), 'utf8'));
const planDir = path.join(root, 'data/runs/examples');
const videoDir = path.join(root, 'output/examples');

const motionById = new Map(assets.cat_motions.map((item) => [String(item.id), item]));
const bgById = new Map(assets.backgrounds.map((item) => [String(item.id), item]));

const examples = [
  {
    id: 'graduate-job-hunt',
    title: '大学生找工作像闯关',
    theme: '大学生工作难找，投简历像进黑洞，岗位要求越来越离谱。',
    background: ['real_school/1', 'office/1', 'real_office/1', 'city/1'],
    beats: [
      ['hook', '打开招聘软件那一秒', '岗位很多，我一个也不敢点', '16', 6.5],
      ['setup', '投了100份简历', '已读不回 x100', '1', 7.0],
      ['escalation', '实习生要三年经验', '猫猫开始怀疑时间线', '15', 7.5],
      ['escalation', '面试排到下周', '排队像抢演唱会票', '26', 7.0],
      ['twist', '终于收到回复', '谢谢参与，下次再来', '9', 7.5],
      ['proof', '同学说他也一样', '原来不是我一只猫', '16', 7.0],
      ['punchline', '猫去应聘公司门口的猫', '至少包吃包住', '2', 8.0],
      ['cta', '明天继续投', '猫猫先充个电', '13', 7.5]
    ]
  },
  {
    id: 'workplace-involution',
    title: '打工猫的一天被会议吃掉',
    theme: '上班内卷，会议越来越多，下班后还要在线待命。',
    background: ['office/1', 'real_office/1', 'office/5', 'window/1'],
    beats: [
      ['hook', '老板说简单聊两句', '猫的警报响了', '16', 6.5],
      ['setup', '9点周会 10点复盘', '电脑刚开，人已离线', '1', 7.0],
      ['escalation', '11点对齐对齐方式', '这会还能再会', '18', 7.0],
      ['escalation', '下午继续同步进展', '进展：还在同步', '9', 7.5],
      ['twist', '下班后一句在吗', '猫猫假装没网', '10', 7.0],
      ['proof', 'PPT改到第18版', '标题还没定', '1', 7.0],
      ['punchline', '猫开始卖萌求放过', '老板：今天先这样', '2', 8.0],
      ['cta', '准点下班像中了大奖', '猫猫复活', '13', 7.5]
    ]
  },
  {
    id: 'exam-or-job',
    title: '毕业猫的三岔路口',
    theme: '考研考公还是就业，毕业生每条路都很挤。',
    background: ['school/1', 'classroom/1', 'real_school/1', 'window/1'],
    beats: [
      ['hook', '毕业前最后一个夜晚', '三条路同时弹窗', '15', 6.5],
      ['setup', '左边考研 右边考公', '中间还要投简历', '16', 7.5],
      ['escalation', '每条路都在排队', '猫的脑袋开始转圈', '3', 7.0],
      ['escalation', '家族群发来上岸攻略', '猫猫压力拉满', '9', 7.5],
      ['twist', '同学已经签约了', '猫开始沉默', '18', 7.0],
      ['proof', '选择本身比考试还难', '这题没有标准答案', '26', 7.0],
      ['punchline', '猫先把闹钟关了', '明天再做重大决定', '10', 7.5],
      ['cta', '先活过今天', '猫猫也算上岸', '2', 7.0]
    ]
  }
];

await fs.mkdir(planDir, { recursive: true });
await fs.mkdir(videoDir, { recursive: true });

for (const example of examples) {
  const plan = buildPlan(example);
  const planPath = path.join(planDir, `${example.id}.json`);
  const outPath = path.join(videoDir, `${example.id}.mp4`);
  await fs.writeFile(planPath, JSON.stringify(plan, null, 2));
  await execFileAsync('node', [
    path.join(root, 'scripts/render-demo-video.mjs'),
    '--plan',
    planPath,
    '--output',
    outPath
  ], { cwd: root, maxBuffer: 1024 * 1024 * 20 });
  const info = await ffprobe(outPath);
  console.log(`${example.id}: ${info.duration}s ${Math.round(info.size / 1024)}KB -> ${path.relative(root, outPath)}`);
}

function buildPlan(example) {
  let t = 0;
  const timeline = example.beats.map(([role, copy, subtitle, motionId, duration], index) => {
    const start = Number(t.toFixed(2));
    t += duration;
    const end = Number(t.toFixed(2));
    return {
      id: `${String(index + 1).padStart(2, '0')}-${role}`,
      start,
      end,
      role,
      intent: subtitle,
      copy,
      subtitle,
      motion: ref(motionById.get(String(motionId)) || motionById.get('2')),
      background: ref(bgById.get(example.background[index % example.background.length]) || bgById.get('office/1')),
      gap: { status: 'matched', strategy: 'direct_match', reason: '示例脚本按素材动作和背景人工校准。' },
      packaging: role === 'hook' || role === 'escalation' ? ['large_caption', 'quick_cut'] : ['bottom_subtitle', role === 'cta' ? 'freeze_end' : 'quick_cut'],
      source_pattern: '一分钟社会现实猫 meme 示例'
    };
  });
  return {
    id: example.id,
    theme: example.theme,
    title: example.title,
    source_structure: { sample_status: 'curated_minute_example' },
    script: timeline.map((slot) => ({ type: slot.role, text: slot.copy, purpose: slot.intent, duration: Number((slot.end - slot.start).toFixed(2)) })),
    timeline,
    material_needs: { covered: timeline.map((slot) => slot.role), missing: [], supplement_strategy: [] },
    agent_notes: ['一分钟示例脚本由文本素材库主题扩展，并按本地猫动作/背景校准。']
  };
}

function ref(asset) {
  return {
    id: String(asset.id || ''),
    file: String(asset.file || ''),
    description: String(asset.description || '')
  };
}

async function ffprobe(file) {
  const { stdout } = await execFileAsync('ffprobe', [
    '-v', 'error',
    '-show_entries', 'format=duration,size',
    '-of', 'json',
    file
  ]);
  const parsed = JSON.parse(stdout);
  return {
    duration: Number(parsed.format?.duration || 0).toFixed(1),
    size: Number(parsed.format?.size || 0)
  };
}
