"""Credential vault endpoints."""
from fastapi import APIRouter, HTTPException

from backend.core.security import is_safe_url
from backend.core.vault import get_vault

router = APIRouter(tags=["vault"])


def _ensure_vault_unlocked(vault) -> None:
    # Key-file-only deployments (no master password) auto-unlock for reads.
    if vault.is_locked and not os.environ.get("JAMBU_MASTER_PASSWORD"):
        vault.unlock("")


@router.get("/vault/domains")
async def list_vault_domains():
    """List all domains with stored credentials."""
    vault = get_vault()
    _ensure_vault_unlocked(vault)
    return {"domains": vault.list_domains()}


@router.get("/vault/credential")
async def get_vault_credential(url: str):
    """Find the best matching credential for a URL."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    vault = get_vault()
    _ensure_vault_unlocked(vault)
    cred = vault.find_best_credential(url)
    if cred:
        return {"found": True, "domain": cred["domain"], "username": cred["username"]}
    return {"found": False}


@router.get("/vault/credential/full")
async def get_vault_credential_full(url: str):
    """Find the best matching credential for a URL and return the decrypted
    password. Separate endpoint from /vault/credential so callers that
    only need a "is there a saved login" check don't have to be exposed
    to the secret. The Tauri autofill button is the only known caller.

    Returns 404 if the vault is locked, has no match, or the URL is
    rejected by the safety filter. We deliberately don't distinguish
    those cases in the response body to avoid leaking the vault state
    to a misbehaving caller."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    vault = get_vault()
    _ensure_vault_unlocked(vault)
    if vault.is_locked:
        raise HTTPException(status_code=404, detail="Vault is locked")
    cred = vault.find_best_credential(url)
    if not cred or not cred.get("password"):
        raise HTTPException(status_code=404, detail="No credential found")
    return {
        "found": True,
        "domain": cred["domain"],
        "username": cred["username"],
        "password": cred["password"],
        "url_pattern": cred.get("url_pattern") or "",
    }


from pydantic import BaseModel


class VaultUnlockRequest(BaseModel):
    master_password: str = ""


@router.post("/vault/unlock")
async def vault_unlock(req: VaultUnlockRequest):
    """Unlock the credential vault with master password."""
    vault = get_vault()
    success = vault.unlock(req.master_password)
    if success:
        return {"success": True, "message": "Vault unlocked"}
    return {"success": False, "error": "Invalid password or vault is locked out"}


@router.post("/vault/lock")
async def vault_lock():
    """Lock the credential vault."""
    vault = get_vault()
    vault.lock()
    return {"success": True, "message": "Vault locked"}


@router.get("/vault/status")
async def vault_status():
    """Get vault lock status."""
    vault = get_vault()
    return {
        "locked": vault.is_locked,
        "access_log": vault.get_access_log()[-10:],
    }
