"""Security event logging helper for middleware.

Centralises the way security middlewares record blocked requests so they all
follow the same shape: action type, client IP, path, method, request_id,
and a free-form reason/details dict. Events are written via the existing
tamper-evident audit log (category = NETWORK).

Failures to log are swallowed so that a broken audit subsystem can never
itself block legitimate traffic.
"""
import logging
from typing import Optional, Dict, Any


log = logging.getLogger("jambu.security")


_SECURITY_ACTIONS = {
    "rate_limit_exceeded": "Request blocked by rate limiter",
    "body_too_large": "Request body exceeded size limit",
    "untrusted_host": "Request had untrusted Host header",
    "request_timeout": "Request exceeded processing timeout",
}


def log_security_event(action: str,
                       client_ip: str = "",
                       path: str = "",
                       method: str = "",
                       request_id: str = "",
                       details: Optional[Dict[str, Any]] = None) -> None:
    """Record a security-relevant event to the audit log.

    Best-effort: any failure to log is swallowed to prevent the audit
    subsystem from causing additional failures.
    """
    description = _SECURITY_ACTIONS.get(action, action)
    event_details = {
        "client_ip": client_ip,
        "path": path,
        "method": method,
        "request_id": request_id,
    }
    if details:
        event_details.update(details)

    log.warning("Security event: %s | ip=%s | path=%s | method=%s | rid=%s",
                description, client_ip, path, method, request_id)

    try:
        from backend.core.audit import get_audit_logger, ActionCategory
        audit = get_audit_logger()
        audit.log(
            category=ActionCategory.NETWORK,
            action=action,
            details=event_details,
            session_id=request_id or "anonymous",
        )
    except Exception as e:
        log.debug("Audit log unavailable: %s", e)


def extract_client_ip(scope) -> str:
    """Extract the client IP from an ASGI scope, with fallbacks."""
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0])
    if isinstance(client, str):
        return client
    forwarded = scope.get("headers") or []
    for name, value in forwarded:
        if name.lower() == b"x-forwarded-for":
            try:
                first = value.decode("latin-1").split(",")[0].strip()
                if first:
                    return first
            except UnicodeDecodeError:
                pass
            break
    return "unknown"
