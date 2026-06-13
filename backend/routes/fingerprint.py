"""Browser fingerprint management endpoints."""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["fingerprint"])


class FingerprintGenerateRequest(BaseModel):
    os_family: str = None


@router.post("/fingerprint/generate")
async def generate_fingerprint(req: FingerprintGenerateRequest):
    """Generate a new browser fingerprint profile for session isolation."""
    try:
        from backend.modules.fingerprint_rotator import get_fingerprint_rotator
        rotator = get_fingerprint_rotator()
        profile = await rotator.generate(req.os_family)
        return {"profile_id": profile.id, "os": profile.os_family}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fingerprint/list")
async def list_fingerprints():
    """List all generated fingerprint profiles."""
    from backend.modules.fingerprint_rotator import get_fingerprint_rotator
    rotator = get_fingerprint_rotator()
    return {"profiles": rotator.list_profiles()}


@router.get("/fingerprint/profile/{profile_id}")
async def get_fingerprint(profile_id: str):
    """Get a specific fingerprint profile by ID."""
    from backend.modules.fingerprint_rotator import get_fingerprint_rotator
    rotator = get_fingerprint_rotator()
    profile = rotator.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile not found: {profile_id}")
    return profile.to_dict()


@router.post("/fingerprint/rotate")
async def rotate_fingerprint(current_profile_id: str = None):
    """Rotate to a new fingerprint for the current session."""
    try:
        from backend.modules.fingerprint_rotator import get_fingerprint_rotator
        rotator = get_fingerprint_rotator()
        profile = await rotator.rotate(current_profile_id)
        return {"profile_id": profile.id, "os": profile.os_family}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
