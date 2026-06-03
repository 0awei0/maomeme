# Baokuan MaoMeme Structure Library

This folder tracks the local viral cat meme reference library.

- `raw/` contains copied source videos for local analysis and is intentionally gitignored.
- `manifest.json` is committed and records the 43 source videos, local raw paths, metadata, hashes, and analysis status.
- Analysis outputs are written to `data/viral-structures/baokuan-maomeme/`.

Import or refresh local raw videos:

```bash
conda run -n cv python backend/scripts/import_viral_maomeme.py
```

Analyze with concurrent Doubao video understanding:

```bash
conda run -n cv python backend/scripts/analyze_viral_maomeme.py --concurrency 8 --resume
```

The raw videos are local reference material. Commit the manifest and structured analysis outputs, not the raw video files.
