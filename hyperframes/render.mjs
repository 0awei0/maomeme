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
    timeline: (plan.timeline || []).map((slot, index) => ({
      index,
      id: slot.id,
      time: { start: slot.start, end: slot.end },
      caption: slot.copy || slot.caption || '',
      packaging_preset: choosePreset(plan.theme, slot, presets),
      layout: slot.layout || 'single',
      transition: slot.transition || { type: 'cut', duration: 0 },
      dialogue: slot.dialogue || [],
      overlay_actions: slot.overlay_actions || [],
      packaging: slot.packaging || [],
      motion_clip: slot.motion_clip || {},
      secondary_motion_clip: slot.secondary_motion_clip || null,
      background_source: slot.background_source || 'matched',
    })),
  };
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
  const text = [
    theme,
    slot.copy || slot.caption || '',
    slot.intent || '',
    slot.background?.description || '',
    slot.motion?.description || '',
    ...(slot.dialogue || []).map((line) => line.text || ''),
  ].join(' ');
  let best = null;
  for (const preset of presets) {
    const score = (preset.triggers || []).reduce((total, trigger) => total + (trigger && text.includes(trigger) ? 1 : 0), 0);
    if (score && (!best || score > best.score)) best = { score, preset };
  }
  return best ? best.preset.id : 'default-cat-meme';
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
