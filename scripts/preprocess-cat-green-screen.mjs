import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = parseArgs(process.argv.slice(2));
const sourceDir = path.resolve(root, args.source || 'assets/cat-motions');
const outDir = path.resolve(root, args.outDir || 'assets/processed/cat-motions-keyed');
const reportPath = path.resolve(root, args.report || 'data/runs/cat-green-screen-preprocess.json');
const concurrency = clampInt(args.concurrency || process.env.CAT_KEY_CONCURRENCY, 1, 6, 3);

await fs.mkdir(outDir, { recursive: true });
const sources = await listFiles(sourceDir, '.mp4');
const items = await mapLimit(sources, concurrency, preprocessOne);
await fs.mkdir(path.dirname(reportPath), { recursive: true });
await fs.writeFile(reportPath, JSON.stringify({
  generated_at: new Date().toISOString(),
  source_dir: path.relative(root, sourceDir),
  out_dir: path.relative(root, outDir),
  scanned: sources.length,
  changed: items.filter((item) => item.status === 'processed').length,
  skipped: items.filter((item) => item.status === 'skipped').length,
  failed: items.filter((item) => item.status === 'failed').length,
  items,
}, null, 2));

console.log(JSON.stringify({
  scanned: sources.length,
  processed: items.filter((item) => item.status === 'processed').length,
  skipped: items.filter((item) => item.status === 'skipped').length,
  failed: items.filter((item) => item.status === 'failed').length,
  out_dir: path.relative(root, outDir),
  report: path.relative(root, reportPath),
}, null, 2));

async function preprocessOne(source) {
  const id = path.basename(source, path.extname(source));
  const out = path.join(outDir, `${id}.mov`);
  const sourceStat = await fs.stat(source);
  const outStat = await safeStat(out);
  if (!args.force && outStat && outStat.mtimeMs >= sourceStat.mtimeMs && outStat.size > 0) {
    return {
      id,
      source: path.relative(root, source),
      output: path.relative(root, out),
      status: 'skipped',
      reason: 'up to date',
    };
  }

  const filter = [
    'crop=iw*0.5:ih-36:iw*0.25:0',
    'colorkey=0x00ff00:0.38:0.10',
    'despill=green',
    'format=argb'
  ].join(',');

  try {
    await execFileAsync('ffmpeg', [
      '-y',
      '-i', source,
      '-vf', filter,
      '-map', '0:v:0',
      '-map', '0:a?',
      '-c:v', 'qtrle',
      '-pix_fmt', 'argb',
      '-c:a', 'aac',
      '-b:a', '128k',
      out,
    ], { maxBuffer: 1024 * 1024 * 12 });
    return {
      id,
      source: path.relative(root, source),
      output: path.relative(root, out),
      status: 'processed',
    };
  } catch (error) {
    return {
      id,
      source: path.relative(root, source),
      output: path.relative(root, out),
      status: 'failed',
      reason: safeError(error),
    };
  }
}

async function listFiles(dir, ext) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(ext))
    .map((entry) => path.join(dir, entry.name))
    .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }));
}

async function safeStat(file) {
  try {
    return await fs.stat(file);
  } catch {
    return null;
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

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function parseArgs(argv) {
  const parsed = { force: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--source') parsed.source = argv[++i];
    else if (argv[i] === '--out-dir') parsed.outDir = argv[++i];
    else if (argv[i] === '--report') parsed.report = argv[++i];
    else if (argv[i] === '--concurrency') parsed.concurrency = argv[++i];
    else if (argv[i] === '--force') parsed.force = true;
  }
  return parsed;
}

function safeError(error) {
  return String(error?.stderr || error?.message || error).slice(0, 600);
}
