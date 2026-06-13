"""Media and YouTube analysis endpoints."""
from fastapi import APIRouter, HTTPException

from backend.core.security import is_safe_url

router = APIRouter(tags=["media"])


@router.post("/media/youtube")
async def youtube_analyze(url: str, summarize: bool = False):
    """Analyze a YouTube video (transcript + metadata + optional summary)."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    try:
        from backend.modules.youtube import analyze_youtube
        result = await analyze_youtube(url, summarize=summarize)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/media/youtube/transcript")
async def youtube_transcript(url: str):
    """Get the transcript of a YouTube video."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    try:
        from backend.modules.youtube import get_youtube_transcript
        result = await get_youtube_transcript(url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/media/youtube/search")
async def youtube_search_transcript(url: str, query: str):
    """Search within a YouTube video's transcript."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    try:
        from backend.modules.youtube import search_youtube_transcript
        result = await search_youtube_transcript(url, query)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
