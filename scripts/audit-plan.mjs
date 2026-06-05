import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const planPath = process.argv[2] || path.join(root, 'data/runs/latest-backend-plan.json');

const hardRules = [
  { when: ['电脑', '周会', '复盘'], expect: ['电脑', '笔记本', '碎碎念', '生无可恋'], forbid: ['开车', '方向盘'] },
  { when: ['会议'], expect: ['电脑', '笔记本', '碎碎念', '生无可恋', '震惊', '错愕'], forbid: ['开车', '方向盘'] },
  { when: ['开车', '堵车', '导航'], expect: ['开车', '方向盘'], forbid: ['电脑', '笔记本'] },
  { when: ['哭', '崩溃', '晚上还在会'], expect: ['哭', '委屈', '嚎啕', '疯狂'], forbid: [] },
  { when: ['加班'], expect: ['哭', '委屈', '嚎啕', '疯狂', '电脑'], forbid: [], skipIf: ['装可爱', '卖萌', '逃过', '下班'] },
  { when: ['装可爱', '卖萌', '逃过', '准点下班', '复活'], expect: ['可爱', '蹦跳', '欢快', '跳舞'], forbid: ['哭', '嚎啕'] }
];

const genericBadCopies = [
  { theme: ['工作', '简历', '就业', '岗位'], bad: ['请假去医院', '打120', '工伤', '冲食堂', '班主任', '裸贷'] },
  { theme: ['上班', '会议', '加班', '内卷'], bad: ['冲食堂', '班主任', '裸贷', '压岁钱'] },
  { theme: ['考研', '考公', '上岸'], bad: ['打120', '裸贷', '烤鸡腿', '压岁钱'] },
  { theme: ['烤肠', '摆摊', '夜市', '小吃'], bad: ['请假去医院', '打120', '班主任', '压岁钱'] }
];

function hits(text, words) {
  return words.some((word) => text.includes(word));
}

function auditSlot(slot) {
  const copy = slot.copy || slot.caption || '';
  const desc = `${slot.motion?.id || ''} ${slot.motion?.file || ''} ${slot.motion?.description || ''} ${motionTags(slot.motion)}`;
  const scene = `${slot.background?.id || ''} ${slot.background?.description || ''}`;
  const issues = [];
  for (const rule of hardRules) {
    if (!hits(copy, rule.when)) continue;
    if (rule.skipIf && hits(copy, rule.skipIf)) continue;
    if (rule.expect.length && !hits(desc, rule.expect)) {
      issues.push(`文案"${copy}"期望 ${rule.expect.join('/')}，但猫素材是"${desc}"`);
    }
    if (rule.forbid.length && hits(desc, rule.forbid)) {
      issues.push(`文案"${copy}"不应使用包含 ${rule.forbid.join('/')} 的猫素材："${desc}"`);
    }
  }
  if (hits(copy, ['会议', '电脑', '老板', '加班', '同步', '复盘']) && !hits(scene, ['办公室', '办公', '工位', '电脑', '会议室', '会议', '复盘', '同步'])) {
    issues.push(`办公剧情背景不够贴合："${scene}"`);
  }
  return issues;
}

function flattenMetadata(value) {
  if (Array.isArray(value)) return value.flatMap((item) => flattenMetadata(item));
  if (value && typeof value === 'object') return Object.values(value).flatMap((item) => flattenMetadata(item));
  const text = String(value || '').trim();
  return text ? [text] : [];
}

function motionTags(motion) {
  const tags = flattenMetadata(motion?.motion_tags || {});
  if (tags.length) return tags.join(' ');
  return {
    '1': '电脑 笔记本 办公 工位',
    '2': '可爱 蹦跳 欢快',
    '9': '哭 嚎啕 崩溃 高压崩溃',
    '10': '探头 偷看 试探 隐蔽观察',
    '13': '跳舞 欢快 蹦跳 可爱',
    '14': '摆手 假装没听见 拒绝 免打扰 边界拒绝',
    '15': '震惊 瞪眼 瞳孔地震',
    '16': '双猫 对话 探头 碎碎念',
    '18': '嫌弃 无语 冷眼',
    '26': '委屈 可怜 强忍 病痛求助'
  }[String(motion?.id || '')] || '';
}

async function main() {
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  const allIssues = [];
  const theme = plan.theme || '';
  const planText = JSON.stringify({
    script: plan.script || [],
    timeline: plan.timeline || [],
    material_needs: plan.material_needs || {}
  }, null, 2);
  if (!Array.isArray(plan.agent_notes) || !plan.agent_notes.some((note) => String(note).includes('爆款结构参考'))) {
    allIssues.push({ slot: 'plan', issue: '缺少爆款结构参考记录，说明新结构库没有接入生成链路' });
  }
  if (!Array.isArray(plan.timeline) || !plan.timeline.some((slot) => String(slot.source_pattern || '').includes('爆款参考'))) {
    allIssues.push({ slot: 'plan', issue: 'timeline.source_pattern 未标注具体爆款参考' });
  }
  for (const rule of genericBadCopies) {
    if (!hits(theme, rule.theme)) continue;
    for (const bad of rule.bad) {
      if (planText.includes(bad)) {
        allIssues.push({ slot: 'plan', issue: `主题"${theme}"疑似照搬不相关爆款情节："${bad}"` });
      }
    }
  }
  for (const slot of plan.timeline || []) {
    for (const issue of auditSlot(slot)) {
      allIssues.push({ slot: slot.id, issue });
    }
  }
  const score = Math.max(0, 100 - allIssues.length * 18);
  const report = {
    plan: path.relative(root, planPath),
    score,
    status: allIssues.length ? 'warn' : 'pass',
    issues: allIssues
  };
  const out = path.join(root, 'data/runs/latest-audit.json');
  await fs.writeFile(out, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (allIssues.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
