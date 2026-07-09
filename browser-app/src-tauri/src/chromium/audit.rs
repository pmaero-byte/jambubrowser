//! Page audit runner — DOM analysis, performance, accessibility, SEO checks.
//!
//! Runs via CDP JavaScript evaluation on the current page and returns
//! structured findings suitable for display in an audit overlay panel.

use serde::Serialize;

use super::cdp::{CdpClient, PerfMetrics};
use super::tab::Tab;

#[derive(Debug, Clone, Serialize)]
pub struct AuditFinding {
    pub category: String,   // "performance", "accessibility", "seo", "security"
    pub severity: String,   // "critical", "warning", "info"
    pub title: String,
    pub detail: String,
    pub score: Option<f64>, // 0..1 where 1 = best
}

#[derive(Debug, Clone, Serialize)]
pub struct AuditReport {
    pub url: String,
    pub title: String,
    pub findings: Vec<AuditFinding>,
    pub overall_score: f64,
    pub perf_metrics: PerfMetrics,
}

/// Audit script injected into the page to collect DOM/performance data.
const AUDIT_SCRIPT: &str = r#####"
(function() {
  const result = {};

  // ── DOM stats ──
  result.totalElements = document.querySelectorAll('*').length;
  result.images = document.querySelectorAll('img').length;
  result.imagesWithoutAlt = document.querySelectorAll('img:not([alt])').length;
  result.imagesWithoutDimensions = Array.from(document.querySelectorAll('img')).filter(function(i) {
    return !i.width && !i.height && !i.getAttribute('width') && !i.getAttribute('height');
  }).length;
  result.links = document.querySelectorAll('a').length;
  result.brokenLinks = 0; // cannot detect locally, set by CDP
  result.scripts = document.querySelectorAll('script').length;
  result.stylesheets = document.querySelectorAll('link[rel="stylesheet"]').length;

  // ── Headings ──
  var headings = document.querySelectorAll('h1,h2,h3,h4,h5,h6');
  result.headings = headings.length;
  result.h1Count = document.querySelectorAll('h1').length;
  var headingLevels = [];
  headings.forEach(function(h) { headingLevels.push(parseInt(h.tagName[1])); });
  result.headingStructure = headingLevels;
  result.hasSkippedHeadings = false;
  var prev = 0;
  headingLevels.forEach(function(l) {
    if (prev > 0 && l > prev + 1) result.hasSkippedHeadings = true;
    prev = Math.max(prev, l);
  });

  // ── Meta / SEO ──
  result.title = document.title;
  result.titleLength = document.title.length;
  result.metaDescription = (document.querySelector('meta[name="description"]') || {}).content || '';
  result.metaDescriptionLength = result.metaDescription.length;
  result.metaViewport = !!document.querySelector('meta[name="viewport"]');
  result.canonical = (document.querySelector('link[rel="canonical"]') || {}).href || '';
  result.hasOpenGraph = !!document.querySelector('meta[property="og:title"]');
  result.hasSchemaOrg = !!document.querySelector('[itemtype]');

  // ── Accessibility ──
  result.hasLang = !!document.documentElement.lang;
  result.formInputs = document.querySelectorAll('input,select,textarea').length;
  result.formInputsWithoutLabel = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]),select,textarea'))
    .filter(function(el) { return !el.id || !document.querySelector('label[for="' + el.id + '"]'); }).length;
  result.hasSkipLink = !!document.querySelector('a[href^="#"]');
  result.tabindexPositive = Array.from(document.querySelectorAll('[tabindex]'))
    .filter(function(el) { return parseInt(el.getAttribute('tabindex')) > 0; }).length;

  // ── Security ──
  result.hasHttps = window.location.protocol === 'https:';
  result.externalLinks = Array.from(document.querySelectorAll('a[href^="http"]'))
    .filter(function(a) { return !a.href.startsWith(window.location.origin); }).length;
  result.externalLinksNoRel = Array.from(document.querySelectorAll('a[href^="http"]'))
    .filter(function(a) { return !a.href.startsWith(window.location.origin) && !a.rel.includes('noopener'); }).length;
  result.inlineStyles = document.querySelectorAll('style').length;
  result.inlineEventHandlers = Array.from(document.querySelectorAll('*'))
    .filter(function(el) { return el.outerHTML.match(/ on\w+=/); }).length;

  return JSON.stringify(result);
})();
"#####;

pub async fn run_audit(cdp: &CdpClient, tab: &Tab) -> Result<AuditReport, String> {
    // 1. Run DOM audit script
    let raw = cdp.evaluate(tab, AUDIT_SCRIPT).await?;
    let parsed: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("Audit parse error: {e}"))?;

    let mut findings = Vec::new();

    // ── SEO checks ──
    let title = parsed["title"].as_str().unwrap_or("").to_string();
    let title_len = parsed["titleLength"].as_u64().unwrap_or(0);
    if title_len == 0 {
        findings.push(AuditFinding {
            category: "seo".into(), severity: "critical".into(),
            title: "Missing page title".into(),
            detail: "The page has no <title> tag. Search engines use this for ranking and display.".into(),
            score: Some(0.0),
        });
    } else if title_len < 30 {
        findings.push(AuditFinding {
            category: "seo".into(), severity: "warning".into(),
            title: "Title too short".into(),
            detail: format!("Title is {title_len} chars. Recommended: 50-60 characters for optimal SERP display."),
            score: Some(0.5),
        });
    }

    let meta_desc_len = parsed["metaDescriptionLength"].as_u64().unwrap_or(0);
    if meta_desc_len == 0 {
        findings.push(AuditFinding {
            category: "seo".into(), severity: "warning".into(),
            title: "Missing meta description".into(),
            detail: "No <meta name='description'> found. Add one (120-160 chars) for better search visibility.".into(),
            score: Some(0.3),
        });
    }

    if parsed["h1Count"].as_u64().unwrap_or(0) > 1 {
        findings.push(AuditFinding {
            category: "seo".into(), severity: "warning".into(),
            title: "Multiple H1 tags".into(),
            detail: "Page has more than one <h1>. Use a single H1 for the main heading.".into(),
            score: Some(0.6),
        });
    }

    if parsed["hasSkippedHeadings"].as_bool().unwrap_or(false) {
        findings.push(AuditFinding {
            category: "seo".into(), severity: "info".into(),
            title: "Skipped heading levels".into(),
            detail: "Heading hierarchy skips levels (e.g., H1 → H3). Use sequential H1→H2→H3.".into(),
            score: Some(0.7),
        });
    }

    // ── Accessibility checks ──
    let imgs_no_alt = parsed["imagesWithoutAlt"].as_u64().unwrap_or(0);
    if imgs_no_alt > 0 {
        findings.push(AuditFinding {
            category: "accessibility".into(), severity: "critical".into(),
            title: format!("{imgs_no_alt} image(s) missing alt text"),
            detail: "Screen readers cannot describe these images. Add descriptive alt attributes.".into(),
            score: Some(0.2),
        });
    }

    let inputs_no_label = parsed["formInputsWithoutLabel"].as_u64().unwrap_or(0);
    if inputs_no_label > 0 {
        findings.push(AuditFinding {
            category: "accessibility".into(), severity: "critical".into(),
            title: format!("{inputs_no_label} form input(s) without labels"),
            detail: "Assistive technology cannot identify these inputs. Use <label> or aria-label.".into(),
            score: Some(0.3),
        });
    }

    if !parsed["hasLang"].as_bool().unwrap_or(false) {
        findings.push(AuditFinding {
            category: "accessibility".into(), severity: "warning".into(),
            title: "Missing lang attribute".into(),
            detail: "<html> tag has no lang attribute. Screen readers need this for pronunciation.".into(),
            score: Some(0.5),
        });
    }

    let tabindex_pos = parsed["tabindexPositive"].as_u64().unwrap_or(0);
    if tabindex_pos > 0 {
        findings.push(AuditFinding {
            category: "accessibility".into(), severity: "warning".into(),
            title: format!("{tabindex_pos} element(s) with positive tabindex"),
            detail: "Positive tabindex values disrupt natural tab order. Use 0 or -1 only.".into(),
            score: Some(0.5),
        });
    }

    // ── Performance checks ──
    let imgs_no_dims = parsed["imagesWithoutDimensions"].as_u64().unwrap_or(0);
    if imgs_no_dims > 0 {
        findings.push(AuditFinding {
            category: "performance".into(), severity: "warning".into(),
            title: format!("{imgs_no_dims} image(s) without explicit dimensions"),
            detail: "Images without width/height cause layout shifts (CLS). Add dimensions to avoid reflow.".into(),
            score: Some(0.6),
        });
    }

    let total_elems = parsed["totalElements"].as_u64().unwrap_or(0);
    if total_elems > 5000 {
        findings.push(AuditFinding {
            category: "performance".into(), severity: "info".into(),
            title: format!("Large DOM ({total_elems} elements)"),
            detail: "Over 5000 DOM nodes can impact rendering performance and memory usage.".into(),
            score: Some(0.7),
        });
    }

    // ── Security checks ──
    if !parsed["hasHttps"].as_bool().unwrap_or(false) {
        findings.push(AuditFinding {
            category: "security".into(), severity: "critical".into(),
            title: "Page served over HTTP".into(),
            detail: "Connection is not encrypted. Consider upgrading to HTTPS.".into(),
            score: Some(0.1),
        });
    }

    let ext_no_rel = parsed["externalLinksNoRel"].as_u64().unwrap_or(0);
    if ext_no_rel > 0 {
        findings.push(AuditFinding {
            category: "security".into(), severity: "warning".into(),
            title: format!("{ext_no_rel} external link(s) missing rel='noopener'"),
            detail: "External links without noopener are vulnerable to tabnabbing attacks.".into(),
            score: Some(0.4),
        });
    }

    let inline_handlers = parsed["inlineEventHandlers"].as_u64().unwrap_or(0);
    if inline_handlers > 0 {
        findings.push(AuditFinding {
            category: "security".into(), severity: "info".into(),
            title: format!("{inline_handlers} inline event handler(s) detected"),
            detail: "Inline event handlers (onclick, onload, etc.) violate Content Security Policy best practices.".into(),
            score: Some(0.6),
        });
    }

    // ── Perf metrics via CDP Performance.getMetrics ──
    let perf_metrics = cdp.get_performance_metrics(tab).await.unwrap_or_default();

    // Calculate overall score
    let total_findings = findings.len();
    let avg_score = if total_findings > 0 {
        findings.iter().filter_map(|f| f.score).sum::<f64>() / total_findings as f64
    } else {
        1.0
    };

    Ok(AuditReport {
        url: tab.url.clone(),
        title,
        findings,
        overall_score: avg_score,
        perf_metrics,
    })
}
