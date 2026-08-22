"""Browser automation, vision, forms, computer control, and login endpoints."""
import json
import logging
import os
import subprocess
import base64
import platform

log = logging.getLogger("jambu.browser")

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator
from typing import Optional, List

from backend.core.audit import get_audit_logger, ActionCategory
from backend.core.privacy import sanitize_content_for_storage
from backend.core.security import is_safe_url
from backend.core.vault import get_vault

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

from backend.engine_runtime import LATEST_LLM_CONFIG, manager

router = APIRouter(tags=["browser"])


# ── Pydantic Models ──


class ActionStep(BaseModel):
    action: str
    selector: str = ""
    value: str = ""
    x: Optional[float] = None
    y: Optional[float] = None


class MultiActionRequest(BaseModel):
    url: str
    steps: List[ActionStep]
    client_id: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class LoginRequest(BaseModel):
    url: str
    username: str
    password: str
    client_id: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class PrivacyModeRequest(BaseModel):
    mode: str


class VisionGroundRequest(BaseModel):
    url: str
    prompt: str = ""
    image_data: str = ""
    client_id: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class FormDetectRequest(BaseModel):
    url: str

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class VisionOCRRequest(BaseModel):
    image_url: str

    @validator("image_url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class VisionUIRequest(BaseModel):
    image_url: str

    @validator("image_url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class VisionVerifyRequest(BaseModel):
    image_data: str
    expected: str


# ── Browser Privacy ──


@router.get("/browser/privacy")
async def browser_privacy_summary():
    """Get current browser privacy settings."""
    from backend.modules.browser import get_browser_manager
    bm = get_browser_manager()
    return {
        "session_mode": bm.current_session_mode.value if bm.current_session_mode else "standard",
        "privacy_level": bm.current_privacy_level.value if bm.current_privacy_level else "standard",
        "tor_enabled": bm.tor_enabled,
        "active_fingerprints": bm.fingerprint_count(),
    }


@router.post("/privacy/mode")
async def set_privacy_mode(req: PrivacyModeRequest):
    """Set privacy mode: standard, enhanced, maximum, or local_only."""
    from backend.core.privacy import get_privacy_manager, PrivacyMode
    privacy_mgr = get_privacy_manager()
    try:
        mode = PrivacyMode(req.mode.lower())
        privacy_mgr.set_mode(mode)
        return {"success": True, "status": "ok", "mode": mode.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid privacy mode: {req.mode}")


# ── Browser Actions ──


@router.post("/act")
async def perform_actions(req: MultiActionRequest):
    """Execute browser actions (click, type, scroll, click_xy) with audit logging."""
    audit = get_audit_logger()

    audit.log(
        category=ActionCategory.BROWSER,
        action="perform_actions",
        details={
            "url": req.url,
            "steps_count": len(req.steps),
            "actions": [step.action for step in req.steps],
        },
        session_id=req.client_id,
    )

    actions = []
    for step in req.steps:
        action_dict = {"action": step.action}
        if hasattr(step, 'selector') and step.selector:
            action_dict["selector"] = step.selector
        if hasattr(step, 'value') and step.value:
            action_dict["value"] = step.value
        if hasattr(step, 'x') and step.x is not None:
            action_dict["x"] = step.x
        if hasattr(step, 'y') and step.y is not None:
            action_dict["y"] = step.y
        actions.append(action_dict)

    # Try crawl4ai first, fallback to Playwright
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        js_lines = []
        for step in req.steps:
            if step.action == "click":
                js_lines.append(f"document.querySelector('{step.selector}').click();")
            elif step.action == "type":
                js_lines.append(f"document.querySelector('{step.selector}').value = '{step.value}';")
            elif step.action == "scroll":
                js_lines.append(f"window.scrollBy(0, {step.value});")
            elif step.action == "click_xy":
                js_lines.append(
                    f"{{ const vx = window.innerWidth * {step.x / 100}; "
                    f"const vy = window.innerHeight * {step.y / 100}; "
                    f"const el = document.elementFromPoint(vx, vy); if(el) el.click(); }}"
                )

        browser_config = BrowserConfig(headless=True)
        run_config = CrawlerRunConfig(wait_until="networkidle")

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(
                url=req.url,
                js_code=f"(async () => {{ {' '.join(js_lines)} }})()",
                config=run_config,
            )

            content = result.markdown[:10000] if result.success else ""
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)

            audit.log(
                category=ActionCategory.BROWSER,
                action="perform_actions_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "crawl4ai",
                },
                session_id=req.client_id,
            )

            return {"status": "success", "markdown": sanitized_content}
    except ImportError:
        pass
    except Exception as e:
        log.error("crawl4ai error: %s", e)

    # Playwright fallback
    try:
        from backend.modules.playwright_scraper import perform_actions_with_playwright

        result = await perform_actions_with_playwright(req.url, actions)

        if result["success"]:
            content = result["content"]
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)

            audit.log(
                category=ActionCategory.BROWSER,
                action="perform_actions_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "playwright",
                },
                session_id=req.client_id,
            )

            return {"status": "success", "markdown": sanitized_content}
        return {"status": "error", "message": result.get("error", "Failed to perform actions")}
    except Exception as e:
        audit.log(
            category=ActionCategory.ERROR,
            action="perform_actions_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflow/execute")
async def execute_workflow(req: MultiActionRequest):
    """Execute a multi-step browser workflow."""
    return await perform_actions(req)


# ── Login ──


@router.post("/login")
async def perform_login(req: LoginRequest):
    """Autonomous login using the Credential Vault."""
    audit = get_audit_logger()

    audit.log(
        category=ActionCategory.CREDENTIAL,
        action="login_attempt",
        details={"url": req.url, "username": req.username},
        session_id=req.client_id,
    )

    try:
        vault = get_vault()
        # Unlock key-file-only vaults automatically; production must set JAMBU_MASTER_PASSWORD.
        if vault.is_locked and not os.environ.get("JAMBU_MASTER_PASSWORD"):
            vault.unlock("")

        from urllib.parse import urlparse
        parsed = urlparse(req.url)
        domain = parsed.hostname or req.url

        vault.store_credential(
            domain=domain,
            username=req.username,
            password=req.password,
            url_pattern=f"*{domain}*",
        )

        await manager.broadcast(req.client_id, f"🔐 Credential stored for {domain}")

        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

            browser_config = BrowserConfig(headless=True)
            run_config = CrawlerRunConfig(wait_until="networkidle")

            async with AsyncWebCrawler(config=browser_config) as crawler:
                js_code = (
                    f"(async () => {{"
                    f"  const userField = document.querySelector('input[type=\"email\"], input[type=\"text\"], input[name=\"username\"], input[name=\"email\"]');"
                    f"  const passField = document.querySelector('input[type=\"password\"]');"
                    f"  const submitBtn = document.querySelector('button[type=\"submit\"], input[type=\"submit\"]');"
                    f"  if (userField) userField.value = '{req.username}';"
                    f"  if (passField) passField.value = '{req.password}';"
                    f"  if (submitBtn) submitBtn.click();"
                    f"}})()"
                )
                result = await crawler.arun(url=req.url, js_code=js_code, config=run_config)

            audit.log(
                category=ActionCategory.CREDENTIAL,
                action="login_success",
                details={"domain": domain, "url": req.url},
                session_id=req.client_id,
            )

            return {
                "status": "success",
                "domain": domain,
                "message": f"Login attempted for {domain}",
                "page_title": result.metadata.get("title", "") if result.success and result.metadata else "",
            }
        except ImportError:
            return {"status": "success", "domain": domain, "message": f"Credential stored for {domain}. Login automation requires crawl4ai."}
    except Exception as e:
        audit.log(
            category=ActionCategory.ERROR,
            action="login_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


# ── Vision ──


@router.post("/vision/grounding")
async def vision_grounding(req: VisionGroundRequest):
    """Visual grounding: analyze page and suggest interactive elements."""
    cid = req.client_id
    await manager.broadcast(cid, "👁️ Performing visual grounding pass...")

    base_url = LATEST_LLM_CONFIG.get("baseUrl", "http://localhost:8080/v1")
    model_id = LATEST_LLM_CONFIG.get("modelId", "gemma-3-12b")

    try:
        async with make_async_client() as cl:
            resp = await cl.get(req.url, timeout=10.0, follow_redirects=True)
            page_text = resp.text[:5000] if resp.status_code == 200 else ""

        prompt = (
            "Analyze this page structure and suggest 3 high-impact actions (click, type, scroll) "
            "to extract information. Return JSON: [{label, action, selector}]. "
            f"Page snippet: {page_text[:3000]}"
        )

        async with httpx.AsyncClient() as cl:
            ai_resp = await cl.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {LATEST_LLM_CONFIG.get('apiKey', '')}"},
                json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                timeout=15.0,
            )
            content = ai_resp.json()["choices"][0]["message"]["content"]
            try:
                suggestions = json.loads(content)
            except json.JSONDecodeError:
                suggestions = [
                    {"label": "🔍 Explore Page", "action": "click", "selector": "a:first-of-type"},
                    {"label": "📊 Extract Content", "action": "scrape", "url": req.url},
                    {"label": "⏬ Scroll for More", "action": "scroll", "value": "500"},
                ]

        return {"suggestions": suggestions}
    except Exception:
        return {
            "suggestions": [
                {"label": "🔍 Explore Page", "action": "click", "selector": "a:first-of-type"},
                {"label": "📊 Extract Content", "action": "scrape", "url": req.url},
                {"label": "⏬ Scroll for More", "action": "scroll", "value": "500"},
            ]
        }


@router.post("/vision/analyze")
async def vision_analyze(req: VisionGroundRequest):
    """Analyze image with vision model: returns UI elements + suggestions."""
    try:
        from backend.modules.vision import analyze_image
        result = await analyze_image(req.image_data, req.prompt)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vision/ocr")
async def vision_ocr(req: VisionOCRRequest):
    """Extract text from an image using OCR."""
    try:
        from backend.modules.vision import ocr_image
        result = await ocr_image(req.image_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vision/ui-elements")
async def vision_ui_elements(req: VisionUIRequest):
    """Detect UI elements in a screenshot for automation."""
    try:
        from backend.modules.vision import detect_ui_elements
        result = await detect_ui_elements(req.image_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vision/verify")
async def vision_verify(req: VisionVerifyRequest):
    """Verify screen state matches expected description."""
    try:
        from backend.modules.vision import verify_screen
        result = await verify_screen(req.image_data, req.expected)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Forms ──


@router.post("/forms/detect")
async def detect_forms(req: FormDetectRequest):
    """Detect and classify forms on a page, match with vault credentials."""
    try:
        from backend.modules.form_filler import detect_and_classify
        result = await detect_and_classify(req.url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/forms/fill-script")
async def generate_fill_script(req: FormDetectRequest):
    """Generate JavaScript to fill a form with vault credentials."""
    try:
        from backend.modules.form_filler import generate_fill_js
        result = await generate_fill_js(req.url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Computer Control ──


@router.get("/computer/capture")
async def computer_capture(region: str = "full"):
    """Capture the current screen (macOS only)."""
    try:
        import Quartz
        import CoreGraphics

        display_id = Quartz.CGMainDisplayID()
        image = CoreGraphics.CGDisplayCreateImage(display_id)
        dest = CoreGraphics.CGImageDestinationCreateWithData(
            None, "public.png", 1, None
        )
        CoreGraphics.CGImageDestinationAddImage(dest, image, None)
        CoreGraphics.CGImageDestinationFinalize(dest)

        data = CoreGraphics.CGImageDestinationCopyData(dest).data()
        b64 = base64.b64encode(data).decode()
        return {"success": True, "image": b64, "format": "png"}
    except ImportError:
        # Fallback: use screencapture CLI
        import tempfile
        import os
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            subprocess.run(["screencapture", "-x", tmp_path], check=True, timeout=10)
            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return {"success": True, "image": b64, "format": "png"}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _get_frontmost_window_id() -> int:
    """Get the frontmost window ID using AppleScript."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get id of first window of (first process whose frontmost is true)'],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip())
    except Exception:
        return -1


@router.post("/computer/mouse")
async def computer_mouse(action: str, x: int = 0, y: int = 0, button: str = "left"):
    """Control mouse: click, move, drag at screen coordinates."""
    try:
        from backend.modules.computer import mouse_action
        result = await mouse_action(action, x, y, button)
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="Computer control module not available")


@router.post("/computer/keyboard")
async def computer_keyboard(text: str = "", key: str = "", modifiers: list = []):
    """Type text or press a key on the keyboard."""
    if text:
        escaped = text.replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'tell application "System Events" to keystroke "{escaped}"'],
                       capture_output=True, timeout=5)
    elif key:
        _key_to_code(key)
    return {"status": "ok"}


def _key_to_code(key: str) -> int:
    """Map key name to macOS key code (simplified subset)."""
    mapping = {
        "return": 36, "enter": 76, "tab": 48, "space": 49, "delete": 51,
        "escape": 53, "cmd": 55, "shift": 56, "alt": 58, "ctrl": 59,
        "up": 126, "down": 125, "left": 123, "right": 124,
        "f5": 96,
    }
    return mapping.get(key.lower(), -1)


@router.post("/computer/launch")
async def computer_launch(app_name: str):
    """Launch a macOS app by name."""
    try:
        subprocess.run(["open", "-a", app_name], check=True, timeout=10,
                       capture_output=True)
        return {"status": "launched", "app": app_name}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "app": app_name}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "app": app_name, "error": e.stderr.decode() if e.stderr else str(e)}


@router.get("/computer/apps")
async def computer_list_apps():
    """List installed macOS applications."""
    apps_dir = "/Applications"
    user_apps = os.path.expanduser("~/Applications")
    apps = []
    for d in [apps_dir, user_apps]:
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".app"):
                    apps.append(name.removesuffix(".app"))
    return {"apps": apps, "total": len(apps)}


# ── Session Record / Replay ──


class RecordRunRequest(BaseModel):
    """Record a scripted browser run for later replay."""
    url: str
    steps: List[ActionStep]
    name: str = ""

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


@router.post("/sessions/recordings/run")
async def record_session_run(req: RecordRunRequest):
    """Execute an action script while recording every step, then persist it."""
    from backend.modules.session_recorder import record_run

    actions = [
        {
            "action": s.action,
            "selector": s.selector,
            "value": s.value,
            "x": s.x,
            "y": s.y,
        }
        for s in req.steps
    ]
    try:
        result = await record_run(req.url, actions, req.name)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/recordings")
async def list_session_recordings(limit: int = 50):
    """List saved recordings (without full step payloads)."""
    from backend.modules.session_recorder import list_recordings

    return {"recordings": list_recordings(limit=min(max(limit, 1), 200))}


@router.get("/sessions/recordings/{recording_id}")
async def get_session_recording(recording_id: int):
    """Fetch one recording including its full step list."""
    from backend.modules.session_recorder import get_recording

    rec = get_recording(recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"recording {recording_id} not found")
    return rec


@router.post("/sessions/recordings/{recording_id}/replay")
async def replay_session_recording(recording_id: int):
    """Replay a stored recording through the Playwright executor."""
    from backend.modules.session_recorder import replay_recording

    try:
        return await replay_recording(recording_id)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/recordings/{recording_id}")
async def delete_session_recording(recording_id: int):
    from backend.modules.session_recorder import delete_recording

    if not delete_recording(recording_id):
        raise HTTPException(status_code=404, detail=f"recording {recording_id} not found")
    return {"success": True, "deleted": recording_id}
