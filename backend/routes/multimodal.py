"""Multimodal processing endpoints."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["multimodal"])


class MultimodalImageRequest(BaseModel):
    image_data: str
    prompt: str = ""


class MultimodalFileRequest(BaseModel):
    file_path: str
    prompt: str = ""


class MultimodalTextRequest(BaseModel):
    text: str
    prompt: str = ""


@router.post("/multimodal/image")
async def multimodal_image(req: MultimodalImageRequest):
    """Process an image (OCR/analysis/extraction)."""
    try:
        from backend.modules.multimodal_input import process_image
        result = await process_image(req.image_data, req.prompt)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/multimodal/file")
async def multimodal_file(req: MultimodalFileRequest):
    """Process a file (CSV, JSON, markdown, code, text)."""
    try:
        from backend.modules.multimodal_input import process_file
        result = await process_file(req.file_path, req.prompt)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/multimodal/text")
async def multimodal_text(req: MultimodalTextRequest):
    """Process pasted text (URL detection, code recognition)."""
    try:
        from backend.modules.multimodal_input import get_processor
        processor = get_processor()
        result = await processor.process_text_input(req.text)
        return {
            "input_type": result.input_type,
            "extracted_text": result.extracted_text,
            "summary": result.summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
