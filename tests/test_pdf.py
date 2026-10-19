"""
PDF text extraction.

The fixture is a real, minimal PDF built byte-by-byte here rather than fetched,
so the extraction test runs offline and cannot be fooled by a stub. The xref
offsets are computed as the file is assembled, because a PDF with a wrong xref
is not a PDF.
"""

import pytest


def make_pdf(*page_texts, password: str = None) -> bytes:
    """Build a minimal valid multi-page PDF containing the given text."""
    objects = []  # 1-based objects, appended in order

    # 1: catalog (placeholder, filled after we know the pages object number)
    # 2: pages
    # 3..: one page + one content stream per page, plus one shared font
    n_pages = len(page_texts) or 1
    font_obj = 3 + n_pages * 2  # page objs and content objs come first

    page_obj_nums = [3 + i * 2 for i in range(n_pages)]
    content_obj_nums = [4 + i * 2 for i in range(n_pages)]

    objects.append(
        f"<< /Type /Catalog /Pages 2 0 R >>"
    )
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")

    for i, text in enumerate(page_texts or (" ",)):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_obj_nums[i]} 0 R "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> >>"
        )
        stream = f"BT /F1 24 Tf 72 700 Td ({_escape(text)}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")

    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Assemble with a correct xref table.
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")

    xref_start = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _escape(text: str) -> str:
    return str(text).replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestDetection:
    def test_url_extension(self):
        from backend.modules.pdf import is_pdf_url
        assert is_pdf_url("https://x.test/report.pdf")
        assert is_pdf_url("https://x.test/report.PDF")
        assert is_pdf_url("https://x.test/report.pdf?token=1#page=2")
        assert not is_pdf_url("https://x.test/report.pdf.html")
        assert not is_pdf_url("https://x.test/page")

    def test_content_type_is_authoritative(self):
        """Many servers serve PDFs as octet-stream, so the header decides when
        it is present, and the extension is only the fallback."""
        from backend.modules.pdf import looks_like_pdf
        assert looks_like_pdf("https://x.test/download", "application/pdf")
        assert looks_like_pdf("https://x.test/download", "application/pdf; charset=binary")
        assert looks_like_pdf("https://x.test/download", "APPLICATION/PDF")
        assert looks_like_pdf("https://x.test/report.pdf", "application/octet-stream")
        assert looks_like_pdf("https://x.test/report.pdf", None)
        assert not looks_like_pdf("https://x.test/page", "text/html")

    def test_empty_inputs_are_not_pdfs(self):
        from backend.modules.pdf import is_pdf_url, looks_like_pdf
        assert not is_pdf_url("")
        assert not looks_like_pdf("", "")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_extracts_text_from_a_single_page(self):
        from backend.modules.pdf import extract
        result = extract(make_pdf("Hello PDF world"))
        assert result.ok
        assert "Hello PDF world" in result.text
        assert result.pages == 1
        assert result.pages_read == 1
        assert not result.problems

    def test_extracts_from_multiple_pages_with_markers(self):
        from backend.modules.pdf import extract
        result = extract(make_pdf("First page", "Second page", "Third page"))
        assert result.pages == 3
        for expected in ("First page", "Second page", "Third page"):
            assert expected in result.text
        assert "--- page 2 ---" in result.text

    def test_max_pages_is_respected(self):
        from backend.modules.pdf import extract
        result = extract(make_pdf("a", "b", "c", "d"), max_pages=2)
        assert result.pages == 4
        assert result.pages_read == 2
        assert "c" not in result.text.replace("--- page 1 ---", "")

    def test_max_chars_truncates_and_flags_it(self):
        from backend.modules.pdf import extract
        result = extract(make_pdf("word " * 500), max_chars=100)
        assert result.truncated is True
        assert len(result.text) <= 120

    def test_non_pdf_bytes_are_rejected_clearly(self):
        from backend.modules.pdf import extract
        result = extract(b"<html>this is a web page</html>")
        assert not result.ok
        assert any("not a PDF" in p for p in result.problems)

    def test_empty_body_is_reported(self):
        from backend.modules.pdf import extract
        result = extract(b"")
        assert not result.ok
        assert result.problems

    def test_corrupt_pdf_does_not_raise(self):
        """A PDF that cannot be parsed must return a result, not throw — an
        exception would tempt the caller to decode the bytes as text."""
        from backend.modules.pdf import extract
        result = extract(b"%PDF-1.4\nthis is not a valid pdf body at all")
        assert isinstance(result.to_dict()["text"], str)
        assert result.text == "" or result.problems

    def test_result_is_serialisable(self):
        from backend.modules.pdf import extract
        d = extract(make_pdf("x")).to_dict()
        assert set(d) >= {"text", "pages", "pages_read", "truncated", "encrypted",
                          "no_text_layer", "problems"}

    def test_library_missing_is_explicit(self, monkeypatch):
        """A missing pypdf must raise a named error, not silently return junk."""
        import builtins
        from backend.modules import pdf

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "pypdf":
                raise ImportError("no pypdf")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(pdf.PdfUnavailable):
            pdf.extract(make_pdf("x"))


# ---------------------------------------------------------------------------
# Wiring into the web-data surface
# ---------------------------------------------------------------------------

class TestPdfWiring:
    def test_pdf_to_text_includes_a_header_and_the_text(self):
        from backend.routes.webdata import _pdf_to_text
        out = _pdf_to_text("https://x.test/r.pdf", make_pdf("Extracted body"))
        assert "Extracted body" in out
        assert out.startswith("[PDF document")

    def test_pdf_to_text_reports_a_scanned_document(self):
        """A PDF with no text layer is a different fact from a parse failure."""
        from backend.routes.webdata import _pdf_to_text
        out = _pdf_to_text("https://x.test/scan.pdf", make_pdf(" "))
        assert "scanned" in out.lower()

    def test_pdf_to_text_never_returns_raw_bytes(self):
        from backend.routes.webdata import _pdf_to_text
        out = _pdf_to_text("https://x.test/bad.pdf", b"%PDF-1.4\nnonsense")
        assert "%PDF" not in out
        assert isinstance(out, str)
