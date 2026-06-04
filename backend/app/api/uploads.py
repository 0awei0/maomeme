from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..models.maomeme import UploadResponse
from ..services.upload_store import save_material_uploads, save_viral_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/viral-video")
async def upload_viral_video(
    file: UploadFile = File(...),
    session_id: str | None = Form(None),
    description: str = Form(""),
):
    try:
        upload = await save_viral_upload(file=file, session_id=session_id, description=description)
        response = UploadResponse(session_id=upload.session_id, uploads=[upload])
        return JSONResponse(response.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc


@router.post("/materials")
async def upload_materials(
    files: list[UploadFile] = File(...),
    session_id: str | None = Form(None),
    description: str = Form(""),
):
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个素材文件")
    try:
        uploads = await save_material_uploads(files=files, session_id=session_id, description=description)
        current_session = uploads[0].session_id if uploads else session_id or ""
        response = UploadResponse(session_id=current_session, uploads=uploads)
        return JSONResponse(response.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"素材上传失败: {exc}") from exc
