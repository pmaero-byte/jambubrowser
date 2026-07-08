"""
Web proxy endpoint — fetches remote pages server-side and returns them
without X-Frame-Options / CSP frame-ancestors so the frontend iframe can
display any site.

Resource URLs are rewritten to path-based proxy URLs (``/proxy/ORIGINAL_URL``)
so that JavaScript module ``import()`` and CSS ``url()`` resolve correctly:
relative paths stay within the proxy path tree rather than collapsing to the
server root (which would happen with query-param-based URLs).
"""

import logging
import re
import urllib.parse
from urllib.parse import urlparse, urljoin

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from backend.core.security import is_safe_url
from backend.core.response_cache import ResponseCache, CachedResponse

log = logging.getLogger("jambu.proxy")
router = APIRouter(tags=["proxy"])

# Path prefix for proxy URLs.  All rewritten asset paths start with this,
# keeping relative module imports inside the path tree.
_PROXY_PREFIX = "/proxy/"

# Content types we pass through as raw bytes.
_PASSTHROUGH_PREFIXES = (
    "image/", "video/", "audio/", "font/",
    "application/pdf", "application/octet-stream",
)

# ── Proxy response cache ────────────────────────────────────────────────────

_proxy_cache: "ResponseCache | None" = None


def get_proxy_cache() -> ResponseCache:
    """Return the singleton proxy response cache (TTL 60 s, 50 MB budget)."""
    global _proxy_cache
    if _proxy_cache is None:
        _proxy_cache = ResponseCache(max_size_bytes=50 * 1024 * 1024, default_ttl=60)
    return _proxy_cache


@router.get("/proxy/{url:path}")
async def web_proxy(url: str, request: Request):
    """
    Fetch the upstream *url* and return its content stripped of
    X-Frame-Options and CSP ``frame-ancestors``.

    The *url* is taken from the path after ``/proxy/`` so that the path tree
    is preserved for relative module / CSS URL resolution.
    """
    if not url or not url.strip():
        raise HTTPException(400, "Missing URL in path (use /proxy/{url})")

    # Reconstruct the full upstream URL: path from route + any leftover query
    # params on the proxy request that aren't proxy-internal ones.
    upstream_url = url
    # Forward query params that are NOT consumed by the proxy itself.
    own_params = {"_cb"}  # cache-bust — consumed here, not forwarded
    extra_params = {k: v for k, v in request.query_params.items()
                    if k not in own_params}
    if extra_params:
        qs = urllib.parse.urlencode(extra_params, doseq=True)
        upstream_url = upstream_url + "?" + qs

    if not is_safe_url(upstream_url):
        raise HTTPException(400, "Blocked or invalid URL")

    # ── Response cache (TTL 60 s, LRU, 50 MB budget) ──
    cache = get_proxy_cache()
    cached = cache.get(upstream_url)

    if cached is not None:
        resp_body = cached.body
        resp_content_type = cached.content_type
        resp_status = cached.status_code
        resp_headers = cached.headers
        log.debug("proxy cache hit for %s (%d bytes)", upstream_url, len(resp_body))
    else:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
            ) as client:
                upstream = await client.get(upstream_url)

            resp_body = upstream.content
            resp_content_type = upstream.headers.get("content-type", "").lower()
            resp_status = upstream.status_code
            # Snapshot headers as a plain dict so they're serialisable.
            resp_headers = dict(upstream.headers)

            cache.set(upstream_url, CachedResponse(
                body=resp_body,
                content_type=resp_content_type,
                status_code=resp_status,
                headers=resp_headers,
            ))
            log.debug("proxy cache miss + store for %s (%d bytes)", upstream_url, len(resp_body))

        except httpx.TimeoutException:
            raise HTTPException(504, f"Upstream timeout fetching {upstream_url}")
        except httpx.ConnectError:
            raise HTTPException(502, f"Could not connect to {upstream_url}")
        except Exception as exc:
            log.error("proxy error for %s: %s", upstream_url, exc)
            raise HTTPException(502, f"Proxy error: {exc}")

    # ── Build response headers (override iframe-blockers) ──
    BLOCKLIST = {"content-encoding", "content-length",
                  "x-frame-options", "content-security-policy"}
    headers = {}
    for key, value in resp_headers.items():
        lkey = key.lower()
        if lkey in BLOCKLIST:
            continue
        headers[key] = value

    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    headers["Pragma"] = "no-cache"
    headers["Expires"] = "0"

    # Explicitly set permissive values so SecurityHeadersMiddleware
    # (which only adds headers that don't already exist) doesn't inject
    # X-Frame-Options: DENY / CSP frame-ancestors 'none'.
    headers["X-Frame-Options"] = "SAMEORIGIN"
    headers["Content-Security-Policy"] = (
        "default-src * 'unsafe-inline' data: blob:; "
        "script-src * 'unsafe-inline' 'unsafe-eval'; "
        "style-src * 'unsafe-inline' blob:; "
        "img-src * data: blob:; "
        "connect-src *; "
        "font-src * data: blob:; "
        "frame-src *; "
        "frame-ancestors *"
    )

    if resp_content_type.startswith(_PASSTHROUGH_PREFIXES):
        return Response(
            content=resp_body,
            status_code=resp_status,
            headers=headers,
            media_type=resp_content_type,
        )

    body = resp_body.decode("utf-8", errors="replace")
    if "text/html" in resp_content_type:
        body = _rewrite_html(body, upstream_url)
    elif "text/css" in resp_content_type:
        body = _rewrite_css(body, upstream_url)

    return Response(
        content=body.encode("utf-8") if isinstance(body, str) else body,
        status_code=resp_status,
        headers=headers,
        media_type=resp_content_type,
    )


# ── URL rewriting helpers ────────────────────────────────────────────────


def _rewrite_html(html: str, original_url: str) -> str:
    """Prepare HTML for same-origin iframe embedding:
    1. Inject ``<base href="original_url">`` for CSS url() / JS fetch().
    2. Rewrite src / href / srcset to path-based proxy URLs.
    3. Inject script shim to fix SPA router path and modulepreload URLs.
    4. Inject devtools instrumentation script (performance, console, network).
    """
    html = _inject_base_tag(html, original_url)
    html = _rewrite_resource_urls(html, original_url)
    html = _inject_spa_shim(html, original_url)
    html = _inject_devtools_script(html, original_url)
    return html


def _proxy_url(url_str: str, base: str) -> str:
    """Wrap a URL in the path-based proxy prefix; resolve relatives first."""
    url_str = url_str.strip()
    if not url_str or url_str.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
        return url_str
    if _PROXY_PREFIX in url_str:
        return url_str
    if url_str.startswith("//"):
        url_str = "https:" + url_str
    if not url_str.startswith(("http://", "https://")):
        url_str = urljoin(base, url_str)
    return _PROXY_PREFIX + url_str


def _rewrite_resource_urls(html: str, original_url: str) -> str:
    """Rewrite src / href / srcset to path-based proxy URLs."""
    parsed = urlparse(original_url)
    base = original_url.rstrip("/") + "/" if not parsed.path.endswith("/") else original_url

    def _replace_src_href(m):
        before = m.group(1)
        attr = m.group(2)
        quote = m.group(3)
        val = m.group(4)
        return f'{before}{attr}={quote}{_proxy_url(val, base)}{quote}'

    html = re.sub(
        r'(^|[\s>])(src|href)=(["\'])(.*?)\3',
        _replace_src_href,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace_srcset(m):
        before = m.group(1)
        quote = m.group(2)
        value = m.group(3)
        tokens = []
        for token in value.split(","):
            token = token.strip()
            if token:
                parts = token.split(None, 1)
                if parts:
                    parts[0] = _proxy_url(parts[0], base)
                tokens.append(" ".join(parts))
        return f'{before}srcset={quote}{", ".join(tokens)}{quote}'

    html = re.sub(
        r'(^|[\s>])srcset=(["\'])(.*?)\2',
        _replace_srcset,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return html


def _inject_base_tag(html: str, original_url: str) -> str:
    """Insert ``<base href="original_url">`` right after <head>."""
    parsed = urlparse(original_url)
    base = original_url.rstrip("/") + "/" if not parsed.path.endswith("/") else original_url

    base_tag = f'<base href="{base}">'

    head_match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head_match:
        pos = head_match.end()
        return html[:pos] + base_tag + html[pos:]

    body_match = re.search(r"<body[^>]*>", html, re.IGNORECASE)
    if body_match:
        return html[:body_match.start()] + "<head>" + base_tag + "</head>" + html[body_match.start():]

    return "<head>" + base_tag + "</head>" + html


def _rewrite_css(css: str, css_url: str) -> str:
    """Rewrite url() / @import inside CSS to path-based proxy URLs."""
    css_dir = css_url.rsplit("/", 1)[0] + "/" if "/" in css_url else css_url + "/"

    def _proxy_css_url(url_str: str) -> str:
        url_str = url_str.strip().strip("\"'")
        if not url_str or url_str.startswith(("data:", "#")):
            return url_str
        if url_str.startswith(("http://", "https://")):
            return _proxy_url(url_str, css_dir)
        resolved = urljoin(css_dir, url_str)
        return _proxy_url(resolved, css_dir)

    css = re.sub(
        r'url\(\s*([^\s"\'()]+)\s*\)'
        r'|url\(\s*\'([^\']+)\'\s*\)'
        r'|url\(\s*"([^"]+)"\s*\)',
        lambda m: f'url({_proxy_css_url(m.group(1) or m.group(2) or m.group(3))})',
        css,
    )

    css = re.sub(
        r"""@import\s+['"]([^'"]+)['"]""",
        lambda m: f'@import "{_proxy_css_url(m.group(1))}"',
        css,
    )

    return css


# ── DevTools instrumentation ──────────────────────────────────────────────

_DEVTOOLS_SCRIPT = """
<script>
(function(){
  var TARGET = window.parent;
  if (!TARGET) return;
  var SRC = 'jambu-devtools';
  function send(type, data) {
    try { TARGET.postMessage({ source: SRC, type: type, data: data }, '*'); } catch(e) {}
  }

  /* ── Navigation Timing (on load) ── */
  function captureNavTiming() {
    try {
      var p = performance.getEntriesByType('navigation')[0];
      if (!p) return;
      send('perf:navigation', {
        url: location.href,
        domContentLoaded: p.domContentLoadedEventEnd,
        load: p.loadEventEnd,
        domInteractive: p.domInteractive,
        ttfb: p.responseStart - p.requestStart,
        redirectTime: p.redirectEnd - p.redirectStart,
        dnsTime: p.domainLookupEnd - p.domainLookupStart,
        tcpTime: p.connectEnd - p.connectStart,
        requestTime: p.responseEnd - p.requestStart,
        responseTime: p.responseEnd - p.responseStart,
        transferSize: p.transferSize,
        encodedBodySize: p.encodedBodySize,
        decodedBodySize: p.decodedBodySize,
        type: p.type,
      });
    } catch(e) {}
  }
  if (document.readyState === 'complete') { captureNavTiming(); }
  else { window.addEventListener('load', captureNavTiming); }

  /* ── PerformanceObserver ── */
  try {
    var ro = new PerformanceObserver(function(list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        if (e.entryType === 'largest-contentful-paint') {
          send('perf:lcp', { renderTime: e.renderTime, loadTime: e.loadTime, size: e.size, id: e.id, url: e.url });
        } else if (e.entryType === 'first-contentful-paint') {
          send('perf:fcp', { startTime: e.startTime });
        } else if (e.entryType === 'layout-shift') {
          send('perf:cls', { value: e.value, sources: (e.sources || []).length, hadRecentInput: e.hadRecentInput });
        } else if (e.entryType === 'longtask') {
          send('perf:longtask', { duration: e.duration, startTime: e.startTime, name: e.name });
        }
      }
    });
    ro.observe({ type: 'largest-contentful-paint', buffered: true });
    ro.observe({ type: 'first-contentful-paint', buffered: true });
    ro.observe({ type: 'layout-shift', buffered: true });
    ro.observe({ type: 'longtask', buffered: true });
  } catch(e) {}

  /* ── Resource Timing (network requests via PerformanceObserver) ── */
  try {
    var netObs = new PerformanceObserver(function(list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        send('perf:resource', {
          name: e.name,
          initiatorType: e.initiatorType,
          startTime: e.startTime,
          duration: e.duration,
          dnsStart: e.domainLookupStart,
          dnsEnd: e.domainLookupEnd,
          connectStart: e.connectStart,
          connectEnd: e.connectEnd,
          ttfb: e.responseStart - e.requestStart,
          responseStart: e.responseStart,
          responseEnd: e.responseEnd,
          transferSize: e.transferSize,
          encodedBodySize: e.encodedBodySize,
          decodedBodySize: e.decodedBodySize,
          nextHopProtocol: e.nextHopProtocol,
        });
      }
    });
    netObs.observe({ type: 'resource', buffered: true });
  } catch(e) {}

  try {
    var navObs = new PerformanceObserver(function(list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        send('perf:navigation', {
          url: location.href,
          domContentLoaded: e.domContentLoadedEventEnd,
          load: e.loadEventEnd,
          domInteractive: e.domInteractive,
          ttfb: e.responseStart - e.requestStart,
          dnsTime: e.domainLookupEnd - e.domainLookupStart,
          tcpTime: e.connectEnd - e.connectStart,
          transferSize: e.transferSize,
          decodedBodySize: e.decodedBodySize,
          type: e.type,
        });
      }
    });
    navObs.observe({ type: 'navigation', buffered: true });
  } catch(e) {}

  /* ── Console interception ── */
  (function() {
    var levels = ['log','info','warn','error','debug'];
    for (var i = 0; i < levels.length; i++) {
      (function(lvl) {
        var orig = console[lvl];
        console[lvl] = function() {
          var args = [];
          for (var j = 0; j < arguments.length; j++) {
            try { args.push(typeof arguments[j] === 'object' ? JSON.stringify(arguments[j], null, 2) : String(arguments[j])); }
            catch(e) { args.push(String(arguments[j])); }
          }
          send('console', { level: lvl, message: args.join(' '), timestamp: Date.now() });
          return orig.apply(console, arguments);
        };
      })(levels[i]);
    }
  })();

  /* ── JS Errors ── */
  window.addEventListener('error', function(e) {
    send('error', { message: e.message, filename: e.filename, lineno: e.lineno, colno: e.colno, source: 'onerror' });
  }, true);
  window.addEventListener('unhandledrejection', function(e) {
    send('error', { message: String(e.reason || 'Unknown'), source: 'unhandledrejection', timestamp: Date.now() });
  }, true);
})();
</script>"""


def _inject_devtools_script(html: str, original_url: str) -> str:
    """Inject the devtools instrumentation script before </body>."""
    body_close = re.search(r"</body>", html, re.IGNORECASE)
    if body_close:
        return html[:body_close.start()] + _DEVTOOLS_SCRIPT + html[body_close.start():]
    # Fallback: append before end of html
    html_close = re.search(r"</html>", html, re.IGNORECASE)
    if html_close:
        return html[:html_close.start()] + _DEVTOOLS_SCRIPT + html[html_close.start():]
    return html + _DEVTOOLS_SCRIPT


def _inject_spa_shim(html: str, original_url: str) -> str:
    """Inject a script that fixes SPA router path and modulepreload URLs.

    Two problems solved:

    1. **SPA router 404** — React Router / Vue Router reads
       ``window.location.pathname``, which is ``/proxy/https://original/``
       instead of ``/``.  We call ``history.replaceState`` *before* the SPA
       module loads so the SPA sees ``/`` as the initial route.

    2. **Root-relative modulepreload 404s** — Vite's ``__vitePreload`` helper
       creates ``<link rel="modulepreload" href="/assets/chunk.js">`` which
       resolves to the server origin (``localhost:8001``) instead of going
       through the proxy.  We intercept ``document.createElement('link')`` and
       prefix root-relative ``href`` values with the proxy base.

    3. **Dynamic import discovery** — SPA routing / lazy loading that does
       ``new URL('/assets/', location.origin)`` or hard-coded ``"/assets/"``
       in chunk-loaders gets its base patched at the Navigator API level.
    """
    parsed = urllib.parse.urlparse(original_url)
    base = original_url.rstrip("/") + "/" if not parsed.path.endswith("/") else original_url
    # Proxy path root for this origin (e.g. "/proxy/https://astrogenesis.net")
    proxy_root = _PROXY_PREFIX + original_url.rstrip("/")

    shim = f"""<script>
(function(){{
  var PR = "{proxy_root}";

  /*
   * 1. Fix SPA router — make it see "/" instead of the proxy-prefixed path,
   *    so React Router / Vue Router renders the home route, not a 404.
   *    history.replaceState only changes the URL bar — does NOT reload the
   *    page or affect the proxy content already loaded.
   */
  history.replaceState(history.state, '', '/');

  /*
   * 2. Fix root-relative link hrefs (modulepreload, stylesheet, etc.) that
   *    Vite's __vitePreload creates with href="/assets/...".  These resolve
   *    to the server origin (localhost:8001) instead of through the proxy.
   *
   *    We intercept .appendChild / .insertBefore on <head> and fix the href
   *    property right before the element enters the DOM.  This catches the
   *    actual href property set by Vite (link.href = value), which bypasses
   *    setAttribute interception.
   */
  function fixHref(el) {{
    try {{
      var u = new URL(el.href);
      if (u.origin === location.origin && !u.pathname.startsWith('/proxy/') && u.pathname.indexOf(PR) === -1) {{
        el.href = PR + u.pathname + u.search + u.hash;
      }}
    }} catch(e){{}}
  }}
  var _a = document.head.appendChild.bind(document.head);
  document.head.appendChild = function(el) {{
    if (el.tagName === 'LINK') fixHref(el);
    return _a(el);
  }};
  var _i = document.head.insertBefore.bind(document.head);
  document.head.insertBefore = function(el, ref) {{
    if (el.tagName === 'LINK') fixHref(el);
    return _i(el, ref);
  }};

  /*
   * 3. Property-descriptor patches for Image.src and Link.href.
   *    React sets img.src (= property assignment) which bypases
   *    both setAttribute and createElement interception.
   */
  var _imgSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  Object.defineProperty(HTMLImageElement.prototype, 'src', {{
    get: function() {{ return _imgSrc.get.call(this); }},
    set: function(val) {{
      if (typeof val === 'string' && val.charAt(0) === '/' && val.charAt(1) !== '/' && val.indexOf('/proxy/') === -1) {{
        val = PR + val;
      }}
      _imgSrc.set.call(this, val);
    }},
    configurable: true
  }});

  /*
   * 4. createElement shim — patch setAttribute on every JS-created
   *    element.  Catches setAttribute('src'/'href', '/root/rel') calls
   *    that the (captured) prototype patch may miss.
   */
  function fixURL(val) {{
    return (typeof val === 'string' && val.charAt(0) === '/' && val.charAt(1) !== '/' && val.indexOf('/proxy/') === -1) ? PR + val : val;
  }}
  var _ce = document.createElement.bind(document);
  document.createElement = function(tag, opts) {{
    var el = _ce(tag, opts);
    var _sa = el.setAttribute.bind(el);
    el.setAttribute = function(name, val) {{
      if (name === 'src' || name === 'href') val = fixURL(val);
      return _sa(name, val);
    }};
    return el;
  }};
}})();
</script>"""

    # Inject before </head> (right before the first <script module> or at the
    # end of <head>), so the shim runs before the SPA bundle.
    head_close = re.search(r"</head>", html, re.IGNORECASE)
    if head_close:
        return html[:head_close.start()] + shim + html[head_close.start():]

    # Fallback: inject before first script or </body>
    body_close = re.search(r"</body>", html, re.IGNORECASE)
    if body_close:
        return html[:body_close.start()] + shim + html[body_close.start():]

    return shim + html
