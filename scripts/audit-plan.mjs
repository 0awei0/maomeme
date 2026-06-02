import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const planPath = process.argv[2] || path.join(root, 'data/runs/latest-backend-plan.json');

const hardRules = [
  { when: ['电脑', '会议', '周会', '复盘'], expect: ['电脑', '笔记本', '碎碎念', '生无可恋'], forbid: ['开车', '方向盘'] },
  { when: ['开车', '堵车', '导航'], expect: ['开车', '方向盘'], forbid: ['电脑', '笔记本'] },
  { when: ['哭', '崩溃', '晚上还在会'], expect: ['哭', '委屈', '嚎啕', '疯狂'], forbid: [] },
  { when: ['加班'], expect: ['哭', '委屈', '嚎啕', '疯狂', '电脑'], forbid: [], skipIf: ['装可爱', '卖萌', '逃过', '下班'] },
  { when: ['装可爱', '卖萌', '逃过', '下班'], expect: ['可爱', '蹦跳', '欢快', '跳舞'], forbid: ['哭', '嚎啕'] }
];

function hits(text, words) {
  return words.some((word) => text.includes(word));
}

function auditSlot(slot) {
  const copy = slot.copy || slot.caption || '';
  const desc = slot.motion?.description || '';
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
  if (hits(copy, ['会议', '电脑', '老板', '加班']) && !hits(scene, ['办公室', '办公', '工位', '电脑'])) {
    issues.push(`办公剧情背景不够贴合："${scene}"`);
  }
  return issues;
}

async function main() {
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  const allIssues = [];
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
