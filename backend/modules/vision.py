"""
Vision Model Integration
=========================
Real vision model integration for visual grounding.
Sends actual screenshots to vision-capable LLMs (GPT-4V, Claude, local VLMs)
and parses responses for interactive element identification.

Replaces the simulated /vision/grounding with actual computer vision.
"""

import asyncio
import base64
import json
import re
import time
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient


@dataclass
class UIElement:
    """A detected interactive UI element."""
    label: str
    element_type: str  # button, link, input, image, text
    selector: str = ""
    text: str = ""
    bounding_box: Optional[Dict[str, float]] = None  # x, y, width, height (normalized 0-1)
    confidence: float = 0.0
    suggested_action: str = "click"  # click, type, scroll, read


@dataclass
class VisionAnalysis:
    """Complete vision analysis result."""
    url: str
    elements: List[UIElement]
    page_structure: str = ""
    summary: str = ""
    processing_time: float = 0
    model_used: str = ""


class VisionModel:
    """
    Interface to vision-capable language models.
    Supports OpenAI-compatible vision APIs (GPT-4V, LLaVA, local VLMs).
    """

    def __init__(self, base_url: str = "http://localhost:8080/v1", 
                 model_id: str = "gemma-3-12b", api_key: str = ""):
        self.base_url = base_url.rstrip('/')
        self.model_id = model_id
        self.api_key = api_key
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = make_async_client(timeout=60.0)
        return self._http_client

    def _encode_image(self, image_data: bytes) -> str:
        return base64.b64encode(image_data).decode()

    async def analyze_screenshot(self, image_data: bytes, 
                                  prompt: str = None) -> str:
        """
        Send a screenshot to the vision model for analysis.

        Args:
            image_data: Raw PNG/JPEG image bytes
            prompt: Custom analysis prompt

        Returns:
            Model's text response
        """
        if prompt is None:
            prompt = (
                "Analyze this webpage screenshot. Identify all interactive elements "
                "(buttons, links, input fields, dropdowns, checkboxes). For each element, "
                "provide: type, visible text/label, approximate position (x,y as percentage "
                "from top-left), and suggested action. Return as JSON array."
            )

        image_b64 = self._encode_image(image_data)

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]
        }]

        client = await self._get_client()
        endpoint = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        resp = await client.post(endpoint, headers=headers, json={
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2000,
        })

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def ground_elements(self, image_data: bytes) -> List[UIElement]:
        """
        Perform visual grounding: identify UI elements with coordinates.

        Returns:
            List of grounded UIElement objects with position data.
        """
        prompt = (
            "You are a visual grounding assistant. Look at this screenshot and identify "
            "ALL clickable or interactive elements. For each, provide:\n"
            "- type: one of [button, link, input, select, checkbox, icon, menu, tab]\n"
            "- label: the visible text or aria-label\n"
            "- css_selector: a likely CSS selector\n"
            "- position: {x, y, width, height} as percentages (0-100) of the viewport\n"
            "- suggested_action: what action to take (click, type, select, scroll)\n\n"
            "Return ONLY a JSON array like:\n"
            '[{"type":"button","label":"Search","css_selector":"button.search-btn",'
            '"position":{"x":80,"y":10,"width":10,"height":4},"suggested_action":"click"}]'
        )

        try:
            response = await self.analyze_screenshot(image_data, prompt)
            elements = self._parse_element_json(response)
            return elements
        except Exception:
            return []

    async def extract_text(self, image_data: bytes) -> str:
        """Extract all visible text from a screenshot using OCR via vision model."""
        prompt = "Extract ALL visible text from this screenshot. Preserve the order and layout. Return only the text."
        return await self.analyze_screenshot(image_data, prompt)

    async def compare_screenshots(self, before: bytes, after: bytes) -> str:
        """
        Compare two screenshots to detect changes.
        Useful for verifying actions were performed.
        """
        prompt = (
            "I'm showing you two screenshots: BEFORE and AFTER a user action. "
            "What changed between them? Describe specific UI differences."
        )

        before_b64 = self._encode_image(before)
        after_b64 = self._encode_image(after)

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{before_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{after_b64}"}},
            ]
        }]

        client = await self._get_client()
        endpoint = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        resp = await client.post(endpoint, headers=headers, json={
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.1,
        })

        return resp.json()["choices"][0]["message"]["content"]

    def _parse_element_json(self, text: str) -> List[UIElement]:
        """Parse JSON element descriptions from model response."""
        # Extract JSON array from response
        json_match = re.search(r'\[[\s\S]*\]', text)
        if not json_match:
            return []

        try:
            raw_elements = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            return []

        elements = []
        for raw in raw_elements:
            pos = raw.get('position', {})
            elements.append(UIElement(
                label=raw.get('label', ''),
                element_type=raw.get('type', 'unknown'),
                selector=raw.get('css_selector', raw.get('selector', '')),
                text=raw.get('text', ''),
                bounding_box={
                    'x': float(pos.get('x', 0)),
                    'y': float(pos.get('y', 0)),
                    'width': float(pos.get('width', 0)),
                    'height': float(pos.get('height', 0)),
                } if pos else None,
                confidence=float(raw.get('confidence', 0.5)),
                suggested_action=raw.get('suggested_action', 'click'),
            ))
        return elements

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class VisionGrounder:
    """
    High-level visual grounding orchestrator.
    Combines screenshot capture with vision model analysis
    to produce actionable element maps.
    """

    def __init__(self, vision_model: VisionModel = None):
        self._model = vision_model or VisionModel()

    async def ground_page(self, screenshot_data: bytes, 
                           page_url: str = "") -> VisionAnalysis:
        """
        Analyze a page screenshot and produce grounded elements.

        Args:
            screenshot_data: Raw screenshot bytes
            page_url: URL of the page (for context)

        Returns:
            VisionAnalysis with detected elements
        """
        start = time.time()
        elements = await self._model.ground_elements(screenshot_data)

        return VisionAnalysis(
            url=page_url,
            elements=elements,
            page_structure=f"Detected {len(elements)} interactive elements",
            summary=f"Found {len(elements)} elements: " + 
                    ", ".join(e.label for e in elements[:5]),
            processing_time=time.time() - start,
            model_used=self._model.model_id,
        )

    def elements_to_suggestions(self, analysis: VisionAnalysis) -> List[dict]:
        """Convert vision elements to the expected suggestion format."""
        return [
            {
                "label": f"{'🔍' if e.suggested_action == 'click' else '📝'} {e.label or e.text or 'Element'}",
                "action": e.suggested_action,
                "selector": e.selector,
                "type": e.element_type,
                "position": e.bounding_box,
                "confidence": e.confidence,
            }
            for e in analysis.elements
        ]


# ---- Module-level convenience ----

_vision_model: Optional[VisionModel] = None


def get_vision_model(base_url: str = None, model_id: str = None, 
                      api_key: str = None) -> VisionModel:
    global _vision_model
    if _vision_model is None:
        _vision_model = VisionModel(
            base_url=base_url or "http://localhost:8080/v1",
            model_id=model_id or "gemma-3-12b",
            api_key=api_key or "",
        )
    return _vision_model
