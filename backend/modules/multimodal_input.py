"""
Multi-modal Input Processor
============================
Processes drag-and-drop, paste, and file inputs for the command bar.
Supports:
- Image analysis (OCR, screenshot-to-data, visual QA)
- File ingestion (PDF, CSV, JSON, markdown parsing)
- URL/content extraction from pasted text
- Screenshot-to-code analysis
"""

import base64
import csv
import io
import json
import re
import os
import hashlib
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

import httpx

from backend.core.database import get_db_cursor


@dataclass
class ProcessedInput:
    """Result of processing a multi-modal input."""
    input_type: str  # image, file, text, url, screenshot
    original_name: str
    extracted_text: str = ""
    structured_data: Optional[dict] = None
    entities: List[dict] = None
    summary: str = ""
    confidence: float = 0.0


class MultimodalProcessor:
    """
    Processes images, files, and text into structured data
    using vision models, parsers, and AI analysis.
    """

    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    SUPPORTED_IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'}
    SUPPORTED_FILE_TYPES = {'pdf', 'csv', 'json', 'md', 'txt', 'html', 'xml', 'py', 'js', 'ts'}

    def __init__(self, llm_config: dict = None):
        self.llm_config = llm_config or {
            "baseUrl": "http://localhost:8080/v1",
            "modelId": "gemma-4-12b",
            "apiKey": "",
        }
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def process_image(self, image_data: bytes, filename: str = "image.png",
                             task: str = "analyze") -> ProcessedInput:
        """
        Process an image: extract text, analyze content, or convert to data.

        Args:
            image_data: Raw image bytes
            filename: Original filename (for type detection)
            task: "ocr", "analyze", "extract_data", "fix_website"
        """
        if len(image_data) > self.MAX_IMAGE_SIZE:
            return ProcessedInput(input_type="image", original_name=filename,
                                   summary="Image too large (max 10MB)")

        image_b64 = base64.b64encode(image_data).decode()
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'

        prompts = {
            'ocr': "Extract ALL visible text from this image. Return only the text, preserving layout.",
            'analyze': "Describe this image in detail. What do you see? What's the context?",
            'extract_data': (
                "If this image contains a table, chart, or structured data, extract it as JSON. "
                "Return ONLY the JSON array of objects."
            ),
            'fix_website': (
                "This is a screenshot of a broken website. Identify the visual issues and "
                "provide specific CSS/HTML fixes. Format as JSON: [{issue, fix}]."
            ),
        }

        prompt = prompts.get(task, prompts['analyze'])

        base_url = self.llm_config.get("baseUrl", "http://localhost:8080/v1")
        model_id = self.llm_config.get("modelId", "gemma-4-12b")
        api_key = self.llm_config.get("apiKey", "")

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/{ext};base64,{image_b64}"
                }},
            ]
        }]

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={"model": model_id, "messages": messages, "temperature": 0.1},
            )
            result = resp.json()
            text = result["choices"][0]["message"]["content"]

            structured_data = None
            if task in ('extract_data', 'fix_website'):
                try:
                    json_match = re.search(r'\[[\s\S]*\]', text)
                    if json_match:
                        structured_data = json.loads(json_match.group(0))
                except (json.JSONDecodeError, AttributeError):
                    pass

            return ProcessedInput(
                input_type="image",
                original_name=filename,
                extracted_text=text,
                structured_data=structured_data,
                summary=text[:200],
                confidence=0.8,
            )
        except Exception as e:
            return ProcessedInput(
                input_type="image", original_name=filename,
                summary=f"Image processing failed: {str(e)}",
            )

    async def process_file(self, file_data: bytes, filename: str) -> ProcessedInput:
        """Process a file based on its type."""
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('csv',):
            return self._parse_csv(file_data, filename)
        elif ext in ('json',):
            return self._parse_json(file_data, filename)
        elif ext in ('md', 'txt', 'py', 'js', 'ts', 'html', 'xml'):
            text = file_data.decode('utf-8', errors='replace')
            return ProcessedInput(
                input_type="file", original_name=filename,
                extracted_text=text[:50000],
                summary=f"Parsed {ext} file: {len(text)} characters",
                confidence=1.0,
            )
        else:
            try:
                text = file_data.decode('utf-8', errors='replace')
                return ProcessedInput(
                    input_type="file", original_name=filename,
                    extracted_text=text[:50000],
                    summary=f"Parsed file: {len(text)} characters",
                )
            except Exception:
                return ProcessedInput(
                    input_type="file", original_name=filename,
                    summary=f"Unsupported file type: {ext}",
                )

    def _parse_csv(self, data: bytes, filename: str) -> ProcessedInput:
        """Parse CSV data into structured format."""
        text = data.decode('utf-8', errors='replace')
        try:
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)[:1000]
            headers = reader.fieldnames or []

            return ProcessedInput(
                input_type="file", original_name=filename,
                extracted_text=text[:10000],
                structured_data={'headers': headers, 'rows': rows, 'row_count': len(rows)},
                summary=f"CSV with {len(headers)} columns, {len(rows)} rows",
                confidence=1.0,
            )
        except Exception:
            return ProcessedInput(
                input_type="file", original_name=filename,
                extracted_text=text[:10000],
                summary="CSV parsing failed",
            )

    def _parse_json(self, data: bytes, filename: str) -> ProcessedInput:
        """Parse JSON data into structured format."""
        text = data.decode('utf-8', errors='replace')
        try:
            parsed = json.loads(text)
            return ProcessedInput(
                input_type="file", original_name=filename,
                structured_data=parsed,
                summary=f"JSON parsed: {type(parsed).__name__}",
                confidence=1.0,
            )
        except json.JSONDecodeError:
            return ProcessedInput(
                input_type="file", original_name=filename,
                extracted_text=text[:10000],
                summary="JSON parsing failed",
            )

    async def process_text_input(self, text: str) -> ProcessedInput:
        """
        Process raw text input from the command bar.
        Detects URLs, code snippets, and structured queries.
        """
        url_pattern = re.compile(r'https?://[^\s]+')
        urls = url_pattern.findall(text)

        if urls:
            return ProcessedInput(
                input_type="url", original_name="pasted_text",
                extracted_text=text,
                structured_data={'urls': urls},
                summary=f"Detected {len(urls)} URL(s)",
            )

        # Check if it's code
        code_indicators = ['def ', 'function ', 'import ', 'class ', 'const ', 'let ', 'var ',
                           'async ', 'await ', 'print(', 'console.log(']
        is_code = any(ind in text for ind in code_indicators)

        if is_code:
            return ProcessedInput(
                input_type="text", original_name="pasted_text",
                extracted_text=text,
                summary="Detected code snippet",
            )

        return ProcessedInput(
            input_type="text", original_name="pasted_text",
            extracted_text=text,
            summary=f"Text input: {len(text)} characters",
        )

    async def analyze_screenshot_for_fix(self, image_data: bytes) -> List[dict]:
        """Analyze a website screenshot and suggest fixes."""
        result = await self.process_image(image_data, "screenshot.png", "fix_website")
        return result.structured_data or []

    def is_supported_image(self, filename: str) -> bool:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        return ext in self.SUPPORTED_IMAGE_TYPES

    def is_supported_file(self, filename: str) -> bool:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        return ext in self.SUPPORTED_FILE_TYPES

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Module-level singleton
_processor: Optional[MultimodalProcessor] = None


def get_processor(llm_config: dict = None) -> MultimodalProcessor:
    global _processor
    if _processor is None:
        _processor = MultimodalProcessor(llm_config)
    return _processor
