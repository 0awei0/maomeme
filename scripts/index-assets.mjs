import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ignoredBackgroundDirs = new Set(['seedream-smoke']);

function isLocalDuplicate(name) {
  return / 2(?:\.[^.]+)?$/.test(name);
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return fallback;
  }
}

async function ffprobe(file) {
  try {
    const { stdout } = await execFileAsync('ffprobe', [
      '-v', 'error',
      '-select_streams', 'v:0',
      '-show_entries', 'stream=width,height,duration,r_frame_rate',
      '-of', 'json',
      file
    ]);
    return JSON.parse(stdout).streams?.[0] ?? {};
  } catch {
    return {};
  }
}

async function listFiles(dir, ext) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const exts = Array.isArray(ext) ? ext : [ext];
  return entries
    .filter((entry) => entry.isFile() && !isLocalDuplicate(entry.name) && exts.some((item) => entry.name.toLowerCase().endsWith(item)))
    .map((entry) => path.join(dir, entry.name))
    .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }));
}

async function listBackgrounds() {
  const pictureRoots = [
    { root: path.join(root, 'assets/backgrounds'), prefix: '' },
    { root: path.join(root, 'assets/generated/backgrounds'), prefix: 'generated' }
  ];
  const backgrounds = [];

  async function visit(groupDir, pictureRoot, prefix) {
    if (prefix && ignoredBackgroundDirs.has(path.basename(groupDir))) {
      return;
    }

    const descriptions = await readJson(path.join(groupDir, 'descriptions.json'), []);
    const descriptionByFile = new Map(descriptions.map((item) => [item.file, item.description]));
    const images = await listFiles(groupDir, ['.jpg', '.jpeg', '.png', '.webp']);
    const relativeScene = path.relative(pictureRoot, groupDir);
    const scene = prefix ? path.join(prefix, relativeScene) : relativeScene;

    for (const image of images) {
      backgrounds.push({
        id: `${scene}/${path.basename(image, '.jpg')}`,
        type: 'background',
        scene,
        file: path.relative(root, image),
        description: descriptionByFile.get(path.basename(image)) ?? ''
      });
    }

    const entries = await fs.readdir(groupDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && !isLocalDuplicate(entry.name)) {
        await visit(path.join(groupDir, entry.name), pictureRoot, prefix);
      }
    }
  }

  for (const item of pictureRoots) {
    try {
      await fs.access(item.root);
      await visit(item.root, item.root, item.prefix);
    } catch {
      // generated backgrounds are optional
    }
  }
  return backgrounds;
}

async function listStickers() {
  const stickerRoot = path.join(root, 'assets/stickers');
  const stickers = [];
  const categoryDescriptions = await readJson(path.join(stickerRoot, 'descriptions.json'), []);
  const categoryByFolder = new Map(categoryDescriptions.map((item) => [item.folder, item]));

  async function visit(groupDir) {
    const folder = path.relative(stickerRoot, groupDir);
    const descriptions = await readJson(path.join(groupDir, 'descriptions.json'), []);
    const descriptionByFile = new Map(descriptions.map((item) => [item.file, item.description]));
    const images = await listFiles(groupDir, ['.jpg', '.jpeg', '.png', '.webp']);
    const category = categoryByFolder.get(folder) || {};

    for (const image of images) {
      const info = await ffprobe(image);
      const basename = path.basename(image);
      const id = `${folder}/${path.basename(image, path.extname(image))}`;
      stickers.push({
        id,
        type: 'sticker',
        category: folder,
        category_name: category.category || '',
        file: path.relative(root, image),
        description: descriptionByFile.get(basename) ?? category.description ?? '',
        width: Number(info.width ?? 0),
        height: Number(info.height ?? 0)
      });
    }

    const entries = await fs.readdir(groupDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && !isLocalDuplicate(entry.name)) {
        await visit(path.join(groupDir, entry.name));
      }
    }
  }

  try {
    await fs.access(stickerRoot);
    const entries = await fs.readdir(stickerRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && !isLocalDuplicate(entry.name)) {
        await visit(path.join(stickerRoot, entry.name));
      }
    }
  } catch {
    // stickers are optional for older checkouts
  }
  return stickers;
}

async function main() {
  const materialDir = path.join(root, 'assets/cat-motions');
  const catDescriptions = await readJson(path.join(materialDir, 'descriptions.json'), {});
  const catLayouts = await readJson(path.join(root, 'data/cat-layout-index.json'), {});
  const layoutById = catLayouts.layouts || catLayouts;
  const videos = await listFiles(materialDir, '.mp4');
  const motions = [];

  for (const video of videos) {
    const id = path.basename(video, '.mp4');
    const info = await ffprobe(video);
    const descriptionEntry = catDescriptions[id] ?? '';
    const description = typeof descriptionEntry === 'string' ? descriptionEntry : descriptionEntry.description ?? '';
    const motion = {
      id,
      type: 'cat_motion',
      file: path.relative(root, video),
      description,
      width: Number(info.width ?? 0),
      height: Number(info.height ?? 0),
      fps: info.r_frame_rate ?? '',
      duration: Number(info.duration ?? 0)
    };
    if (descriptionEntry && typeof descriptionEntry === 'object' && descriptionEntry.motion_tags) {
      motion.motion_tags = descriptionEntry.motion_tags;
    }
    if (layoutById[id]) {
      motion.cat_layout = layoutById[id];
    }
    motions.push(motion);
  }

  const backgrounds = await listBackgrounds();
  const stickers = await listStickers();
  const index = {
    generated_at: new Date().toISOString(),
    summary: {
      cat_motions: motions.length,
      backgrounds: backgrounds.length,
      stickers: stickers.length
    },
    cat_motions: motions,
    backgrounds,
    stickers
  };

  await fs.mkdir(path.join(root, 'data'), { recursive: true });
  await fs.writeFile(path.join(root, 'data/assets-index.json'), JSON.stringify(index, null, 2));
  console.log(`Indexed ${motions.length} cat motions, ${backgrounds.length} backgrounds, and ${stickers.length} stickers.`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
