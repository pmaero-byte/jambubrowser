"""System, privacy, audit, and security endpoints."""
import psutil
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi import HTTPException

from backend import __version__
from backend.core.database import get_db_cursor, get_stats as db_stats
from backend.core.vault import get_vault
from backend.core.privacy import get_privacy_manager
from backend.core.audit import get_audit_logger
from backend.core.supply_chain import get_verifier
from backend.core.security import is_safe_url

router = APIRouter(tags=["system"])


# ── Exception handlers (installed by engine.py, not here) ──


@router.get("/health")
async def health():
    """System health with real-time metrics + dependency probes."""
    mem = psutil.virtual_memory()
    checks = {}
    overall = "online"

    try:
        from backend.core.database import get_db_cursor
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        overall = "degraded"

    try:
        from backend.core.audit import get_audit_logger
        stats = get_audit_logger().get_statistics()
        checks["audit"] = "ok"
        checks["audit_entries"] = stats.get("total_entries", 0)
    except Exception as e:
        checks["audit"] = f"error: {type(e).__name__}"
        overall = "degraded"

    try:
        from backend.core.vault import get_vault
        vault = get_vault()
        checks["vault"] = "locked" if vault.is_locked else "unlocked"
    except Exception as e:
        checks["vault"] = f"error: {type(e).__name__}"

    return {
        "status": overall,
        "message": f"Jambubrowser v{__version__} is ready.",
        "version": __version__,
        "ram_used_gb": round(mem.used / (1024 ** 3), 2),
        "ram_total_gb": round(mem.total / (1024 ** 3), 2),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "checks": checks,
    }


@router.get("/stats")
async def get_stats():
    """Database and system statistics."""
    db_info = db_stats()
    return {
        "doc_count": db_info["documents"],
        "active_missions": db_info["active_missions"],
        "custom_tools": db_info["custom_tools"],
        "credentials": db_info["credentials"],
        "browser_sessions": db_info["browser_sessions"],
    }


# ── Privacy ──


@router.get("/privacy/report")
async def privacy_report():
    """Get comprehensive privacy report."""
    privacy_mgr = get_privacy_manager()
    audit_logger = get_audit_logger()
    return {
        "privacy": privacy_mgr.get_privacy_report(),
        "audit": audit_logger.get_statistics(),
        "vault_status": "locked" if get_vault().is_locked else "unlocked",
    }


@router.get("/privacy/check")
async def check_url_privacy(url: str):
    """Check if a URL is allowed under current privacy mode."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    privacy_mgr = get_privacy_manager()
    allowed = privacy_mgr.check_url_allowed(url)
    return {
        "url": url,
        "allowed": allowed,
        "mode": privacy_mgr.mode.value,
    }


# ── Audit ──


@router.get("/audit/stats")
async def audit_stats():
    """Get audit statistics."""
    return get_audit_logger().get_statistics()


@router.get("/audit/log")
async def audit_log(category: str = None, limit: int = 100):
    """Get audit log entries."""
    audit_logger = get_audit_logger()
    entries = audit_logger.get_entries(category=category, limit=limit)
    return {"entries": entries, "total": len(entries)}


@router.get("/audit/verify")
async def verify_audit_chain():
    """Verify the integrity of the audit log chain."""
    audit_logger = get_audit_logger()
    is_valid, message = audit_logger.verify_chain_integrity()
    return {"valid": is_valid, "message": message}


# ── Security ──


@router.get("/security/verify")
async def verify_security():
    """Verify supply chain integrity of all dependencies."""
    verifier = get_verifier()
    report = verifier.get_verification_report()
    return report


@router.get("/security/verify/package")
async def verify_package(package_name: str):
    """Verify a specific Python package's integrity."""
    verifier = get_verifier()
    result = verifier.verify_package(package_name)
    return result


@router.post("/security/regenerate")
async def regenerate_supply_chain_baseline():
    """Re-hash every critical package and overwrite the known-good baseline.

    Call this after a legitimate dependency update (pip install --upgrade,
    new requirements.txt) so future verify calls compare against the new
    hashes instead of flagging every package as tampered.
    """
    verifier = get_verifier()
    report = verifier.regenerate_baseline()
    return report


# ── LLM Config ──


@router.get("/llm/config")
async def get_llm_config_info():
    """Get current LLM configuration with auto-detection."""
    from backend.llm import get_config, get_registry
    from backend.engine_runtime import LATEST_LLM_CONFIG
    cfg = get_config()
    reg = get_registry()
    return {
        "effective": LATEST_LLM_CONFIG,
        "config": {
            "default_provider": cfg.default_provider,
            "fallback_chain": cfg.fallback_chain,
            "auto_detect": cfg.auto_detect,
            "local_only": cfg.local_only,
            "timeout_seconds": cfg.timeout_seconds,
        },
        "available_providers": reg.list_available(),
        "endpoint_overrides": cfg.endpoint_overrides,
    }
