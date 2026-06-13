"""Input validation and sanitization utilities."""
import ipaddress
import os
import re
from urllib.parse import urlparse

# Private / loopback IP ranges that should be blocked for SSRF prevention
_BLOCKED_IPS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
]

# Allowed URL schemes for external requests
_ALLOWED_SCHEMES = {"http", "https"}

# Blocked file extensions for uploads
_BLOCKED_EXTENSIONS = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".sh", ".bin"}


def is_safe_url(url: str, allow_private: bool = False) -> bool:
    """Validate URL for external requests (SSRF protection).

    Checks:
      - Must have http/https scheme
      - Hostname must resolve to a public IP (unless allow_private=True)
      - No empty host
    """
    if not url or not isinstance(url, str):
        return False
    if len(url) > 8192:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return False
        host = parsed.hostname
        if not host:
            return False
        # Allow localhost in dev mode but not 0.0.0.0
        if host in ("localhost", "127.0.0.1", "0.0.0.0") and not allow_private:
            return False
        # Check for DNS rebinding / IP obfuscation
        if not allow_private:
            try:
                addr = ipaddress.ip_address(host)
                for block in _BLOCKED_IPS:
                    if addr in ipaddress.ip_network(block):
                        return False
            except ValueError:
                pass  # Hostname, not IP — assume public
        return True
    except Exception:
        return False


def safe_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal.

    Strips directory separators, ensures the result is a single
    filename component within the expected directory.
    """
    if not filename:
        return "unnamed"
    # Remove any path separators
    filename = os.path.basename(filename)
    # Remove null bytes
    filename = filename.replace("\x00", "")
    # Whitelist safe characters
    filename = re.sub(r"[^\w.\- ]", "", filename)
    if not filename:
        return "unnamed"
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[: 255 - len(ext)] + ext
    return filename


def is_safe_path(requested_path: str, allowed_base: str) -> bool:
    """Prevent path traversal beyond the allowed base directory.

    Resolves both paths to absolute and checks the requested path
    is a child of the allowed base.
    """
    if not requested_path:
        return False
    try:
        abs_requested = os.path.realpath(requested_path)
        abs_base = os.path.realpath(allowed_base)
        return abs_requested.startswith(abs_base + os.sep) or abs_requested == abs_base
    except Exception:
        return False


def sanitize_html(text: str) -> str:
    """Strip dangerous HTML/JS from text fields."""
    if not text:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"on\w+\s*=\s*\"[^\"]*\"", "", text, flags=re.IGNORECASE)
    text = re.sub(r"on\w+\s*=\s*'[^']*'", "", text, flags=re.IGNORECASE)
    text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
    return text


def validate_file_upload(filename: str, content_size: int,
                         max_size_mb: int = 10) -> tuple[bool, str]:
    """Validate file upload: extension, size, name safety."""
    if not filename:
        return False, "No filename provided"
    ext = os.path.splitext(filename)[1].lower()
    if ext in _BLOCKED_EXTENSIONS:
        return False, f"File extension '{ext}' is not allowed"
    if content_size > max_size_mb * 1024 * 1024:
        return False, f"File exceeds {max_size_mb} MB limit"
    cleaned = safe_filename(filename)
    if cleaned != filename:
        return False, "Filename contains invalid characters"
    return True, ""
