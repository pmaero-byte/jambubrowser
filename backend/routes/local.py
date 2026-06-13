"""Local system endpoints — Obsidian, reminders, clipboard, notes."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["local"])


class ObsidianRequest(BaseModel):
    title: str
    content: str = ""
    folder: str = ""


class ReminderRequest(BaseModel):
    title: str
    notes: str = ""
    due_date: Optional[str] = None


class LocalNoteRequest(BaseModel):
    title: str
    content: str
    format: str = "markdown"


@router.post("/local/obsidian/create")
async def obsidian_create(req: ObsidianRequest):
    """Create a new Obsidian note."""
    try:
        from backend.modules.local_connector import create_obsidian_note
        result = await create_obsidian_note(req.title, req.content, req.folder)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/local/obsidian/append")
async def obsidian_append(req: ObsidianRequest):
    """Append content to an existing Obsidian note."""
    try:
        from backend.modules.local_connector import append_obsidian_note
        result = await append_obsidian_note(req.title, req.content)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/local/obsidian/read")
async def obsidian_read(title: str, vault_path: str = None):
    """Read an Obsidian note by title."""
    try:
        from backend.modules.local_connector import read_obsidian_note
        result = await read_obsidian_note(title, vault_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/local/obsidian/search")
async def obsidian_search(query: str, max_results: int = 10, vault_path: str = None):
    """Search the Obsidian vault."""
    try:
        from backend.modules.local_connector import search_obsidian
        result = await search_obsidian(query, max_results, vault_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/local/obsidian/stats")
async def obsidian_stats(vault_path: str = None):
    """Get Obsidian vault statistics."""
    try:
        from backend.modules.local_connector import get_obsidian_stats
        result = await get_obsidian_stats(vault_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/local/reminders/create")
async def reminders_create(req: ReminderRequest):
    """Create a macOS Reminder."""
    try:
        from backend.modules.local_connector import create_reminder
        result = await create_reminder(req.title, req.notes, req.due_date)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/local/clipboard/copy")
async def clipboard_copy(text: str):
    """Copy text to the system clipboard."""
    import subprocess
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        return {"status": "copied"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/local/clipboard/paste")
async def clipboard_paste():
    """Get the current system clipboard contents."""
    import subprocess
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return {"clipboard": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/local/notes/save")
async def save_research_note(req: LocalNoteRequest):
    """Save research as a local markdown file."""
    import os
    try:
        notes_dir = os.path.expanduser("~/.jambu/notes")
        os.makedirs(notes_dir, exist_ok=True)
        safe_name = req.title.replace(" ", "_").replace("/", "_")
        path = os.path.join(notes_dir, f"{safe_name}.md")
        with open(path, "w") as f:
            f.write(req.content)
        return {"status": "saved", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
