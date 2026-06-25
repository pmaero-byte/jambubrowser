"""Product Context Extractor — understands what a site DOES before analyzing it.

Runs FIRST in the audit pipeline. Scrapes the site, extracts:
- What the product does (value proposition)
- Who it's for (target audience)
- Key features
- Technology stack
- Business model
- User journey / conversion flow

This context is passed to all other employees so they can provide
context-aware analysis instead of generic checklists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from backend.employees.base import BaseEmployee, AuditData

log = logging.getLogger("jambu.employees.product_context")


@dataclass
class ProductContext:
    """Extracted product context passed to all employees."""
    what_it_does: str
    target_audience: str
    key_features: list[str]
    value_proposition: str
    tech_stack: list[str]
    business_model: str
    user_journey: str
    conversion_flow: str
    competitive_advantage: str
    raw_analysis: str

    def to_prompt_context(self) -> str:
        """Format as context for other employees' system prompts."""
        return f"""
## PRODUCT CONTEXT (extracted by Product Context Analyzer)

**What it does:** {self.what_it_does}

**Target audience:** {self.target_audience}

**Value proposition:** {self.value_proposition}

**Key features:**
{chr(10).join(f'- {f}' for f in self.key_features)}

**Tech stack:** {', '.join(self.tech_stack)}

**Business model:** {self.business_model}

**User journey:** {self.user_journey}

**Conversion flow:** {self.conversion_flow}

**Competitive advantage:** {self.competitive_advantage}

---

IMPORTANT: Use this context to provide SPECIFIC, ACTIONABLE recommendations.
- Reference the actual product features in your findings
- Suggest fixes that align with the product's tech stack
- Prioritize issues that affect the product's core user journey
- Consider the business model when assessing impact
"""


class ProductContextExtractor(BaseEmployee):
    name = "Product Context Analyzer"
    emoji = "🔍"
    max_tokens = 3000
    temperature = 0.3

    system_prompt = """You are a product analyst. Your job: understand what this website DOES, who it's FOR, and how it makes money (or doesn't).

Analyze the page content and extract:

1. **What it does** — One sentence. Not "it's a website" but "it's a CFD simulation tool that runs in the browser."

2. **Target audience** — Who uses this? Students? Engineers? Consumers? Be specific.

3. **Key features** — List 3-5 main features visible on the page.

4. **Value proposition** — Why would someone use this instead of alternatives?

5. **Tech stack** — What technologies does it use? (React, Next.js, WebGPU, etc.)

6. **Business model** — Free? Freemium? SaaS? Open source? Ads?

7. **User journey** — What does a user DO on this site? Sign up? Browse? Use a tool?

8. **Conversion flow** — What's the main CTA? How does the site convert visitors?

9. **Competitive advantage** — What makes this different from similar products?

OUTPUT FORMAT:
Return a JSON object:
{
  "what_it_does": "One sentence description",
  "target_audience": "Who uses this",
  "key_features": ["feature1", "feature2", "feature3"],
  "value_proposition": "Why use this",
  "tech_stack": ["tech1", "tech2"],
  "business_model": "Free/freemium/SaaS/etc",
  "user_journey": "What users do",
  "conversion_flow": "Main CTA and conversion path",
  "competitive_advantage": "What makes it different"
}

RULES:
- Be specific to THIS product, not generic
- If you can't determine something, say "unclear" rather than guessing
- Focus on what's VISIBLE on the page, not assumptions
- Reference actual text from the page (headings, descriptions, CTAs)"""

    def _prepare_data(self, data: AuditData) -> str:
        lines = [f"URL: {data.url}", f"Title: {data.title}\n"]

        lines.append("=== DOM STRUCTURE (accessibility tree) ===")
        if data.dom_snapshot:
            lines.append(data.dom_snapshot[:15000])
            if len(data.dom_snapshot) > 15000:
                lines.append(f"\n... (truncated, {len(data.dom_snapshot)} total chars)")
        else:
            lines.append("  (no DOM snapshot)")

        lines.append("\n=== PAGE SOURCE (first 20000 chars) ===")
        if data.page_source:
            lines.append(data.page_source[:20000])
            if len(data.page_source) > 20000:
                lines.append(f"\n... (truncated, {len(data.page_source)} total chars)")
        else:
            lines.append("  (no page source)")

        return "\n".join(lines)

    async def extract_context(self, data: AuditData) -> ProductContext:
        """Run the extractor and return structured ProductContext."""
        from backend.llm import ChatMessage, Role, get_registry, normalize_llm_response

        messages = self._build_messages(data)
        llm_messages = [
            ChatMessage(role=Role(m.role.value if hasattr(m.role, 'value') else str(m.role)), content=m.content)
            for m in messages
        ]

        try:
            response = await get_registry().chat(
                llm_messages,
                provider="minimax",
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            raw = normalize_llm_response(response.content)
        except Exception as exc:
            log.error("Product context extraction failed: %s", exc)
            return ProductContext(
                what_it_does="Unable to determine",
                target_audience="Unknown",
                key_features=[],
                value_proposition="Unknown",
                tech_stack=[],
                business_model="Unknown",
                user_journey="Unknown",
                conversion_flow="Unknown",
                competitive_advantage="Unknown",
                raw_analysis=f"Extraction failed: {exc}",
            )

        try:
            parsed = json.loads(raw)
            return ProductContext(
                what_it_does=parsed.get("what_it_does", "Unknown"),
                target_audience=parsed.get("target_audience", "Unknown"),
                key_features=parsed.get("key_features", []),
                value_proposition=parsed.get("value_proposition", "Unknown"),
                tech_stack=parsed.get("tech_stack", []),
                business_model=parsed.get("business_model", "Unknown"),
                user_journey=parsed.get("user_journey", "Unknown"),
                conversion_flow=parsed.get("conversion_flow", "Unknown"),
                competitive_advantage=parsed.get("competitive_advantage", "Unknown"),
                raw_analysis=raw,
            )
        except json.JSONDecodeError:
            return ProductContext(
                what_it_does=raw[:200] if raw else "Unknown",
                target_audience="Unknown",
                key_features=[],
                value_proposition="Unknown",
                tech_stack=[],
                business_model="Unknown",
                user_journey="Unknown",
                conversion_flow="Unknown",
                competitive_advantage="Unknown",
                raw_analysis=raw,
            )
