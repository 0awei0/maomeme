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
const hyperframesManifestPromise = loadHyperframesManifest(args.hyperframesManifest);
const renderId = args.output ? path.basename(args.output, path.extname(args.output)) : `render-${Date.now()}`;
const runtimeDir = path.join(outputDir, 'runtime', renderId);
const workDir = path.join(runtimeDir, 'segments');
const captionDir = path.join(runtimeDir, 'captions');
const overlayDir = path.join(runtimeDir, 'overlays');
const segmentConcurrency = clampInt(process.env.RENDER_SEGMENT_CONCURRENCY, 1, Math.min(4, os.cpus().length || 2), 2);
const ffmpegPreset = process.env.RENDER_FFMPEG_PRESET || 'veryfast';
const actionAudioVolume = clamp(Number(process.env.RENDER_ACTION_AUDIO_VOLUME ?? '0.42'), 0, 1);
const finalBedVolume = clamp(Number(process.env.RENDER_FINAL_BGM_VOLUME ?? '0.16'), 0, 1);
const useFinalBed = String(process.env.RENDER_USE_FINAL_BGM ?? 'false').toLowerCase() === 'true';
const pythonRunner = resolvePythonRunner();
let fallbackAudioSourcePromise;

async function renderSegment(slot, index) {
  const hyperframeSlot = await hyperframeSlotFor(slot, index);
  slot = applyHyperframeSlot(slot, hyperframeSlot);
  const duration = Math.max(1, slot.end - slot.start);
  const out = path.join(workDir, `${String(index).padStart(2, '0')}-${slot.id}.mp4`);
  const background = path.join(root, slot.background.file);
  const motionSource = await resolveMotionSource(slot.motion.file);
  const secondaryMotionSource = slot.secondary_motion?.file ? await resolveMotionSource(slot.secondary_motion.file) : motionSource;
  const primaryAudio = await inspectAudio(motionSource.audioFile, slot.motion_clip, duration);
  const secondaryAudio = slot.secondary_motion?.file
    ? await inspectAudio(secondaryMotionSource.audioFile, slot.secondary_motion_clip || slot.motion_clip, duration)
    : null;
  const fallbackAudioSource = await getFallbackAudioSource();
  const caption = path.join(captionDir, `${String(index).padStart(2, '0')}-${slot.id}.png`);
  const overlayFrameDir = path.join(overlayDir, `${String(index).padStart(2, '0')}-${slot.id}`);
  const hasOverlay = Array.isArray(slot.overlay_actions) && slot.overlay_actions.length > 0;
  await Promise.all([
    makeCaption(slot, caption),
    hasOverlay ? makeOverlayFrames(slot, overlayFrameDir, duration) : Promise.resolve()
  ]);
  const dialogue = slot.layout === 'dialogue' && slot.secondary_motion?.file;
  const leftCatFilter = keyedMotionFilter(1, 'leftcat', { flip: false, keyed: motionSource.keyed, quality: slot.motion_quality });
  const rightCatFilter = keyedMotionFilter(2, 'rightcat', { flip: true, keyed: secondaryMotionSource.keyed, quality: slot.secondary_motion_quality || slot.motion_quality });
  const singleCatFilter = keyedMotionFilter(1, 'cat', { flip: false, keyed: motionSource.keyed, quality: slot.motion_quality });
  const baseFilter = dialogue
    ? [
      '[0:v]scale=960:544:force_original_aspect_ratio=increase,crop=960:544,setsar=1[bg]',
      leftCatFilter,
      rightCatFilter,
      '[bg][leftcat]overlay=62:H-h-54:shortest=1[leftcomp]',
      '[leftcomp][rightcat]overlay=W-w-62:H-h-54:shortest=1[comp]',
      '[3:v]format=rgba[cap]',
      '[comp][cap]overlay=0:0:shortest=1[captioned]'
    ].join(';')
    : [
      '[0:v]scale=960:544:force_original_aspect_ratio=increase,crop=960:544,setsar=1[bg]',
      singleCatFilter,
      '[bg][cat]overlay=(W-w)/2:H-h-58:shortest=1[comp]',
      '[2:v]format=rgba[cap]',
      '[comp][cap]overlay=0:0:shortest=1[captioned]'
    ].join(';');
  const overlayInputIndex = dialogue ? 4 : 3;
  const mainAudio = chooseMainAudioTrack({
    primaryAudio,
    secondaryAudio,
    motionSource,
    secondaryMotionSource,
    slot,
    dialogue,
    fallbackAudioSource,
  });
  const preOutput = hasOverlay
    ? `${baseFilter};[${overlayInputIndex}:v]format=rgba[ov];[captioned][ov]overlay=0:0:shortest=1[vpre]`
    : `${baseFilter};[captioned]copy[vpre]`;
  const audioStartIndex = overlayInputIndex + (hasOverlay ? 1 : 0);
  const fallbackInputIndex = audioStartIndex + (mainAudio ? 1 : 0);
  const audioFilter = buildAudioFilter({
    duration,
    audioInput: mainAudio ? { ...mainAudio, input: audioStartIndex } : null,
    fallbackInputIndex: mainAudio ? null : fallbackAudioSource ? fallbackInputIndex : null,
  });
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
  if (mainAudio) {
    inputs.push(
      '-ss', String(Math.max(0, Number(mainAudio.clip?.start || 0))),
      '-t', String(duration),
      '-i', mainAudio.file
    );
  }
  if (!mainAudio && fallbackAudioSource) {
    inputs.push(
      '-stream_loop', '-1',
      '-t', String(duration),
      '-i', fallbackAudioSource.file
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
    '-t', String(duration),
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
    '-i', source.file,
  ];
}

function chooseMainAudioTrack({ primaryAudio, secondaryAudio, motionSource, secondaryMotionSource, slot, dialogue, fallbackAudioSource }) {
  if (primaryAudio.audible) {
    return {
      file: motionSource.audioFile,
      clip: slot.motion_clip,
      label: 'amain',
      volume: dialogue ? actionAudioVolume * 0.82 : actionAudioVolume,
    };
  }
  if (dialogue && secondaryAudio?.audible) {
    return {
      file: secondaryMotionSource.audioFile,
      clip: slot.secondary_motion_clip || slot.motion_clip,
      label: 'amain',
      volume: actionAudioVolume * 0.72,
    };
  }
  if (fallbackAudioSource) {
    return null;
  }
  return null;
}

function buildAudioFilter({ duration, audioInput = null, fallbackInputIndex = null }) {
  if (!audioInput && fallbackInputIndex === null) {
    return `;anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:${duration},asetpts=PTS-STARTPTS[a]`;
  }
  if (audioInput) {
    return `;[${audioInput.input}:a]atrim=0:${duration},asetpts=PTS-STARTPTS,volume=${audioInput.volume ?? 1.0},aformat=channel_layouts=stereo:sample_rates=44100[a]`;
  }
  return `;[${fallbackInputIndex}:a]atrim=0:${duration},asetpts=PTS-STARTPTS,volume=${finalBedVolume},aformat=channel_layouts=stereo:sample_rates=44100[a]`;
}

async function inspectAudio(file, clipSpec = {}, slotDuration = 4) {
  try {
    const probe = await execFileAsync('ffprobe', [
      '-v', 'error',
      '-select_streams', 'a:0',
      '-show_entries', 'stream=codec_type',
      '-of', 'json',
      file
    ], { maxBuffer: 1024 * 256 });
    const parsed = JSON.parse(probe.stdout || '{}');
    if (!Array.isArray(parsed.streams) || !parsed.streams.length) {
      return { hasAudio: false, audible: false, maxVolume: -Infinity };
    }
    const start = Math.max(0, Number(clipSpec?.start || 0));
    const duration = clamp(Number(clipSpec?.duration || slotDuration || 4), 1, Math.max(1, slotDuration || 4));
    const volume = await execFileAsync('ffmpeg', [
      '-hide_banner',
      '-nostats',
      '-ss', String(start),
      '-t', String(duration),
      '-i', file,
      '-af', 'volumedetect',
      '-f', 'null',
      '-'
    ], { maxBuffer: 1024 * 512 }).catch((error) => error);
    const text = `${volume.stderr || ''}`;
    const match = text.match(/max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB/);
    const maxVolume = match ? Number(match[1]) : -Infinity;
    return { hasAudio: true, audible: Number.isFinite(maxVolume) && maxVolume > -45, maxVolume };
  } catch {
    return { hasAudio: false, audible: false, maxVolume: -Infinity };
  }
}

async function getFallbackAudioSource() {
  if (!fallbackAudioSourcePromise) {
    fallbackAudioSourcePromise = findFallbackAudioSource();
  }
  return fallbackAudioSourcePromise;
}

async function findFallbackAudioSource() {
  const preferred = [
    'data/viral-structures/baokuan-maomeme/bkmm-001-抖音202662-534438/audio.m4a',
    'data/viral-structures/baokuan-maomeme/bkmm-036-抖音202663-775737/audio.m4a',
    'data/viral-structures/baokuan-maomeme/bkmm-003-抖音202663-009401/audio.m4a',
    'assets/cat-motions/13.mp4',
    'assets/cat-motions/2.mp4',
    'assets/cat-motions/4.mp4',
    'assets/cat-motions/9.mp4',
  ].map((file) => path.join(root, file));
  const candidates = [
    ...preferred,
    ...((await fs.readdir(path.join(root, 'assets/cat-motions')).catch(() => []))
      .filter((file) => file.endsWith('.mp4'))
      .map((file) => path.join(root, 'assets/cat-motions', file))),
  ];
  for (const file of candidates) {
    if (!await exists(file)) continue;
    const audio = await inspectAudio(file, { start: 0, duration: 4 }, 4);
    if (audio.audible) return { file };
  }
  return null;
}

async function resolveMotionSource(file) {
  const source = path.join(root, file);
  const id = path.basename(file, path.extname(file));
  const keyed = path.join(root, 'assets/processed/cat-motions-keyed', `${id}.mov`);
  if (await exists(keyed)) return { file: keyed, audioFile: source, audioInputIndex: 1, keyed: true };
  return { file: source, audioFile: source, audioInputIndex: 1, keyed: false };
}

async function exists(file) {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

function keyedMotionFilter(inputIndex, label, options = {}) {
  const flip = options.flip ? 'hflip,' : '';
  const needsCrop = Boolean(options.quality?.needs_crop);
  const crop = needsCrop ? 'crop=iw*0.62:ih*0.76:iw*0.19:ih*0.08,' : 'crop=iw*0.5:ih-36:iw*0.25:0,';
  const scale = needsCrop ? 'scale=310:-1,' : 'scale=360:-1,';
  if (options.keyed) {
    return `[${inputIndex}:v]${flip}${needsCrop ? crop : ''}${scale}format=rgba[${label}]`;
  }
  return `[${inputIndex}:v]${crop}${flip}${scale}colorkey=0x00ff00:0.38:0.10,despill=green,format=rgba[${label}]`;
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

async function loadHyperframesManifest(file) {
  if (!file) return null;
  try {
    return JSON.parse(await fs.readFile(path.resolve(file), 'utf8'));
  } catch {
    return null;
  }
}

async function hyperframeSlotFor(slot, index) {
  const manifest = await hyperframesManifestPromise;
  if (!manifest || !Array.isArray(manifest.timeline)) return null;
  return manifest.timeline.find((item) => item.id === slot.id) || manifest.timeline[index - 1] || null;
}

function applyHyperframeSlot(slot, hyperframeSlot) {
  if (!hyperframeSlot) return slot;
  return {
    ...slot,
    overlay_actions: Array.isArray(hyperframeSlot.overlay_actions) ? hyperframeSlot.overlay_actions : slot.overlay_actions,
    packaging_preset: hyperframeSlot.packaging_preset || slot.packaging_preset,
    hyperframe_role: hyperframeSlot.hyperframe_role || slot.hyperframe_role,
    motion_quality: hyperframeSlot.motion_quality || slot.motion_quality || {},
    secondary_motion_quality: hyperframeSlot.secondary_motion_quality || slot.secondary_motion_quality || {},
  };
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
  const totalDuration = slots.reduce((total, slot) => total + Math.max(1, Number(slot.end || 0) - Number(slot.start || 0)), 0);
  const finalBed = useFinalBed ? await getFallbackAudioSource() : null;
  await concatSegments(concatFile, output, totalDuration, finalBed);
  await assertAudioVideoAligned(output);

  console.log(`Rendered demo video: ${path.relative(root, output)}`);
}

function parseArgs(argv) {
  const parsed = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--plan') parsed.plan = path.resolve(root, argv[++i]);
    else if (argv[i] === '--output') parsed.output = argv[++i];
    else if (argv[i] === '--hyperframes-manifest') parsed.hyperframesManifest = path.resolve(root, argv[++i]);
  }
  return parsed;
}

async function concatSegments(concatFile, output, totalDuration, finalBed = null) {
  const tempOutput = output.replace(/\.mp4$/i, '.concat-tmp.mp4');
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
    tempOutput
  ], { maxBuffer: 1024 * 1024 * 10 });
  const trimArgs = [
    '-y',
    '-i', tempOutput,
  ];
  if (finalBed?.file) {
    trimArgs.push('-stream_loop', '-1', '-t', String(totalDuration), '-i', finalBed.file);
  }
  const hasFiniteDuration = Number.isFinite(totalDuration) && totalDuration > 0;
  if (hasFiniteDuration) {
    const audioFilter = finalBed?.file
      ? `[0:a]asetpts=PTS-STARTPTS,atrim=0:${totalDuration},volume=${Math.max(0, 1 - finalBedVolume * 0.5)}[seg_audio];[1:a]asetpts=PTS-STARTPTS,atrim=0:${totalDuration},volume=${finalBedVolume}[final_bed];[seg_audio][final_bed]amix=inputs=2:duration=first:normalize=0,aformat=channel_layouts=stereo:sample_rates=44100,atrim=0:${totalDuration},asetpts=PTS-STARTPTS[a]`
      : `[0:a]asetpts=PTS-STARTPTS,atrim=0:${totalDuration},asetpts=PTS-STARTPTS[a]`;
    trimArgs.push(
      '-t',
      String(totalDuration),
      '-filter_complex',
      `[0:v]setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop_duration=1,trim=0:${totalDuration},setpts=PTS-STARTPTS[v];${audioFilter}`,
      '-map',
      '[v]',
      '-map',
      '[a]',
    );
  }
  trimArgs.push(
    '-c:v', 'libx264',
    '-preset', ffmpegPreset,
    '-crf', '23',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-ar', '44100',
    '-ac', '2',
    '-pix_fmt', 'yuv420p',
    '-shortest',
    '-movflags', '+faststart',
    output
  );
  await execFileAsync('ffmpeg', trimArgs, { maxBuffer: 1024 * 1024 * 10 });
  await fs.rm(tempOutput, { force: true });
}

async function assertAudioVideoAligned(file) {
  const result = await execFileAsync('ffprobe', [
    '-v', 'error',
    '-show_entries', 'stream=codec_type,duration',
    '-of', 'json',
    file,
  ], { maxBuffer: 1024 * 256 });
  const streams = JSON.parse(result.stdout || '{}').streams || [];
  const video = streams.find((stream) => stream.codec_type === 'video');
  const audio = streams.find((stream) => stream.codec_type === 'audio');
  if (!video || !audio) {
    throw new Error(`Rendered file missing video/audio stream: ${file}`);
  }
  const videoDuration = Number(video.duration || 0);
  const audioDuration = Number(audio.duration || 0);
  if (Number.isFinite(videoDuration) && Number.isFinite(audioDuration) && Math.abs(videoDuration - audioDuration) > 0.25) {
    throw new Error(`Audio/video duration mismatch: video=${videoDuration.toFixed(3)} audio=${audioDuration.toFixed(3)}`);
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
