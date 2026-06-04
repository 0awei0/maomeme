import fsSync from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const hyperframesDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(hyperframesDir, '..');
const args = parseArgs(process.argv.slice(2));

async function main() {
  if (!args.plan || !args.output) {
    throw new Error('Usage: node hyperframes/render.mjs --plan <plan.json> --output <video.mp4>');
  }
  const plan = JSON.parse(await fs.readFile(args.plan, 'utf8'));
  const manifest = buildManifest(plan);
  const manifestDir = path.join(root, 'backend', 'outputs', 'hyperframes');
  await fs.mkdir(manifestDir, { recursive: true });
  const manifestPath = path.join(manifestDir, `${path.basename(args.output, '.mp4')}.json`);
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2));

  await execFileAsync('node', [
    path.join(root, 'scripts', 'render-demo-video.mjs'),
    '--plan',
    args.plan,
    '--output',
    args.output,
    '--hyperframes-manifest',
    manifestPath,
  ], {
    cwd: root,
    maxBuffer: 1024 * 1024 * 20,
  });
  console.log(`HyperFrames rendered ${path.relative(root, args.output)}`);
}

function buildManifest(plan) {
  const presets = loadPackagingPresetsSync();
  return {
    engine: 'maomeme-hyperframes',
    version: 1,
    plan_id: plan.id,
    theme: plan.theme,
    frame: { width: 960, height: 544, fps: 30 },
    packaging_presets: presets.map(({ id, title, visuals, recommended_transitions }) => ({
      id,
      title,
      visuals,
      recommended_transitions,
    })),
    timeline: (plan.timeline || []).map((slot, index) => {
      const preset = choosePreset(plan.theme, slot, presets);
      return {
        index,
        id: slot.id,
        time: { start: slot.start, end: slot.end },
        caption: slot.copy || slot.caption || '',
        packaging_preset: preset?.id || 'default-cat-meme',
        layout: slot.layout || 'single',
        transition: slot.transition || { type: 'cut', duration: 0 },
        dialogue: slot.dialogue || [],
        overlay_actions: mergeOverlayActions(overlayActionInputs(plan.theme, slot, preset)),
        packaging: slot.packaging || [],
        motion_clip: slot.motion_clip || {},
        motion_quality: slot.motion_quality || {},
        secondary_motion_clip: slot.secondary_motion_clip || null,
        secondary_motion_quality: slot.secondary_motion_quality || {},
        background_source: slot.background_source || 'matched',
        hyperframe_role: semanticRole(plan.theme, slot, preset),
      };
    }),
  };
}

function overlayActionInputs(theme, slot, preset) {
  const agentActions = Array.isArray(slot.overlay_actions) ? slot.overlay_actions : [];
  if (agentActions.length >= 2) return agentActions;
  if (agentActions.length === 1) {
    return [
      ...agentActions,
      ...semanticOverlayActions(theme, slot, preset),
    ];
  }
  if (!slotShouldReceivePresetOverlay(theme, slot)) {
    return [];
  }
  return [
    ...semanticOverlayActions(theme, slot, preset),
    ...(preset?.default_overlay_actions || []),
  ];
}

function slotShouldReceivePresetOverlay(theme, slot) {
  const role = slot.role || '';
  if (['hook', 'pressure', 'twist', 'escalation', 'punchline'].includes(role)) return true;
  const text = searchableText(theme, slot);
  return /(离谱|突然|老板|请假|120|急救|岗位|要求|烤肠|摆摊|周一|闹钟|会议|加班|已读|不回)/.test(text);
}

function loadPackagingPresetsSync() {
  try {
    const file = path.join(hyperframesDir, 'templates', 'packaging-presets.json');
    const data = JSON.parse(fsSync.readFileSync(file, 'utf8'));
    return Array.isArray(data.presets) ? data.presets : [];
  } catch {
    return [];
  }
}

function choosePreset(theme, slot, presets) {
  const primaryText = [
    theme,
    slot.copy || slot.caption || '',
    slot.intent || '',
    ...(slot.dialogue || []).map((line) => line.text || ''),
  ].join(' ');
  const secondaryText = [
    slot.background?.description || '',
    slot.motion?.description || '',
  ].join(' ');
  let best = null;
  for (const preset of presets) {
    const score = (preset.triggers || []).reduce((total, trigger) => {
      if (!trigger) return total;
      return total + (primaryText.includes(trigger) ? 3 : 0) + (secondaryText.includes(trigger) ? 1 : 0);
    }, 0);
    if (score && (!best || score > best.score)) best = { score, preset };
  }
  return best ? best.preset : null;
}

function semanticOverlayActions(theme, slot, preset) {
  const slotText = primarySlotText(slot);
  const categoryText = `${theme} ${slotText}`;
  const text = `${categoryText} ${slot.background?.description || ''} ${slot.motion?.description || ''}`;
  const caption = slot.copy || slot.caption || '';
  const duration = Math.max(1, Number(slot.end || 0) - Number(slot.start || 0));
  const longEnough = Math.max(1.2, Math.min(duration - 0.25, 2.6));

  if (/(120|急救|救护车)/.test(categoryText)) {
    return [{
      type: 'emergency_call',
      start: 0.25,
      duration: longEnough,
      title: '急救电话',
      caller: '00后猫',
      status: '老板已沉默',
    }];
  }

  if (/(请假|不批准|不批假|病假|审批)/.test(categoryText)) {
    return [{
      type: 'leave_request',
      start: 0.28,
      duration: longEnough,
      title: '请假审批',
      reason: '身体报警',
      status: '老板：不批准',
    }];
  }

  if (/(烤肠|香肠|摆摊|小吃摊|夜市|地摊|摊位|冰粉)/.test(categoryText)) {
    return [{
      type: 'stall_sign',
      start: 0.3,
      duration: longEnough,
      title: /(夜市|地摊)/.test(categoryText) ? '夜市小摊' : '校门口小摊',
      items: stallItems(categoryText, slot.role),
    }];
  }

  if (/(房租|押金|房贷|账单|预算|租房|合租|中介|通勤)/.test(categoryText)) {
    if (/(通勤|地铁|公交|站台)/.test(categoryText) && ['pressure', 'twist', 'echo'].includes(slot.role)) {
      return [{
        type: 'commute_card',
        start: 0.3,
        duration: longEnough,
        title: '通勤账单',
        items: ['早八地铁', '单程 2h', '咖啡续命'],
      }];
    }
    return [{
      type: 'bill_card',
      start: 0.32,
      duration: longEnough,
      title: '现实账单',
      items: billItems(categoryText),
    }];
  }

  if (/(考研|考公|上岸|自习|考试|二战|三战|选择|刷题|申论)/.test(categoryText)) {
    if (/(资料|刷题|倒计时|复习|家族群)/.test(categoryText) || ['pressure', 'proof', 'escalation'].includes(slot.role)) {
      return [{
        type: 'study_card',
        start: 0.3,
        duration: longEnough,
        title: '今日复习',
        items: studyItems(categoryText),
      }];
    }
    return [{
      type: 'choice_panel',
      start: 0.3,
      duration: longEnough,
      title: '请选择今天焦虑',
      options: examOptions(categoryText),
    }];
  }

  if (/(会议|加班|复盘|同步|老板|KPI|PPT|周会|下班|在线待命)/.test(categoryText)) {
    return [{
      type: 'work_chat_stack',
      start: 0.28,
      duration: longEnough,
      title: '工作群',
      messages: officeMessages(categoryText),
    }];
  }

  if (isJobHunt(categoryText, preset)) {
    if (/(投|简历|已读|不回|拒|HR|面试|黑洞|沟通)/i.test(caption)) {
      return [{
        type: 'chat_stack',
        start: 0.35,
        duration: longEnough,
        title: '招聘消息',
        messages: jobMessages(slotText),
      }];
    }
    if (/(要求|经验|全栈|团队|三年|3年|5年|门槛|简章|应届生要|要有|黑话|规则|翻译)/.test(caption)
      || /(全栈|团队|门槛|黑话|规则|翻译|满级)/.test(slotText)) {
      return [{
        type: 'job_requirement_card',
        start: 0.35,
        duration: longEnough,
        title: '岗位要求',
        items: requirementItems(text),
      }];
    }
    if (/(已读|不回|投递|黑洞|HR|面邀|拒)/i.test(slotText)) {
      return [{
        type: 'chat_stack',
        start: 0.35,
        duration: longEnough,
        title: '招聘消息',
        messages: jobMessages(slotText),
      }];
    }
    if (/(刷|看到|招聘软件|招聘APP|薪资|工资|岗位|心仪|软件|APP|招聘|应届生可投|可投)/.test(slotText)) {
      return [{
        type: 'phone_job_feed',
        start: 0.22,
        duration: longEnough,
        title: shortText(caption || '刷到薪资还行的岗位', 15),
        salary: salaryText(text),
        company: '校招热岗',
        tags: requirementTags(text),
      }];
    }
  }

  return [];
}

function searchableText(theme, slot) {
  return [
    theme,
    slot.copy || slot.caption || '',
    slot.intent || '',
    slot.background?.description || '',
    slot.motion?.description || '',
    ...(slot.dialogue || []).map((line) => line.text || ''),
  ].join(' ');
}

function primarySlotText(slot) {
  return [
    slot.copy || slot.caption || '',
    slot.intent || '',
    ...(slot.dialogue || []).map((line) => line.text || ''),
  ].join(' ');
}

function semanticRole(theme, slot, preset = null) {
  const text = searchableText(theme, slot);
  if (preset?.id === 'job-hunt-black-hole') return 'job_hunt';
  if (preset?.id === 'meeting-involution') return 'workplace';
  if (preset?.id === 'exam-choice-anxiety') return 'exam';
  if (preset?.id === 'rent-bill-pressure') return 'bill';
  if (preset?.id === 'street-food-stall-involution') return 'street_food';
  if (/(烤肠|香肠|摆摊|小吃摊|夜市|地摊)/.test(text)) return 'street_food';
  if (/(房租|押金|房贷|账单|预算|租房|合租|中介|通勤)/.test(text)) return 'bill';
  if (/(考研|考公|上岸|自习|考试)/.test(text)) return 'exam';
  if (/(会议|加班|复盘|同步|老板|KPI|PPT)/.test(text)) return 'workplace';
  if (isJobHunt(text)) return 'job_hunt';
  return 'cat_meme';
}

function isJobHunt(text, preset) {
  if (/(烤肠|香肠|摆摊|小吃摊|夜市|地摊|租房|房租|押金|合租|通勤|考研|考公|上岸|考试)/.test(text)) {
    return false;
  }
  return preset?.id === 'job-hunt-black-hole' || /(简历|求职|岗位|面试|HR|offer|校招|招聘|薪资|工资)/i.test(text);
}

function salaryText(text) {
  const match = text.match(/(\d{1,2}\s*[kK千万wW][-~到至]?\s*\d{0,2}\s*[kK千万wW]?|\d{3,5}\s*元?)/);
  if (match) return match[1].replace(/\s+/g, '');
  if (/(四千|4000|4k|4K)/.test(text)) return '4K';
  return '薪资还行';
}

function requirementTags(text) {
  if (/(应届|校招|毕业)/.test(text)) return ['应届可投', '经验优先', '立即沟通'];
  if (/(全栈|运营|团队)/.test(text)) return ['全链路', '带团队', '抗压'];
  return ['不加班', '双休', '经验不限'];
}

function requirementItems(text) {
  const items = [];
  if (/(黑话|规则|翻译)/.test(text)) items.push('经验不限=最好满级', '抗压=随时在线', '年轻团队=都很能卷');
  if (/(三年|3年|5年|五年|经验)/.test(text)) items.push('3年以上经验');
  if (/(全栈|运营|链路)/.test(text)) items.push('会全链路运营');
  if (/(团队|管理|老板)/.test(text)) items.push('带过团队');
  if (/(应届|校招|毕业)/.test(text)) items.push('欢迎应届生');
  return items.length ? items.slice(0, 4) : ['经验不限但要满级', '能抗压', '会很多'];
}

function billItems(text) {
  const items = [];
  if (/(房租|租房)/.test(text)) items.push('房租');
  if (/(押金|中介)/.test(text)) items.push('押金');
  if (/(通勤|地铁|公交)/.test(text)) items.push('通勤');
  if (/(水电|网费)/.test(text)) items.push('水电网');
  return items.length ? items.slice(0, 3) : ['房租', '通勤', '押金'];
}

function stallItems(text, role) {
  if (/(买一送一|降价|特价|竞争)/.test(text)) return ['隔壁买一送一', '我也降一块', '摊主也卷'];
  if (/(摊位费|成本|煤气|房租)/.test(text)) return ['摊位费先扣', '煤气也要钱', '利润先沉默'];
  if (['punchline', 'cta'].includes(role)) return ['烤肠不包上岸', '但能先暖手', '明天再摆'];
  return ['烤肠 3元', '加料 +1', '今日也内卷'];
}

function studyItems(text) {
  const items = [];
  if (/考研/.test(text)) items.push('考研英语');
  if (/(考公|申论)/.test(text)) items.push('申论资料');
  if (/(就业|简历|投)/.test(text)) items.push('简历待改');
  if (/(家族群|父母)/.test(text)) items.push('家族群攻略');
  return items.length ? items.slice(0, 3) : ['刷题 x3', '倒计时', '选择题'];
}

function examOptions(text) {
  const options = [];
  if (/考研/.test(text)) options.push('考研');
  if (/考公/.test(text)) options.push('考公');
  if (/(就业|工作|简历)/.test(text)) options.push('就业');
  for (const item of ['二战', '实习', '先睡觉']) {
    if (options.length >= 3) break;
    if (!options.includes(item)) options.push(item);
  }
  return options.length ? options.slice(0, 3) : ['考研', '考公', '就业'];
}

function officeMessages(text) {
  if (/(周会|复盘|同步)/.test(text)) return ['9点周会', '10点复盘', '再同步一次'];
  if (/(下班|在线|待命|在吗)/.test(text)) return ['老板：在吗', '简单看一下', '今晚辛苦下'];
  if (/PPT/.test(text)) return ['PPT再改版', '颜色再活泼', '五分钟后要'];
  return ['老板：在吗', '再同步一次', '今晚辛苦下'];
}

function jobMessages(text) {
  if (/(已读|不回|黑洞)/.test(text)) return ['HR：已读', '系统：暂无回复', '猫：我还在吗'];
  if (/(面试|沟通)/.test(text)) return ['先发作品集', '再做测试题', '下周等通知'];
  if (/(拒|暂不合适)/.test(text)) return ['很遗憾', '暂不合适', '保持联系'];
  return ['已投递', '对方已读', '要求又加一条'];
}

function shortText(text, maxLength) {
  const compact = String(text || '').replace(/\s+/g, '');
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 1)}…` : compact;
}

function mergeOverlayActions(actions) {
  const primaryTypes = new Set([
    'phone_job_feed',
    'job_requirement_card',
    'work_chat_stack',
    'chat_stack',
    'choice_panel',
    'study_card',
    'bill_card',
    'commute_card',
    'stall_sign',
    'leave_request',
    'emergency_call',
    'generated_sticker',
  ]);
  const primary = actions.find((action) => primaryTypes.has(action?.type));
  if (primary) {
    const emphasis = actions.find((action) => action?.type === 'impact_burst');
    const throwObject = actions.find((action) => action?.type === 'throw_object');
    if (emphasis && ['work_chat_stack', 'chat_stack'].includes(primary.type)) {
      return throwObject ? [primary, throwObject] : [primary, emphasis];
    }
    return throwObject ? [primary, throwObject] : [primary];
  }

  const seen = new Set();
  const merged = [];
  const priority = {
    impact_burst: 0,
    stamp_reject: 1,
    popup: 2,
    throw_object: 3,
  };
  actions = [...actions].sort((left, right) => (priority[left?.type] ?? 9) - (priority[right?.type] ?? 9));
  for (const action of actions) {
    if (!action?.type) continue;
    const key = `${action.type}|${action.text || action.title || action.object || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(action);
    if (merged.length >= 2) break;
  }
  return merged;
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--plan') parsed.plan = path.resolve(root, argv[++index]);
    else if (argv[index] === '--output') parsed.output = path.resolve(root, argv[++index]);
  }
  return parsed;
}

main().catch((error) => {
  console.error(error.stderr || error);
  process.exit(1);
});
