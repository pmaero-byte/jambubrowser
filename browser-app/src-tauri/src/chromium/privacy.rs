//! Privacy Engine — ad/tracker blocking and fingerprint protection.
//!
//! Uses CDP to block ad/tracker requests (Network.setBlockedURLs) and
//! injects anti-fingerprinting scripts (Page.addScriptToEvaluateOnNewDocument)
//! when each tab is created.

/// Curated blocklist of common ad/tracker domains and URL patterns.
/// Derived from EasyList — the top ~200 highest-traffic trackers/ads.
const AD_TRACKER_PATTERNS: &[&str] = &[
    // ── Google ads & analytics ──
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googletagmanager.com", "googletagservices.com", "google-analytics.com",
    "googletraveladservices.com", "adservice.google.com",
    "pagead2.googlesyndication.com", "googleads.g.doubleclick.net",
    // ── Facebook / Meta ──
    "facebook.com/tr", "connect.facebook.net/en_US/fbevents.js",
    // ── Amazon ads ──
    "amazon-adsystem.com", "aax-us-east.amazon-adsystem.com",
    "c.amazon-adsystem.com", "fls-na.amazon.com",
    // ── Microsoft / Bing ──
    "bing.com/bat", "bat.bing.com", "scorecardresearch.com",
    // ── Major ad networks ──
    "adnxs.com", "adsrvr.org", "adtech.de", "advertising.com",
    "criteo.com", "criteo.net", "casalemedia.com", "openx.net",
    "pubmatic.com", "rubiconproject.com", "moatads.com", "outbrain.com",
    "taboola.com", "zemanta.com", "sharethrough.com", "sovrn.com",
    "indexww.com", "33across.com", "adform.net", "adition.com",
    "smartadserver.com", "yieldmo.com", "yieldlab.net",
    "bluekai.com", "exelator.com", "demdex.net", "krxd.net",
    "rlcdn.com", "tribalfusion.com", "turn.com", "w55c.net",
    "dataxu.com", "eyeota.net", "adobe.com/udm", "agkn.com",
    // ── Analytics ──
    "hotjar.com", "mouseflow.com", "fullstory.com", "heap-api.com",
    "amplitude.com", "mixpanel.com", "segment.io", "segment.com",
    "optimizely.com", "clarity.ms", "mouseflow.com",
    // ── Social widgets ──
    "platform.twitter.com/widgets.js", "platform.instagram.com",
    "assets.pinterest.com", "pinterest.com/pinit",
    "linkedin.com/px", "snap.licdn.com",
    "redditstatic.com/ads", "reddit.com/api/info",
    // ── CDN trackers ──
    "cdn.mxpnl.com", "cdn.optimizely.com", "cdn.segment.com",
    "cdn.amplitude.com", "cdn.branch.io", "cdn.bizible.com",
    // ── Misc trackers ──
    "quantserve.com", "chartbeat.com", "parsely.com", "newrelic.com",
    "datadoghq.com", "sentry.io", "logrocket.com", "rollbar.com",
    "bugsnag.com", "pendo.io", "intercom.io", "zendesk.com/embeddable",
    "drift.com", "livechatinc.com", "tawk.to", "crisp.chat",
    "usefathom.com", "plausible.io", "simpleanalytics.com",
    // ── Malware / phishing domains ──
    "coin-hive.com", "coinhive.com", "crypto-loot.com",
    "jsecoin.com", "webmine.cz", "minero.cc", "cryptonight.com",
];

/// Fingerprint protection script injected into every new page.
/// Spoofs canvas, WebGL, AudioContext, and navigator properties to
/// prevent browser fingerprinting.
pub const FINGERPRINT_PROTECTION_SCRIPT: &str = r#"
(function() {
  'use strict';
  // ── Canvas fingerprint spoofing ──
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function() {
    const ctx = this.getContext('2d');
    if (ctx) {
      const imageData = ctx.getImageData(0, 0, this.width, this.height);
      for (let i = 0; i < imageData.data.length; i += 4) {
        imageData.data[i] ^= imageData.data[i+1] & 0x01;
      }
      ctx.putImageData(imageData, 0, 0);
    }
    return origToDataURL.apply(this, arguments);
  };
  const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function() {
    const data = origGetImageData.apply(this, arguments);
    for (let i = 0; i < data.data.length; i += 4) {
      data.data[i] ^= data.data[i+1] & 0x01;
    }
    return data;
  };
  // ── WebGL fingerprint spoofing ──
  try {
    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    const spoofed = {
      [37445]: 'Apple Inc.',
      [37446]: 'Apple M4 Pro',
    };
    WebGLRenderingContext.prototype.getParameter = function(p) {
      return spoofed[p] || origGetParam.call(this, p);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
      WebGL2RenderingContext.prototype.getParameter = WebGLRenderingContext.prototype.getParameter;
    }
  } catch(e) {}
  // ── AudioContext fingerprint spoofing ──
  try {
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function() {
      const data = origGetChannelData.apply(this, arguments);
      for (let i = 0; i < data.length; i++) {
        data[i] += (Math.random() * 2 - 1) * 1e-7;
      }
      return data;
    };
  } catch(e) {}
  // ── Navigator spoofing ──
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 12 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
  } catch(e) {}
})();
"#;

/// Returns the list of URL patterns to block for ad/tracker protection.
pub fn get_blocked_urls() -> Vec<String> {
    AD_TRACKER_PATTERNS.iter().map(|p| format!("*{p}*")).collect()
}
