"""
Real-time Risk Shield
======================
Multi-source URL risk assessment with consensus scoring.
Checks URLs against URLhaus, PhishTank, and heuristic analysis
before the browser navigates to them.
"""

import asyncio
import hashlib
import json
import time
import re
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from collections import OrderedDict
from urllib.parse import urlparse

import httpx

from backend.modules.notifications import get_notifier, Urgency


class RiskLevel:
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    source: str
    risk_level: str
    score: float
    details: str = ""
    response_time: float = 0


class LRUCache:
    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        self._cache = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Optional[dict]:
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry['timestamp'] < self._ttl:
                self._cache.move_to_end(key)
                return entry['value']
            del self._cache[key]
        return None

    def set(self, key: str, value: dict):
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = {'value': value, 'timestamp': time.time()}


class RiskShield:
    """
    Multi-source URL risk assessment engine.
    Aggregates results from threat intelligence sources
    and computes a consensus risk score.
    """

    SUSPICIOUS_TLDS = {'.tk', '.ml', '.ga', '.cf', '.gq', '.xyz', '.top', '.club', '.work'}
    PHISHING_PATTERNS = [
        re.compile(r'(paypal|apple|google|microsoft|amazon|facebook|instagram|netflix|bank).*\.(tk|ml|ga|cf|gq|xyz)', re.I),
        re.compile(r'(login|signin|account|secure|verify|update|confirm).*\.(tk|ml|ga|cf|gq|xyz)', re.I),
        re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),
    ]

    def __init__(self):
        self._cache = LRUCache(max_size=2000, ttl=1800)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._notifier = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    def _get_notifier(self):
        if self._notifier is None:
            self._notifier = get_notifier()
        return self._notifier

    async def _check_urlhaus(self, url: str) -> Optional[CheckResult]:
        start = time.time()
        try:
            client = await self._get_client()
            resp = await client.get("https://urlhaus-api.abuse.ch/v1/url/", params={"url": url})
            data = resp.json()
            if data.get('query_status') == 'ok' and data.get('url_status') == 'online':
                threats = data.get('threat', '')
                score = 0.9 if threats else 0.3
                return CheckResult(source='urlhaus',
                    risk_level=RiskLevel.HIGH if score > 0.7 else RiskLevel.LOW,
                    score=score, details=threats or 'No known threats',
                    response_time=time.time() - start)
            return CheckResult(source='urlhaus', risk_level=RiskLevel.SAFE, score=0.1,
                details='Not in database', response_time=time.time() - start)
        except Exception:
            return None

    def _check_heuristic(self, url: str) -> CheckResult:
        start = time.time()
        parsed = urlparse(url)
        domain = parsed.hostname or ''
        score = 0.0
        flags = []

        for tld in self.SUSPICIOUS_TLDS:
            if domain.endswith(tld):
                score += 0.3
                flags.append(f'Suspicious TLD ({tld})')
                break

        for pattern in self.PHISHING_PATTERNS:
            if pattern.search(url):
                score += 0.4
                flags.append('Phishing pattern detected')
                break

        if len(url) > 200:
            score += 0.1
            flags.append('Unusually long URL')
        if domain.count('.') > 3:
            score += 0.1
            flags.append(f'Excessive subdomains')
        if '@' in url:
            score += 0.3
            flags.append('@ symbol in URL')
        if url.lower().startswith('data:'):
            score += 0.8
            flags.append('Data URI scheme')

        if score >= 0.7: risk = RiskLevel.HIGH
        elif score >= 0.4: risk = RiskLevel.MEDIUM
        elif score >= 0.2: risk = RiskLevel.LOW
        else: risk = RiskLevel.SAFE

        return CheckResult(source='heuristic', risk_level=risk, score=min(score, 1.0),
            details='; '.join(flags) if flags else 'No suspicious patterns',
            response_time=time.time() - start)

    async def _check_phishtank(self, url: str) -> Optional[CheckResult]:
        start = time.time()
        try:
            client = await self._get_client()
            resp = await client.post("https://checkurl.phishtank.com/checkurl/",
                data={"url": url, "format": "json"})
            data = resp.json()
            if data.get('results', {}).get('in_database') == '1':
                is_verified = data['results'].get('verified') == '1'
                score = 1.0 if is_verified else 0.7
                return CheckResult(source='phishtank',
                    risk_level=RiskLevel.CRITICAL if is_verified else RiskLevel.HIGH,
                    score=score, details=data['results'].get('phish_detail_url', ''),
                    response_time=time.time() - start)
            return CheckResult(source='phishtank', risk_level=RiskLevel.SAFE, score=0.0,
                details='Not in database', response_time=time.time() - start)
        except Exception:
            return None

    async def assess_url(self, url: str, real_time: bool = True) -> dict:
        cached = self._cache.get(url)
        if cached:
            return cached

        heuristic = self._check_heuristic(url)

        if real_time:
            urlhaus, phishtank = await asyncio.gather(
                self._check_urlhaus(url), self._check_phishtank(url), return_exceptions=True)
        else:
            urlhaus, phishtank = None, None

        checks = [heuristic]
        if urlhaus and not isinstance(urlhaus, Exception):
            checks.append(urlhaus)
        if phishtank and not isinstance(phishtank, Exception):
            checks.append(phishtank)

        scores = [c.score for c in checks]
        consensus_score = sum(scores) / len(scores) if scores else 0.5
        high_risk_count = sum(1 for c in checks if c.score >= 0.7)
        blocked = high_risk_count >= 2

        if consensus_score >= 0.8:
            risk_level, blocked = RiskLevel.CRITICAL, True
        elif consensus_score >= 0.6:
            risk_level = RiskLevel.HIGH
        elif consensus_score >= 0.3:
            risk_level = RiskLevel.MEDIUM
        elif consensus_score >= 0.1:
            risk_level = RiskLevel.LOW
        else:
            risk_level = RiskLevel.SAFE

        result = {
            'url': url, 'risk_level': risk_level,
            'consensus_score': round(consensus_score, 2),
            'checks': [{'source': c.source, 'risk_level': c.risk_level,
                        'score': c.score, 'details': c.details} for c in checks],
            'blocked': blocked, 'high_risk_count': high_risk_count,
            'total_checks': len(checks),
            'reason': self._generate_reason(checks, risk_level, blocked),
            'timestamp': time.time(),
        }

        self._cache.set(url, result)

        if blocked or risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            try:
                notifier = self._get_notifier()
                await notifier.send_security_alert(url=url, risk_type=risk_level.upper(),
                    details=result['reason'])
            except Exception:
                pass

        return result

    def _generate_reason(self, checks, risk_level, blocked):
        if not checks:
            return "No checks performed."
        flagged = [c for c in checks if c.score >= 0.4]
        if not flagged:
            return "All sources indicate this URL is safe."
        parts = [f"{c.source}: {c.details}" for c in flagged]
        prefix = "BLOCKED - Multiple sources flagged this URL. " if blocked else ""
        return prefix + "; ".join(parts)

    async def quick_check(self, url: str) -> dict:
        heuristic = self._check_heuristic(url)
        blocked = heuristic.score >= 0.7
        if heuristic.score >= 0.7: risk = RiskLevel.HIGH
        elif heuristic.score >= 0.4: risk = RiskLevel.MEDIUM
        elif heuristic.score >= 0.2: risk = RiskLevel.LOW
        else: risk = RiskLevel.SAFE
        return {'url': url, 'risk_level': risk, 'score': heuristic.score,
                'blocked': blocked, 'reason': heuristic.details,
                'checks': [{'source': heuristic.source, 'risk_level': heuristic.risk_level,
                           'score': heuristic.score, 'details': heuristic.details}]}

    async def batch_assess(self, urls: List[str]) -> List[dict]:
        return await asyncio.gather(*[self.assess_url(url) for url in urls], return_exceptions=True)

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def get_cache_stats(self) -> dict:
        return {
            'size': len(self._cache._cache),
            'max_size': self._cache._max_size,
            'ttl': self._cache._ttl,
        }


_shield: Optional[RiskShield] = None


def get_shield() -> RiskShield:
    global _shield
    if _shield is None:
        _shield = RiskShield()
    return _shield


async def assess_url_risk(url: str, real_time: bool = True) -> dict:
    return await get_shield().assess_url(url, real_time=real_time)


async def quick_url_check(url: str) -> dict:
    return await get_shield().quick_check(url)
