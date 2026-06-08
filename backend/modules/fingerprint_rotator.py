"""
Fingerprint Rotator
====================
Per-session browser fingerprint randomization for forensic safety.
Generates unique browser profiles with randomized:
- User-Agent strings
- Viewport/Window dimensions
- Canvas fingerprint
- WebGL vendor/renderer
- Audio context fingerprint
- Timezone and locale

Each session gets a unique, consistent fingerprint that resists
cross-session tracking.
"""

import random
import hashlib
import time
import platform
from typing import Optional, List, Dict
from dataclasses import dataclass, field


# ---- Realistic User-Agent Strings ----

MACOS_CHROME_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

WINDOWS_CHROME_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

MACOS_SAFARI_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

LINUX_FIREFOX_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

ALL_USER_AGENTS = (
    MACOS_CHROME_AGENTS + WINDOWS_CHROME_AGENTS +
    MACOS_SAFARI_AGENTS + LINUX_FIREFOX_AGENTS
)

# ---- Canvas Fingerprint Data ----

CANVAS_NOISE_PATTERNS = [
    "Cwm fjordbank glyphs vext quiz",
    "Amazingly few discotheques provide jukeboxes",
    "Sphinx of black quartz, judge my vow",
    "Pack my box with five dozen liquor jugs",
]

WEBGL_VENDORS = [
    ("Google Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"),
    ("Google Inc.", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
    ("Mozilla", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"),
]

# ---- Screen Resolutions ----

RESOLUTIONS = [
    (1440, 900),
    (1680, 1050),
    (1920, 1080),
    (2560, 1440),
    (1280, 800),
    (1512, 982),  # MacBook Pro 14"
    (1728, 1117),  # MacBook Pro 16"
]

# ---- Timezones ----

TIMEZONES = [
    "America/Los_Angeles",
    "America/New_York",
    "America/Chicago",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Australia/Sydney",
]

LOCALES = ["en-US", "en-GB", "en-CA", "en-AU", "de-DE", "fr-FR", "ja-JP"]

# ---- Audio Fingerprint ----

AUDIO_BUFFER_VALUES = [
    124.0434, 124.0435, 124.0433, 124.0436,
    35.7383, 35.7384, 35.7382,
    79.5342, 79.5341, 79.5343,
]


@dataclass
class BrowserFingerprint:
    """A complete browser fingerprint profile."""
    profile_id: str
    user_agent: str
    viewport_width: int
    viewport_height: int
    platform: str
    oscpu: str
    language: str
    timezone: str
    canvas_noise: str
    webgl_vendor: str
    webgl_renderer: str
    audio_value: float
    hardware_concurrency: int
    device_memory: int
    color_depth: int = 24
    pixel_ratio: float = 2.0
    do_not_track: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'profile_id': self.profile_id,
            'user_agent': self.user_agent,
            'viewport': {'width': self.viewport_width, 'height': self.viewport_height},
            'platform': self.platform,
            'oscpu': self.oscpu,
            'language': self.language,
            'timezone': self.timezone,
            'hardware_concurrency': self.hardware_concurrency,
            'device_memory': self.device_memory,
            'color_depth': self.color_depth,
            'pixel_ratio': self.pixel_ratio,
            'webgl': {
                'vendor': self.webgl_vendor,
                'renderer': self.webgl_renderer,
            },
            'created_at': self.created_at,
        }

    def to_js_config(self) -> str:
        """Generate JavaScript configuration for Playwright."""
        return f"""
// Fingerprint Profile: {self.profile_id}
Object.defineProperty(navigator, 'userAgent', {{ get: () => '{self.user_agent}' }});
Object.defineProperty(navigator, 'platform', {{ get: () => '{self.platform}' }});
Object.defineProperty(navigator, 'language', {{ get: () => '{self.language}' }});
Object.defineProperty(navigator, 'languages', {{ get: () => ['{self.language}'] }});
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.hardware_concurrency} }});
Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {self.device_memory} }});
"""

    def to_playwright_config(self) -> dict:
        """Generate Playwright context configuration from this fingerprint."""
        return {
            'user_agent': self.user_agent,
            'viewport': {
                'width': self.viewport_width,
                'height': self.viewport_height,
            },
            'locale': self.language,
            'timezone_id': self.timezone,
            'device_scale_factor': self.pixel_ratio,
            'color_scheme': 'dark',
            'extra_http_headers': {
                'Accept-Language': f"{self.language},en;q=0.9",
                'DNT': '1' if self.do_not_track else '0',
            },
        }


class FingerprintRotator:
    """
    Generates and manages unique browser fingerprints.
    Each call to generate_profile() produces a unique, consistent fingerprint
    that a browser session can adopt.
    """

    def __init__(self):
        self._profiles: Dict[str, BrowserFingerprint] = {}
        self._rng = random.Random()

    def generate_profile(self, os_family: str = None) -> BrowserFingerprint:
        """
        Generate a new unique browser fingerprint profile.

        Args:
            os_family: 'macos', 'windows', 'linux', or None for random

        Returns:
            A BrowserFingerprint with randomized attributes
        """
        profile_id = hashlib.md5(
            f"{time.time()}_{random.random()}".encode()
        ).hexdigest()[:16]

        # Select User-Agent based on OS
        if os_family == 'macos':
            agent_pool = MACOS_CHROME_AGENTS + MACOS_SAFARI_AGENTS
        elif os_family == 'windows':
            agent_pool = WINDOWS_CHROME_AGENTS
        elif os_family == 'linux':
            agent_pool = LINUX_FIREFOX_AGENTS
        else:
            agent_pool = ALL_USER_AGENTS

        user_agent = self._rng.choice(agent_pool)
        resolution = self._rng.choice(RESOLUTIONS)

        # Determine OS from user agent
        if 'Macintosh' in user_agent:
            platform_str = 'MacIntel'
            os_info = 'Intel Mac OS X 10_15_7'
        elif 'Windows' in user_agent:
            platform_str = 'Win32'
            os_info = 'Windows NT 10.0; Win64; x64'
        else:
            platform_str = 'Linux x86_64'
            os_info = 'Linux x86_64'

        vendor_renderer = self._rng.choice(WEBGL_VENDORS)
        canvas_text = self._rng.choice(CANVAS_NOISE_PATTERNS)

        fingerprint = BrowserFingerprint(
            profile_id=profile_id,
            user_agent=user_agent,
            viewport_width=resolution[0],
            viewport_height=resolution[1],
            platform=platform_str,
            oscpu=os_info,
            language=self._rng.choice(LOCALES),
            timezone=self._rng.choice(TIMEZONES),
            canvas_noise=canvas_text,
            webgl_vendor=vendor_renderer[0],
            webgl_renderer=vendor_renderer[1],
            audio_value=self._rng.choice(AUDIO_BUFFER_VALUES),
            hardware_concurrency=self._rng.choice([2, 4, 6, 8, 10, 12, 16]),
            device_memory=self._rng.choice([2, 4, 8, 16]),
            pixel_ratio=self._rng.choice([1.0, 1.25, 1.5, 2.0]),
        )

        self._profiles[profile_id] = fingerprint
        return fingerprint

    def get_profile(self, profile_id: str) -> Optional[BrowserFingerprint]:
        """Get a previously generated profile by ID."""
        return self._profiles.get(profile_id)

    def generate_fresh_profile(self) -> BrowserFingerprint:
        """Generate a clean profile with no link to previous ones."""
        self._rng = random.Random()
        return self.generate_profile()

    def list_profiles(self) -> List[dict]:
        """List all generated profiles."""
        return [p.to_dict() for p in self._profiles.values()]

    def get_profile_for_playwright(self, profile_id: str) -> Optional[Dict]:
        """
        Get profile settings compatible with Playwright browser context.

        Returns a dict that can be passed to browser.new_context().
        """
        profile = self.get_profile(profile_id)
        if not profile:
            return None

        return {
            'user_agent': profile.user_agent,
            'viewport': {
                'width': profile.viewport_width,
                'height': profile.viewport_height,
            },
            'locale': profile.language,
            'timezone_id': profile.timezone,
            'color_scheme': random.choice(['light', 'dark', 'no-preference']),
            'device_scale_factor': profile.pixel_ratio,
            'extra_http_headers': {
                'Accept-Language': f"{profile.language},en;q=0.9",
                'DNT': '1' if profile.do_not_track else '0',
            },
        }

    def rotate_profile(self, current_profile_id: str = None) -> BrowserFingerprint:
        """
        Generate a new profile deliberately different from the current one.
        Used for session rotation.
        """
        current = self.get_profile(current_profile_id) if current_profile_id else None

        # Generate with different OS if possible
        if current:
            current_os = 'macos' if 'Macintosh' in current.user_agent else (
                'windows' if 'Windows' in current.user_agent else 'linux'
            )
            new_os = self._rng.choice([o for o in ['macos', 'windows', 'linux'] if o != current_os])
            return self.generate_profile(os_family=new_os)

        return self.generate_profile()

    def get_proxy_routing_config(self, profile_id: str) -> Dict:
        """
        Get proxy/routing configuration for geo-location spoofing
        matching the profile's timezone.
        """
        profile = self.get_profile(profile_id)
        if not profile:
            return {}

        # Map timezones to approximate proxy regions
        tz_to_region = {
            'America/Los_Angeles': 'us-west',
            'America/New_York': 'us-east',
            'Europe/London': 'uk',
            'Europe/Berlin': 'de',
            'Asia/Tokyo': 'jp',
            'Asia/Singapore': 'sg',
            'Australia/Sydney': 'au',
        }

        return {
            'region': tz_to_region.get(profile.timezone, 'us'),
            'timezone': profile.timezone,
            'locale': profile.language,
        }


# Module-level singleton
_rotator: Optional[FingerprintRotator] = None


def get_rotator() -> FingerprintRotator:
    global _rotator
    if _rotator is None:
        _rotator = FingerprintRotator()
    return _rotator
