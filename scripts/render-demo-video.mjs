import fsSync from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(root, 'output');
const args = parseArgs(process.argv.slice(2));
const renderId = args.output ? path.basename(args.output, path.extname(args.output)) : `render-${Date.now()}`;
const runtimeDir = path.join(outputDir, 'runtime', renderId);
const workDir = path.join(runtimeDir, 'segments');
const captionDir = path.join(runtimeDir, 'captions');
const overlayDir = path.join(runtimeDir, 'overlays');
const segmentConcurrency = clampInt(process.env.RENDER_SEGMENT_CONCURRENCY, 1, Math.min(4, os.cpus().length || 2), 2);
const ffmpegPreset = process.env.RENDER_FFMPEG_PRESET || 'veryfast';
const pythonRunner = resolvePythonRunner();

async function renderSegment(slot, index) {
  const duration = Math.max(1, slot.end - slot.start);
  const out = path.join(workDir, `${String(index).padStart(2, '0')}-${slot.id}.mp4`);
  const background = path.join(root, slot.background.file);
  const motionSource = path.join(root, slot.motion.file);
  const secondaryMotionSource = slot.secondary_motion?.file ? path.join(root, slot.secondary_motion.file) : motionSource;
  const caption = path.join(captionDir, `${String(index).padStart(2, '0')}-${slot.id}.png`);
  const overlayFrameDir = path.join(overlayDir, `${String(index).padStart(2, '0')}-${slot.id}`);
  const hasOverlay = Array.isArray(slot.overlay_actions) && slot.overlay_actions.length > 0;
  await Promise.all([
    makeCaption(slot, caption),
    hasOverlay ? makeOverlayFrames(slot, overlayFrameDir, duration) : Promise.resolve()
  ]);
  const dialogue = slot.layout === 'dialogue' && slot.secondary_motion?.file;
  const baseFilter = dialogue
    ? [
      '[0:v]scale=960:544:force_original_aspect_ratio=increase,crop=960:544,setsar=1[bg]',
      '[1:v]crop=iw*0.5:ih:iw*0.25:0,scale=360:-1,colorkey=0x00ff00:0.32:0.08,format=rgba[leftcat]',
      '[2:v]crop=iw*0.5:ih:iw*0.25:0,hflip,scale=360:-1,colorkey=0x00ff00:0.32:0.08,format=rgba[rightcat]',
      '[bg][leftcat]overlay=62:H-h-14:shortest=1[leftcomp]',
      '[leftcomp][rightcat]overlay=W-w-62:H-h-14:shortest=1[comp]',
      '[3:v]format=rgba[cap]',
      '[comp][cap]overlay=0:0:shortest=1[captioned]'
    ].join(';')
    : [
      '[0:v]scale=960:544:force_original_aspect_ratio=increase,crop=960:544,setsar=1[bg]',
      '[1:v]crop=iw*0.5:ih:iw*0.25:0,scale=360:-1,colorkey=0x00ff00:0.32:0.08,format=rgba[cat]',
      '[bg][cat]overlay=(W-w)/2:H-h-18:shortest=1[comp]',
      '[2:v]format=rgba[cap]',
      '[comp][cap]overlay=0:0:shortest=1[captioned]'
    ].join(';');
  const overlayInputIndex = dialogue ? 4 : 3;
  const preOutput = hasOverlay
    ? `${baseFilter};[${overlayInputIndex}:v]format=rgba[ov];[captioned][ov]overlay=0:0:shortest=1[vpre]`
    : `${baseFilter};[captioned]copy[vpre]`;
  const audioFilter = dialogue
    ? `;[1:a]atrim=0:${duration},asetpts=PTS-STARTPTS,volume=0.78[a1];[2:a]atrim=0:${duration},asetpts=PTS-STARTPTS,volume=0.78[a2];[a1][a2]amix=inputs=2:duration=longest:normalize=1[a]`
    : `;[1:a]atrim=0:${duration},asetpts=PTS-STARTPTS[a]`;
  const filter = `${preOutput};${transitionFilter(slot.transition, duration)}${audioFilter}`;

  const inputs = dialogue ? [
    '-y',
    '-loop', '1',
    '-t', String(duration),
    '-i', background,
    ...motionInputArgs(motionSource, slot.motion_clip, duration),
    ...motionInputArgs(secondaryMotionSource, slot.secondary_motion_clip || slot.motion_clip, duration),
    '-loop', '1',
    '-t', String(duration),
    '-i', caption,
  ] : [
    '-y',
    '-loop', '1',
    '-t', String(duration),
    '-i', background,
    ...motionInputArgs(motionSource, slot.motion_clip, duration),
    '-loop', '1',
    '-t', String(duration),
    '-i', caption,
  ];
  if (hasOverlay) {
    inputs.push(
      '-framerate', '30',
      '-i', path.join(overlayFrameDir, '%04d.png')
    );
  }

  await execFileAsync('ffmpeg', [
    ...inputs,
    '-filter_complex', filter,
    '-map', '[v]',
    '-map', '[a]',
    '-r', '30',
    '-c:v', 'libx264',
    '-preset', ffmpegPreset,
    '-crf', '23',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-ar', '44100',
    '-ac', '2',
    '-pix_fmt', 'yuv420p',
    '-shortest',
    out
  ], { maxBuffer: 1024 * 1024 * 10 });

  return out;
}

function motionInputArgs(source, clipSpec = {}, slotDuration) {
  const clipStart = Math.max(0, Number(clipSpec?.start || 0));
  const clipDuration = clamp(Number(clipSpec?.duration || slotDuration || 4), 1, 5);
  return [
    '-stream_loop', '-1',
    '-ss', String(clipStart),
    '-t', String(Math.max(slotDuration, clipDuration)),
    '-i', source,
  ];
}

function transitionFilter(transition = {}, duration) {
  const type = transition?.type || 'cut';
  const transitionDuration = clamp(Number(transition?.duration || 0), 0, 0.5);
  if (type === 'fade' && transitionDuration > 0) {
    const outStart = Math.max(0, duration - transitionDuration);
    return `[vpre]fade=t=in:st=0:d=${transitionDuration},fade=t=out:st=${outStart}:d=${transitionDuration}[v]`;
  }
  if (type === 'flash' && transitionDuration > 0) {
    return `[vpre]fade=t=in:st=0:d=${transitionDuration}:color=white[v]`;
  }
  if (type === 'zoom') {
    return '[vpre]scale=1000:567:force_original_aspect_ratio=increase,crop=960:544:(iw-960)/2:(ih-544)/2,setsar=1[v]';
  }
  if (type === 'whip') {
    return '[vpre]crop=920:544:20:0,scale=960:544,setsar=1[v]';
  }
  return '[vpre]copy[v]';
}

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

async function makeOverlayFrames(slot, outDir, duration) {
  await fs.rm(outDir, { recursive: true, force: true });
  await runPython([
    path.join(root, 'scripts/make-overlay-frames.py'),
    '--actions',
    JSON.stringify(slot.overlay_actions || []),
    '--out-dir',
    outDir,
    '--duration',
    String(duration),
    '--fps',
    '30',
    '--width',
    '960',
    '--height',
    '544'
  ], { maxBuffer: 1024 * 1024 * 4 });
}

async function makeCaption(slot, out) {
  const title = slot.copy || slot.caption || slot.intent || slot.role;
  const subtitle = subtitleForSlot(slot);
  const showSubtitle = !(Array.isArray(slot.overlay_actions) && slot.overlay_actions.length > 0);
  await runPython([
    path.join(root, 'scripts/make-caption.py'),
    '--title',
    title,
    '--subtitle',
    subtitle,
    '--show-subtitle',
    showSubtitle ? 'true' : 'false',
    '--role',
    slot.role || '',
    '--layout',
    slot.layout || 'single',
    '--dialogue',
    JSON.stringify(slot.dialogue || []),
    '--out',
    out,
    '--width',
    '960',
    '--height',
    '544'
  ], { maxBuffer: 1024 * 1024 * 4 });
}

function subtitleForSlot(slot) {
  if (slot.subtitle) return slot.subtitle;
  if (slot.role === 'hook') return '老板说：简单聊两句';
  if (slot.role === 'setup') return '猫：我只是打开了电脑';
  if (slot.role === 'escalation') return '下午也会，晚上还会';
  if (slot.role === 'punchline') return '猫猫卖萌，准点下班';
  return slot.intent || '';
}

async function main() {
  const planPath = args.plan || await firstExisting([
    path.join(root, 'data/runs/latest-backend-plan.json'),
    path.join(root, 'data/runs/latest-plan.json')
  ]);
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  await fs.mkdir(workDir, { recursive: true });
  await fs.mkdir(captionDir, { recursive: true });
  await fs.mkdir(overlayDir, { recursive: true });

  const slots = Array.isArray(plan.timeline) ? plan.timeline : [];
  const segments = await mapLimit(
    slots.map((slot, index) => ({ slot, index })),
    segmentConcurrency,
    ({ slot, index }) => renderSegment(slot, index + 1)
  );

  const concatFile = path.join(workDir, 'concat.txt');
  await fs.writeFile(concatFile, segments.map((file) => `file '${file.replace(/'/g, "'\\''")}'`).join('\n'));

  const output = args.output ? path.resolve(args.output) : path.join(outputDir, 'maomeme-demo.mp4');
  await fs.mkdir(path.dirname(output), { recursive: true });
  await concatSegments(concatFile, output);

  console.log(`Rendered demo video: ${path.relative(root, output)}`);
}

function parseArgs(argv) {
  const parsed = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--plan') parsed.plan = path.resolve(root, argv[++i]);
    else if (argv[i] === '--output') parsed.output = argv[++i];
  }
  return parsed;
}

async function concatSegments(concatFile, output) {
  try {
    await execFileAsync('ffmpeg', [
      '-y',
      '-f', 'concat',
      '-safe', '0',
      '-i', concatFile,
      '-c', 'copy',
      '-movflags', '+faststart',
      output
    ], { maxBuffer: 1024 * 1024 * 10 });
  } catch {
    await execFileAsync('ffmpeg', [
      '-y',
      '-f', 'concat',
      '-safe', '0',
      '-i', concatFile,
      '-c:v', 'libx264',
      '-preset', ffmpegPreset,
      '-crf', '23',
      '-c:a', 'aac',
      '-b:a', '128k',
      '-ar', '44100',
      '-ac', '2',
      '-pix_fmt', 'yuv420p',
      output
    ], { maxBuffer: 1024 * 1024 * 10 });
  }
}

async function mapLimit(items, limit, mapper) {
  const results = new Array(items.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await mapper(items[index], index);
    }
  });
  await Promise.all(workers);
  return results;
}

function resolvePythonRunner() {
  if (process.env.MAOMEME_PYTHON && fsSync.existsSync(process.env.MAOMEME_PYTHON)) {
    return { command: process.env.MAOMEME_PYTHON, prefix: [] };
  }
  const condaPython = process.env.CONDA_PREFIX ? path.join(process.env.CONDA_PREFIX, 'bin', 'python') : '';
  if (condaPython && fsSync.existsSync(condaPython)) {
    return { command: condaPython, prefix: [] };
  }
  return { command: 'conda', prefix: ['run', '-n', 'cv', 'python'] };
}

function runPython(args, options) {
  return execFileAsync(pythonRunner.command, [...pythonRunner.prefix, ...args], options);
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

async function firstExisting(files) {
  for (const file of files) {
    try {
      await fs.access(file);
      return file;
    } catch {
      // keep looking
    }
  }
  throw new Error(`No plan file found: ${files.join(', ')}`);
}

main().catch((error) => {
  console.error(error.stderr || error);
  process.exit(1);
});
