"""Billing routes — Stripe integration stub + usage reporting."""

from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

from backend.core.api_keys import validate_api_key, APIKey, TIER_LIMITS

router = APIRouter(tags=["billing"])

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICES = {
    "pro": os.environ.get("STRIPE_PRICE_PRO", "price_pro_monthly"),
    "team": os.environ.get("STRIPE_PRICE_TEAM", "price_team_monthly"),
}


class CheckoutRequest(BaseModel):
    tier: str
    success_url: str = "https://jambubrowser.com/billing/success"
    cancel_url: str = "https://jambubrowser.com/billing/cancel"


def _require_api_key(x_api_key: Optional[str] = Header(None)) -> APIKey:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    key = validate_api_key(x_api_key)
    if not key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key


@router.get("/billing/status")
async def billing_status(x_api_key: Optional[str] = Header(None)):
    key = _require_api_key(x_api_key)
    limits = TIER_LIMITS.get(key.tier, TIER_LIMITS["free"])
    return {
        "tier": key.tier,
        "name": key.name,
        "limits": limits,
        "usage": {
            "monthly_audits_used": key.monthly_audits_used,
            "monthly_audit_limit": key.monthly_audit_limit,
            "remaining": key.monthly_audit_limit - key.monthly_audits_used if key.monthly_audit_limit != -1 else "unlimited",
        },
        "billing_portal": "/billing/portal" if key.tier != "free" else None,
    }


@router.post("/billing/checkout")
async def billing_checkout(req: CheckoutRequest, x_api_key: Optional[str] = Header(None)):
    key = _require_api_key(x_api_key)

    if req.tier not in STRIPE_PRICES:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {req.tier}")

    if not STRIPE_SECRET_KEY:
        return {
            "status": "stripe_not_configured",
            "message": "Set STRIPE_SECRET_KEY to enable billing. Returning mock checkout.",
            "tier": req.tier,
            "price_id": STRIPE_PRICES[req.tier],
            "checkout_url": None,
        }

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICES[req.tier], "quantity": 1}],
            success_url=req.success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=req.cancel_url,
            metadata={"api_key_id": str(key.id), "tier": req.tier},
        )
        return {
            "status": "created",
            "checkout_url": session.url,
            "session_id": session.id,
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="stripe package not installed. Run: pip install stripe")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {e}")


@router.get("/billing/tiers")
async def billing_tiers():
    return {
        "tiers": {
            "free": {
                "name": "Free",
                "price_monthly": 0,
                "limits": TIER_LIMITS["free"],
                "features": ["5 Quick Scans/month", "1 Full Audit/month", "Basic findings", "No report export"],
            },
            "pro": {
                "name": "Pro",
                "price_monthly": 29,
                "limits": TIER_LIMITS["pro"],
                "features": ["Unlimited Quick Scans", "50 Full Audits/month", "Full findings + fixes", "Markdown/PDF export", "Audit history (30 days)", "GitHub Action"],
            },
            "team": {
                "name": "Team",
                "price_monthly": 99,
                "limits": TIER_LIMITS["team"],
                "features": ["Everything in Pro", "Unlimited Full Audits", "Team dashboard", "Jira/Linear integration", "Custom audit rules", "Priority support"],
            },
            "enterprise": {
                "name": "Enterprise",
                "price_monthly": "custom",
                "limits": TIER_LIMITS["enterprise"],
                "features": ["Self-hosted deployment", "Air-gapped LLM", "SSO/SAML", "Custom employees", "SLA + dedicated support", "SOC 2 compliance"],
            },
        }
    }
