# MaoMeme Agent Notes

## Python Environment

- Do not use the system Python for this project.
- Do not create local virtualenvs such as `backend/.venv`.
- Use the existing conda environment `cv` for all Python backend work.
- Run backend commands through `conda run -n cv ...`, for example:

```bash
conda run -n cv python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Local Process Hygiene

- When starting backend or frontend dev servers for debugging, stop the temporary processes before finishing the turn.
- Do not leave extra uvicorn/Vite processes running on ports such as `8000`, `8001`, `5173`, or `5174`.
- If a port is already occupied by the user, do not kill it unless explicitly asked; use another port for temporary checks and close it afterward.

## Project Layout

- `frontend/`: React/Vite workbench. Users enter a theme, generate/select/revise scripts, submit render jobs, and preview videos.
- `backend/`: FastAPI service, Doubao/fallback agents, asset matching, plan revision, and render job queue.
- `scripts/`: repo-level CLI tools for asset indexing, demo plan generation, plan audit, caption PNG generation, and FFmpeg video rendering.
- `assets/cat-motions/`: source cat animation clips.
- `assets/backgrounds/`: source background images and descriptions.
- `assets/processed/`: local regenerated caches such as pre-keyed transparent cat clips; do not treat this as source material or commit it.
- `data/`: asset index, text material library, structure protocols, and run JSON files.
- `samples/viral/`: complete viral reference videos for structure analysis.
- `docs/`: competition docs and project notes.
- `output/`: public generated videos and final demo artifacts.
- `backend/outputs/`: backend runtime plans and intermediate job files.

## Secret Handling

- Never read, print, summarize, or copy real env files such as `.env` or `backend/.env`.
- Do not search for API keys or inspect secret values. Treat all real credentials as write-only runtime configuration.
- Use `backend/.env.example` to understand required variable names.
- Code may load secrets through environment-variable APIs at runtime, but generated logs, docs, tests, and responses must not expose secret values.
- If credential troubleshooting is needed, only report whether a required variable appears configured through safe application checks; do not reveal the value.

## Backend Direction

The backend borrows ideas from `/Users/a1-6/Desktop/code/douyin/backend`, especially Doubao video understanding, structured video models, and agent/tool orchestration. For this repo, keep the flow focused on cat meme generation:

1. Analyze viral reference videos.
2. Index local cat animations and backgrounds.
3. Generate script/storyboard/timeline with Doubao when `ARK_API_KEY` is available.
4. Fall back to deterministic local generation when API credentials are missing.
5. Render demos with FFmpeg first; keep HyperFrames as the optional HTML packaging upgrade.

## Agent vs Workflow Boundary

- Agents should decide creative structure and return structured JSON: script candidates, timeline slots, motion/background choices, clip ranges, transitions, dialogue, and overlay actions.
- Agents should not directly read env files, execute arbitrary scripts, write FFmpeg commands, or generate free-form renderer code.
- Dynamic Agent tools live as backend service functions, mainly `backend/app/services/agent_tools.py`.
- Repo scripts under `scripts/` are fixed workflow executors:
  - `scripts/index-assets.mjs`: rebuilds `data/assets-index.json`.
  - `scripts/clean-background-green-bands.py`: crops obvious green-screen bands from background images.
  - `scripts/preprocess-cat-green-screen.mjs`: regenerates local transparent cat-motion cache from source mp4 clips.
  - `scripts/render-demo-video.mjs`: stable FFmpeg/Pillow renderer.
  - `scripts/make-caption.py`: renderer helper for caption PNGs.
  - `scripts/make-overlay-frames.py`: renderer helper for overlay animation frames.
  - `scripts/audit-plan.mjs` and `scripts/generate-example-videos.mjs`: local QA/demo helpers.
- `backend/scripts/*.py` are manual smoke/demo CLIs, not tools the LLM dynamically invokes during normal frontend generation.

## Parallelism

- Use async Doubao clients for network Agent calls. The default frontend candidate stream uses one coordinated streaming request for 3 candidates to reduce duplicate ideas.
- Non-stream batch candidate generation may run multiple angle prompts concurrently, bounded by `ARK_AGENT_CONCURRENCY`.
- Storyboard/material matching may pre-match slots concurrently, bounded by `STORYBOARD_MATCH_CONCURRENCY`, then preserve timeline order for transitions and asset de-duplication.
- Rendering should keep segment-level parallelism via `RENDER_SEGMENT_CONCURRENCY`; within each segment, independent helpers such as caption PNG and overlay frames can run in parallel before FFmpeg composition.

## Agent Defaults

- Normal frontend/API generation should default to the real Doubao Agent path.
- Use deterministic local presets only when `use_doubao=false` is explicitly provided, when Doubao credentials are unavailable, or when the real Agent times out/errors and a clear fallback message is returned.
- Candidate generation should use the async streaming Ark client (`AsyncArk` with `stream=True`) so the UI can show incremental Agent output instead of waiting for a single blocking response.
- Do not hard-code model IDs for speed tests. Use `ARK_MODEL` for the formal/pro model and `ARK_LITE_MODEL` plus `ARK_MODEL_MODE=lite` for fast testing.
