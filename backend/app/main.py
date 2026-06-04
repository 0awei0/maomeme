from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings

from .api.analyze import router as analyze_router
from .api.maomeme import router as maomeme_router
from .api.uploads import router as uploads_router

app = FastAPI(title="MaoMeme 爆款结构迁移引擎", version="0.1.0")
settings = get_settings()
settings.PUBLIC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze_router)
app.include_router(maomeme_router)
app.include_router(uploads_router)
app.mount("/output", StaticFiles(directory=str(settings.PUBLIC_OUTPUT_DIR)), name="output")


@app.get("/health")
async def health():
    return {"status": "ok"}
