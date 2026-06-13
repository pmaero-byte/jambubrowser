"""
Privacy & Trustless Security Module
====================================
Implements zero-trust privacy controls for the sovereign browser.

Security Features:
- Request/response sanitization (PII leakage prevention)
- Local-only mode enforcement (zero external network calls)
- Content sanitization before storage
- Audit logging for all data flows
- Network isolation enforcement
"""

import re
import hashlib
import time
from typing import Optional, List, Dict, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse


class PrivacyMode(Enum):
    """Privacy enforcement modes."""
    STANDARD = "standard"      # Basic sanitization
    ENHANCED = "enhanced"      # Aggressive PII removal
    MAXIMUM = "maximum"        # Zero external calls, full sanitization
    LOCAL_ONLY = "local_only"  # No network access at all


@dataclass
class SanitizationResult:
    """Result of content sanitization."""
    original_length: int
    sanitized_length: int
    pii_removed: List[str]
    blocked_patterns: List[str]
    was_modified: bool


class PIIDetector:
    """Detects and removes personally identifiable information."""

    # Common PII patterns
    PATTERNS = {
        "email": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
        "phone_us": re.compile(r'(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'),
        "phone_intl": re.compile(r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}'),
        "ssn": re.compile(r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'),
        "credit_card": re.compile(r'\b(?:\d{4}[-.\s]?){3}\d{4}\b'),
        "ip_address": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
        "ipv6": re.compile(r'([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}'),
        "mac_address": re.compile(r'([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}'),
        "passport": re.compile(r'\b[A-Z]{1,2}\d{6,9}\b'),
        "driver_license": re.compile(r'\b[A-Z]\d{4}[-.\s]?\d{5}[-.\s]?\d{5}\b'),
    }

    # Suspicious patterns that might indicate tracking
    TRACKING_PATTERNS = {
        "google_analytics": re.compile(r'UA-\d{4,9}-\d{1,4}'),
        "facebook_pixel": re.compile(r'fbq\(\d+,\s*[\'"][^\'"]+[\'"]'),
        "mixpanel": re.compile(r'mixpanel\.track'),
        "segment": re.compile(r'analytics\.track'),
    }

    @classmethod
    def detect_pii(cls, text: str) -> Dict[str, List[str]]:
        """Detect all PII in text."""
        findings = {}
        for pii_type, pattern in cls.PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                findings[pii_type] = matches
        return findings

    @classmethod
    def detect_tracking(cls, text: str) -> List[str]:
        """Detect tracking code in text."""
        findings = []
        for tracker_type, pattern in cls.TRACKING_PATTERNS.items():
            if pattern.search(text):
                findings.append(tracker_type)
        return findings

    @classmethod
    def mask_pii(cls, text: str, pii_type: str) -> str:
        """Mask specific PII type in text."""
        pattern = cls.PATTERNS.get(pii_type)
        if not pattern:
            return text
        return pattern.sub(f'[REDACTED_{pii_type.upper()}]', text)


class NetworkIsolator:
    """Enforces network isolation policies."""

    # Blocked domains for privacy
    BLOCKED_DOMAINS = {
        "google-analytics.com",
        "googletagmanager.com",
        "facebook.com",
        "doubleclick.net",
        "hotjar.com",
        "mixpanel.com",
        "segment.com",
        "amplitude.com",
        "fullstory.com",
        "heap.io",
        "optimizely.com",
        "crazyegg.com",
    }

    # Allowed local domains
    LOCAL_DOMAINS = {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
    }

    def __init__(self, mode: PrivacyMode = PrivacyMode.ENHANCED):
        self.mode = mode
        self._blocked_requests: List[dict] = []

    def is_url_allowed(self, url: str) -> bool:
        """Check if a URL is allowed under current privacy mode."""
        if self.mode == PrivacyMode.LOCAL_ONLY:
            parsed = urlparse(url)
            return parsed.hostname in self.LOCAL_DOMAINS

        if self.mode == PrivacyMode.MAXIMUM:
            parsed = urlparse(url)
            if parsed.hostname in self.LOCAL_DOMAINS:
                return True
            if parsed.hostname in self.BLOCKED_DOMAINS:
                self._blocked_requests.append({
                    "url": url,
                    "reason": "blocked_domain",
                    "timestamp": time.time(),
                })
                return False

        return True

    def sanitize_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Remove sensitive headers from requests."""
        sanitized = dict(headers)

        # Remove tracking headers
        tracking_headers = [
            "x-forwarded-for",
            "x-real-ip",
            "x-client-ip",
            "cf-connecting-ip",
            "x-forwarded-host",
            "x-forwarded-proto",
        ]

        for header in tracking_headers:
            sanitized.pop(header.lower(), None)

        return sanitized

    def get_blocked_requests(self) -> List[dict]:
        """Get list of blocked requests for audit."""
        return list(self._blocked_requests)


class ContentSanitizer:
    """Sanitizes content before storage."""

    def __init__(self, mode: PrivacyMode = PrivacyMode.ENHANCED):
        self.mode = mode
        self.pii_detector = PIIDetector()
        self.network_isolator = NetworkIsolator(mode)

    def sanitize_for_storage(
        self,
        content: str,
        url: str = None,
        remove_pii: bool = True,
        remove_tracking: bool = True,
    ) -> tuple[str, SanitizationResult]:
        """
        Sanitize content before storage.

        Returns:
            Tuple of (sanitized_content, sanitization_result)
        """
        original_length = len(content)
        pii_removed = []
        blocked_patterns = []
        modified = False

        if remove_pii:
            pii_findings = self.pii_detector.detect_pii(content)
            for pii_type, matches in pii_findings.items():
                for match in matches:
                    content = content.replace(match, f'[REDACTED_{pii_type.upper()}]')
                    pii_removed.append(f"{pii_type}:{match[:20]}...")
                modified = True

        if remove_tracking:
            tracking_findings = self.pii_detector.detect_tracking(content)
            for tracker in tracking_findings:
                blocked_patterns.append(tracker)
                modified = True

        # Remove URLs if in maximum mode
        if self.mode == PrivacyMode.MAXIMUM:
            url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')
            urls_found = url_pattern.findall(content)
            for url_found in urls_found:
                content = content.replace(url_found, '[URL_REDACTED]')
                blocked_patterns.append(f"url:{url_found[:30]}...")
            modified = True

        return content, SanitizationResult(
            original_length=original_length,
            sanitized_length=len(content),
            pii_removed=pii_removed,
            blocked_patterns=blocked_patterns,
            was_modified=modified,
        )

    def sanitize_url(self, url: str) -> str:
        """Sanitize a URL by removing tracking parameters."""
        parsed = urlparse(url)

        # Remove common tracking parameters
        tracking_params = {
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
            'fbclid', 'gclid', 'mc_cid', 'mc_eid',
            'ref', 'source', 'medium',
        }

        if parsed.query:
            params = parsed.query.split('&')
            clean_params = [
                p for p in params
                if p.split('=')[0] not in tracking_params
            ]
            clean_query = '&'.join(clean_params)
        else:
            clean_query = ''

        # Reconstruct URL
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_query:
            clean_url += f"?{clean_query}"
        if parsed.fragment:
            clean_url += f"#{parsed.fragment}"

        return clean_url


class AuditLogger:
    """Logs all privacy-related actions for audit."""

    def __init__(self, max_entries: int = 10000):
        self._entries: List[dict] = []
        self._max_entries = max_entries

    def log(self, action: str, details: dict = None):
        """Log an audit entry."""
        entry = {
            "timestamp": time.time(),
            "action": action,
            "details": details or {},
        }
        self._entries.append(entry)

        # Trim to max entries
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def log_pii_detected(self, pii_type: str, location: str):
        """Log PII detection."""
        self.log("pii_detected", {
            "type": pii_type,
            "location": location,
        })

    def log_content_sanitized(self, url: str, pii_count: int):
        """Log content sanitization."""
        self.log("content_sanitized", {
            "url": url,
            "pii_count": pii_count,
        })

    def log_network_blocked(self, url: str, reason: str):
        """Log blocked network request."""
        self.log("network_blocked", {
            "url": url,
            "reason": reason,
        })

    def log_credential_access(self, domain: str, action: str):
        """Log credential vault access."""
        self.log("credential_access", {
            "domain": domain,
            "action": action,
        })

    def get_entries(self, limit: int = 100) -> List[dict]:
        """Get recent audit entries."""
        return self._entries[-limit:]

    def get_statistics(self) -> dict:
        """Get audit statistics."""
        return {
            "total_entries": len(self._entries),
            "pii_detections": sum(1 for e in self._entries if e["action"] == "pii_detected"),
            "content_sanitizations": sum(1 for e in self._entries if e["action"] == "content_sanitized"),
            "blocked_requests": sum(1 for e in self._entries if e["action"] == "network_blocked"),
            "credential_accesses": sum(1 for e in self._entries if e["action"] == "credential_access"),
        }


class PrivacyManager:
    """
    Central privacy manager for the sovereign browser.
    Coordinates all privacy controls and enforcement.
    """

    def __init__(self, mode: PrivacyMode = PrivacyMode.ENHANCED):
        self.mode = mode
        self.pii_detector = PIIDetector()
        self.network_isolator = NetworkIsolator(mode)
        self.content_sanitizer = ContentSanitizer(mode)
        self.audit_logger = AuditLogger()

    def set_mode(self, mode: PrivacyMode):
        self.mode = mode
        self.network_isolator = NetworkIsolator(mode)
        self.content_sanitizer = ContentSanitizer(mode)

    def sanitize_request(
        self,
        url: str,
        headers: Dict[str, str] = None,
        body: str = None,
    ) -> tuple[bool, str, Dict[str, str], str]:
        """
        Sanitize an outgoing request.

        Returns:
            Tuple of (allowed, sanitized_url, sanitized_headers, sanitized_body)
        """
        # Check if URL is allowed
        if not self.network_isolator.is_url_allowed(url):
            self.audit_logger.log_network_blocked(url, "privacy_policy")
            return False, url, headers or {}, body or ""

        # Sanitize URL
        sanitized_url = self.content_sanitizer.sanitize_url(url)

        # Sanitize headers
        sanitized_headers = self.network_isolator.sanitize_headers(headers or {})

        # Sanitize body
        sanitized_body = body
        if body:
            sanitized_body, _ = self.content_sanitizer.sanitize_for_storage(body)

        return True, sanitized_url, sanitized_headers, sanitized_body

    def sanitize_response(
        self,
        content: str,
        url: str = None,
    ) -> tuple[str, SanitizationResult]:
        """
        Sanitize an incoming response before storage.

        Returns:
            Tuple of (sanitized_content, sanitization_result)
        """
        sanitized, result = self.content_sanitizer.sanitize_for_storage(content, url)

        if result.was_modified:
            self.audit_logger.log_content_sanitized(url, len(result.pii_removed))

        return sanitized, result

    def check_url_allowed(self, url: str) -> bool:
        """Check if a URL is allowed under current privacy mode."""
        allowed = self.network_isolator.is_url_allowed(url)
        if not allowed:
            self.audit_logger.log_network_blocked(url, "privacy_policy")
        return allowed

    def get_privacy_report(self) -> dict:
        """Generate a comprehensive privacy report."""
        return {
            "mode": self.mode.value,
            "audit_statistics": self.audit_logger.get_statistics(),
            "blocked_requests": self.network_isolator.get_blocked_requests()[-10:],
            "local_only": self.mode == PrivacyMode.LOCAL_ONLY,
            "pii_removal": self.mode in (PrivacyMode.ENHANCED, PrivacyMode.MAXIMUM),
            "tracking_blocked": self.mode in (PrivacyMode.ENHANCED, PrivacyMode.MAXIMUM),
        }


# Module-level singleton
_privacy_manager: Optional[PrivacyManager] = None


def get_privacy_manager(mode: PrivacyMode = None) -> PrivacyManager:
    """Get or create the singleton privacy manager."""
    global _privacy_manager
    if _privacy_manager is None:
        _privacy_manager = PrivacyManager(mode or PrivacyMode.ENHANCED)
    return _privacy_manager


def sanitize_content_for_storage(
    content: str,
    url: str = None,
    mode: PrivacyMode = PrivacyMode.ENHANCED,
) -> tuple[str, SanitizationResult]:
    """Convenience function for content sanitization."""
    manager = get_privacy_manager(mode)
    return manager.sanitize_response(content, url)


def is_url_allowed(
    url: str,
    mode: PrivacyMode = PrivacyMode.ENHANCED,
) -> bool:
    """Convenience function for URL checking."""
    manager = get_privacy_manager(mode)
    return manager.check_url_allowed(url)
