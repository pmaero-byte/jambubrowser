"""
Anti-Bot Shield Hardening & Human-in-the-Loop (HITL) Detection Engine.

Provides:
1. Deep detection of anti-bot barriers: Cloudflare Turnstile, Akamai Bot Manager,
   reCAPTCHA, hCaptcha, and AWS WAF challenges.
2. Production-grade browser stealth & fingerprint masking scripts.
3. Interactive Human-in-the-Loop (HITL) handover state tracking, pausing autonomous
   agents when a CAPTCHA is present, and enabling human solving in the live viewport.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

log = logging.getLogger("jambu.antibot")

# ── Stealth / Anti-Detection JavaScript ─────────────────────────────────────
# Injected into page context before scripts load.
# Masks automation indicators, mocks Chrome runtime, and prevents webdriver leaks.

STEALTH_JS = r"""
(() => {
  // 1. Mask webdriver property
  try {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => false,
      configurable: true
    });
    delete navigator.__proto__.webdriver;
  } catch (e) {}

  // 2. Realistic Chrome runtime emulation
  try {
    if (!window.chrome) {
      window.chrome = {};
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        connect: () => {},
        sendMessage: () => {},
        id: undefined,
        onMessage: { addListener: () => {} }
      };
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = () => ({
        requestTime: (Date.now() - 500) / 1000,
        startLoadTime: (Date.now() - 400) / 1000,
        commitLoadTime: (Date.now() - 300) / 1000,
        finishDocumentLoadTime: (Date.now() - 100) / 1000,
        firstPaintTime: (Date.now() - 50) / 1000,
        finishLoadTime: (Date.now() - 10) / 1000,
        wasFetchedViaSpdy: false,
        wasNpnNegotiated: false,
        npnNegotiatedProtocol: "",
        wasAlternateProtocolAvailable: false,
        connectionInfo: "http/1.1"
      });
    }
    if (!window.chrome.csi) {
      window.chrome.csi = () => ({
        startE: Date.now() - 500,
        onloadT: Date.now(),
        pageT: 500,
        tran: 15
      });
    }
  } catch (e) {}

  // 3. Mock plugins & mime types
  try {
    const mockPlugins = [
      { name: "PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
      { name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
      { name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
      { name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
      { name: "WebKit built-in PDF", filename: "internal-pdf-viewer", description: "Portable Document Format" }
    ];
    Object.defineProperty(navigator, 'plugins', {
      get: () => mockPlugins,
      configurable: true
    });
  } catch (e) {}

  // 4. Overwrite Permissions.query
  try {
    const originalQuery = window.navigator.permissions ? window.navigator.permissions.query : null;
    if (originalQuery) {
      window.navigator.permissions.query = (parameters) => {
        if (parameters && parameters.name === 'notifications') {
          return Promise.resolve({ state: Notification.permission });
        }
        return originalQuery.call(window.navigator.permissions, parameters);
      };
    }
  } catch (e) {}

  // 5. Clean up Chromedriver/CDC variables
  try {
    const cleanCdc = () => {
      for (const key in window) {
        if (key.match(/^[a-z]{3}_[a-zA-Z0-9]{22}_/)) {
          delete window[key];
        }
      }
    };
    cleanCdc();
    document.addEventListener('DOMContentLoaded', cleanCdc);
  } catch (e) {}

  // 6. Make function toString look native
  try {
    const originalToString = Function.prototype.toString;
    Function.prototype.toString = function() {
      if (this === window.chrome.loadTimes || this === window.chrome.csi || this === window.navigator.permissions.query) {
        return "function () { [native code] }";
      }
      return originalToString.call(this);
    };
  } catch (e) {}
})();
"""

# ── DOM Challenge Inspection Script ─────────────────────────────────────────

DETECT_CHALLENGE_JS = r"""
() => {
  const title = (document.title || "").trim();
  const bodyText = (document.body ? document.body.innerText : "") || "";

  // 1. Cloudflare Turnstile & Managed Challenge
  const cfIframe = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div#challenge-stage, .cf-turnstile, div#cf-please-wait, div#challenge-running');
  const isCfTitle = title.includes("Just a moment...") ||
                    title.includes("Attention Required! | Cloudflare") ||
                    title.includes("Verify you are human");
  const isCfText = bodyText.includes("Cloudflare Ray ID") ||
                   bodyText.includes("Checking if the site connection is secure") ||
                   bodyText.includes("needs to review the security of your connection");

  if (cfIframe || isCfTitle || isCfText) {
    let bbox = null;
    if (cfIframe) {
      const rect = cfIframe.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        bbox = { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) };
      }
    }
    return {
      detected: true,
      provider: "cloudflare_turnstile",
      title: title,
      message: "Cloudflare Turnstile verification required.",
      has_element: Boolean(cfIframe),
      bbox: bbox
    };
  }

  // 2. Akamai Bot Challenge
  const akamaiEl = document.querySelector('#akamai-captcha, script[src*="akamai"], .ak-challenge, iframe[src*="captcha-delivery.com"]');
  const isAkamaiTitle = title.includes("Access Denied") && bodyText.includes("Reference #");
  if (akamaiEl || isAkamaiTitle) {
    return {
      detected: true,
      provider: "akamai",
      title: title,
      message: "Akamai Bot Defense challenge detected.",
      has_element: Boolean(akamaiEl),
      bbox: null
    };
  }

  // 3. Google reCAPTCHA
  const recaptchaIframe = document.querySelector('iframe[src*="recaptcha/api2"], div.g-recaptcha, div#recaptcha');
  if (recaptchaIframe) {
    const rect = recaptchaIframe.getBoundingClientRect();
    return {
      detected: true,
      provider: "recaptcha",
      title: title,
      message: "Google reCAPTCHA challenge detected.",
      has_element: true,
      bbox: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }
    };
  }

  // 4. hCaptcha
  const hcaptchaEl = document.querySelector('iframe[src*="hcaptcha.com"], div.h-captcha, div#hcaptcha');
  if (hcaptchaEl) {
    const rect = hcaptchaEl.getBoundingClientRect();
    return {
      detected: true,
      provider: "hcaptcha",
      title: title,
      message: "hCaptcha verification challenge detected.",
      has_element: true,
      bbox: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }
    };
  }

  // 5. AWS WAF Captcha
  const awsEl = document.querySelector('div#aws-waf-captcha, iframe[src*="awswaf"]');
  if (awsEl) {
    return {
      detected: true,
      provider: "aws_waf",
      title: title,
      message: "AWS WAF Captcha barrier detected.",
      has_element: true,
      bbox: null
    };
  }

  // 6. Generic Bot Blocker Text
  if (title.toLowerCase().includes("security check") ||
      (bodyText.includes("Press & Hold") && bodyText.includes("human verification"))) {
    return {
      detected: true,
      provider: "generic_captcha",
      title: title,
      message: "Human verification requested by website.",
      has_element: false,
      bbox: null
    };
  }

  return { detected: false };
}
"""


class AntiBotDetector:
    """Detects anti-bot challenges and manages Human-in-the-Loop (HITL) handover."""

    @staticmethod
    async def inject_stealth(context_or_page: Any) -> None:
        """Injects stealth scripts into the browser context or page."""
        try:
            if hasattr(context_or_page, "add_init_script"):
                await context_or_page.add_init_script(STEALTH_JS)
            elif hasattr(context_or_page, "evaluate"):
                await context_or_page.evaluate(STEALTH_JS)
        except Exception as exc:
            log.debug("failed to inject stealth js: %s", exc)

    @staticmethod
    async def detect(page_or_adapter: Any) -> Dict[str, Any]:
        """Evaluates whether the page currently presents an anti-bot challenge."""
        try:
            page = page_or_adapter
            if not hasattr(page, "evaluate"):
                if hasattr(page, "adapter"):
                    page = getattr(page, "adapter")
                elif hasattr(page, "page"):
                    page = getattr(page, "page")
            if not hasattr(page, "evaluate") and hasattr(page, "_page"):
                page = getattr(page, "_page", page)
            if not hasattr(page, "evaluate"):
                return {"detected": False}
            res = await page.evaluate(DETECT_CHALLENGE_JS)
            if isinstance(res, dict) and res.get("detected"):
                log.info(
                    "Anti-bot challenge detected [%s]: %s (Title: %s)",
                    res.get("provider"),
                    res.get("message"),
                    res.get("title"),
                )
                res["detected_at"] = time.time()
                return res
            return {"detected": False}
        except Exception as exc:
            log.debug("error detecting challenge: %s", exc)
            return {"detected": False, "error": str(exc)}

    @staticmethod
    async def is_challenge_solved(page_or_adapter: Any) -> bool:
        """Checks if a previously detected challenge has disappeared or resolved."""
        detection = await AntiBotDetector.detect(page_or_adapter)
        return not detection.get("detected", False)
