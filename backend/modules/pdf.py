"""
PDF text extraction
===================

A `.pdf` URL used to be flagged by ``scraper.is_special_media`` and then handed
"Unsupported special media." — the file was detected and never read. This module
is the actual reader.

Kept deliberately small and pure: it takes bytes and returns text, so it can be
tested without a network, a URL, or an HTTP client.

Two honesty notes:

* **Extraction is best-effort.** A PDF is a container, not a text format. A
  scanned document has no text layer at all and needs OCR, which this does not
  do — it reports that instead of returning an empty string that looks like an
  empty page.
* **The library is optional.** ``pypdf`` is pure Python and declared, but if it
  is missing the failure is explicit rather than a silent fall back to binary
  garbage decoded as text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("jambu.pdf")

DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_CHARS = 200_000


class PdfUnavailable(RuntimeError):
    """The PDF library is not installed."""


@dataclass
class PdfExtraction:
    """What came out of a PDF."""

    text: str
    pages: int = 0
    pages_read: int = 0
    truncated: bool = False
    encrypted: bool = False
    no_text_layer: bool = False
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) or self.no_text_layer

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "pages": self.pages,
            "pages_read": self.pages_read,
            "truncated": self.truncated,
            "encrypted": self.encrypted,
            "no_text_layer": self.no_text_layer,
            "problems": list(self.problems),
        }


def is_pdf_url(url: str) -> bool:
    """Whether a URL's path ends in .pdf (query strings and fragments ignored)."""
    if not url:
        return False
    path = url.split("#", 1)[0].split("?", 1)[0].lower()
    return path.endswith(".pdf")


def is_pdf_content_type(content_type: Optional[str]) -> bool:
    return bool(content_type) and "application/pdf" in str(content_type).lower()


def looks_like_pdf(url: str, content_type: Optional[str] = None) -> bool:
    """Whether to treat a response as a PDF.

    Content type is authoritative when present; the extension is the fallback,
    because a great many servers serve PDFs as ``application/octet-stream``.
    """
    if is_pdf_content_type(content_type):
        return True
    if is_pdf_url(url):
        return True
    return False


def _load_reader():
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise PdfUnavailable(
            "pypdf is not installed. Install with: pip install pypdf"
        ) from e
    return PdfReader


def extract(
    data: bytes,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
    password: Optional[str] = None,
) -> PdfExtraction:
    """Extract text from PDF bytes.

    Never raises for content reasons: a PDF that cannot be parsed comes back as
    a result with ``problems`` and no text, because an exception here would
    tempt a caller to fall back to decoding the raw bytes as text — which is
    exactly the behaviour this module replaces.
    """
    result = PdfExtraction(text="")
    if not data:
        result.problems.append("empty response body")
        return result

    if not data[:5] == b"%PDF-":
        result.problems.append("response is not a PDF (missing %PDF- header)")
        return result

    PdfReader = _load_reader()

    try:
        reader = PdfReader(_as_stream(data))
    except Exception as e:  # noqa: BLE001
        result.problems.append(f"could not parse the PDF: {type(e).__name__}: {e}")
        return result

    if getattr(reader, "is_encrypted", False):
        result.encrypted = True
        try:
            if not reader.decrypt(password or ""):
                result.problems.append("PDF is encrypted and the password was not provided")
                return result
            result.encrypted = False
        except Exception as e:  # noqa: BLE001
            result.problems.append(f"encrypted PDF could not be decrypted: {type(e).__name__}")
            return result

    try:
        result.pages = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        result.problems.append(f"could not count pages: {type(e).__name__}")
        return result

    chunks: List[str] = []
    total = 0
    for index in range(min(result.pages, max_pages)):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as e:  # noqa: BLE001 — one bad page must not lose the rest
            result.problems.append(f"page {index + 1} failed to extract: {type(e).__name__}")
            continue
        result.pages_read += 1
        page_text = page_text.strip()
        if not page_text:
            continue
        chunk = f"\n\n--- page {index + 1} ---\n\n{page_text}"
        if total + len(chunk) > max_chars:
            chunks.append(chunk[: max(0, max_chars - total)])
            result.truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)

    result.text = "".join(chunks).strip()
    if not result.text:
        # Distinguish "nothing here" from "we could not read it": a scanned PDF
        # has no text layer and needs OCR, which is a different message to the
        # user than a parser failure.
        result.no_text_layer = result.pages_read > 0
        if result.no_text_layer:
            result.problems.append(
                "no text layer found — this is probably a scanned document and would need OCR"
            )
    return result


def _as_stream(data: bytes):
    from io import BytesIO

    return BytesIO(data)
