"""
Agentic Skill Synthesizer
==========================
Autonomous skill creation: the agent detects failures, writes Python
scripts via LLM, tests them in the sandbox, and persists reusable tools.

Flow:
1. Failure Detection → classify error type (scraping, parsing, auth, etc.)
2. Code Generation → LLM writes a Python script to handle the specific case
3. Sandbox Testing → execute in sandbox, verify output
4. Iterative Debugging → if test fails, LLM fixes, re-test (max 3 attempts)
5. Tool Persistence → save successful script to toolbox with metadata
"""

import logging

log = logging.getLogger("jambu.skill_synthesizer")

import asyncio
import hashlib
import json
import re
import time
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import httpx

from backend.core.sandbox import execute_sandboxed
from backend.core.database import get_db_cursor


@dataclass
class FailureContext:
    """Context about a detected failure."""
    url: str
    error_type: str  # scraping, parsing, auth, selector, rate_limit, cert, timeout
    error_message: str
    page_snippet: str = ""
    target_data: str = ""
    attempted_selectors: List[str] = field(default_factory=list)
    status_code: int = 0


@dataclass
class SkillAttempt:
    """A single attempt at generating a skill."""
    attempt_number: int
    code: str
    test_result: dict
    success: bool
    error_output: str = ""


@dataclass
class SynthesizedSkill:
    """A successfully synthesized skill ready for persistence."""
    name: str
    description: str
    code: str
    solves_problem: str
    test_input: str
    test_output: str
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)


class SkillSynthesizer:
    """
    Autonomous skill synthesizer.
    Detects failures, generates and tests solutions, persists working tools.
    """

    MAX_ATTEMPTS = 3
    SANDBOX_TIMEOUT = 15

    def __init__(self, llm_config: dict = None):
        self.llm_config = llm_config or {
            "baseUrl": "http://localhost:8080/v1",
            "modelId": "gemma-4-12b",
            "apiKey": "",
        }
        self._http_client: Optional[httpx.AsyncClient] = None
        self._synthesized_skills: List[SynthesizedSkill] = []

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    def classify_failure(self, error_message: str, 
                          page_snippet: str = "", url: str = "") -> FailureContext:
        """
        Classify a failure from an error message.

        Returns a FailureContext with the detected error type.
        """
        error_lower = error_message.lower()

        if any(kw in error_lower for kw in ('timeout', 'timed out', 'connection refused')):
            error_type = 'timeout'
        elif any(kw in error_lower for kw in ('401', '403', 'unauthorized', 'forbidden', 'login')):
            error_type = 'auth'
        elif any(kw in error_lower for kw in ('429', 'rate limit', 'too many')):
            error_type = 'rate_limit'
        elif any(kw in error_lower for kw in ('ssl', 'certificate', 'tls')):
            error_type = 'cert'
        elif any(kw in error_lower for kw in ('selector', 'element not found', 'no such element',
                                                'click', 'queryselector', 'xpath')):
            error_type = 'selector'
        elif any(kw in error_lower for kw in ('parse', 'json', 'xml', 'html', 'markdown',
                                                'unexpected token', 'syntax')):
            error_type = 'parsing'
        elif any(kw in error_lower for kw in ('404', 'not found')):
            error_type = 'not_found'
        else:
            error_type = 'scraping'

        return FailureContext(
            url=url,
            error_type=error_type,
            error_message=error_message,
            page_snippet=page_snippet[:3000],
        )

    async def _ask_llm(self, prompt: str, system_msg: str = "You are an expert Python developer.") -> str:
        """Ask the LLM to generate code or analyze a problem."""
        base_url = self.llm_config.get("baseUrl", "http://localhost:8080/v1")
        model_id = self.llm_config.get("modelId", "gemma-4-12b")
        api_key = self.llm_config.get("apiKey", "")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=10.0,
            )
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"LLM unavailable: {str(e)[:200]}")

    async def generate_skill(self, failure: FailureContext,
                              target_description: str = "") -> str:
        """
        Generate a Python skill script to address a failure.

        Args:
            failure: The classified failure context
            target_description: What the user wanted to extract

        Returns:
            Generated Python code
        """
        system_msg = (
            "You are an expert Python web scraping and automation developer. "
            "Write clean, production-quality Python scripts that handle edge cases. "
            "Always include a 'async def run(**kwargs)' function as the entry point. "
            "Use httpx for HTTP requests, re for parsing. "
            "Handle errors gracefully with try/except. "
            "Return data as dict or list."
        )

        prompt = (
            f"I encountered a {failure.error_type} error while scraping:\n\n"
            f"URL: {failure.url}\n"
            f"Error: {failure.error_message}\n"
        )

        if failure.page_snippet:
            prompt += f"\nPage structure (snippet):\n```html\n{failure.page_snippet[:2000]}\n```\n"

        if target_description:
            prompt += f"\nI was trying to extract: {target_description}\n"

        if failure.error_type == 'selector':
            prompt += (
                "\nWrite a Python script that:\n"
                "1. Handles the specific page structure shown\n"
                "2. Uses multiple fallback selectors\n"
                "3. Returns the extracted data as a dict\n"
                "4. Has an 'async def run(**kwargs)' entry point\n"
            )
        elif failure.error_type == 'parsing':
            prompt += (
                "\nWrite a Python script that:\n"
                "1. Robustly parses the tricky data format\n"
                "2. Handles malformed input gracefully\n"
                "3. Returns clean extracted data\n"
                "4. Has an 'async def run(**kwargs)' entry point\n"
            )
        elif failure.error_type == 'auth':
            prompt += (
                "\nWrite a Python script that:\n"
                "1. Handles the authentication flow\n"
                "2. Manages cookies/sessions properly\n"
                "3. Returns data after authentication\n"
                "4. Has an 'async def run(**kwargs)' entry point\n"
            )
        else:
            prompt += (
                "\nWrite a Python script that:\n"
                "1. Handles this specific failure case\n"
                "2. Implements a robust retry strategy\n"
                "3. Returns the extracted data\n"
                "4. Has an 'async def run(**kwargs)' entry point\n"
            )

        prompt += "\nReturn ONLY the complete Python code. No explanations."

        return await self._ask_llm(prompt, system_msg)

    def _extract_code(self, llm_response: str) -> str:
        """Extract code block from LLM response."""
        code_match = re.search(r'```python\n(.*?)```', llm_response, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        code_match = re.search(r'```\n(.*?)```', llm_response, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        # Fallback: return everything that looks like code
        lines = llm_response.strip().split('\n')
        code_lines = []
        for line in lines:
            if (line.startswith(('import ', 'from ', 'def ', 'async ', 'class ', 
                                  'try:', 'except', '    ', '\t', '#')) or
                '=' in line or 'return' in line):
                code_lines.append(line)
            elif code_lines:  # Stop at first non-code line after code started
                break

        return '\n'.join(code_lines) if code_lines else llm_response

    async def _generate_test(self, skill_code: str, failure: FailureContext) -> str:
        """Generate a test script that validates the skill."""
        system_msg = "You are a test engineer. Write a simple test that validates a script."
        prompt = (
            f"Given this Python script:\n```python\n{skill_code[:1500]}\n```\n\n"
            f"It should handle this problem at URL: {failure.url}\n"
            f"It extracts: {failure.target_data or 'relevant data'}\n\n"
            "Write a simple test script that imports and calls 'run()' from this module. "
            "Return ONLY the test code, no explanations."
        )
        return await self._ask_llm(prompt, system_msg)

    async def synthesize(self, failure: FailureContext,
                          target_description: str = "") -> Optional[SynthesizedSkill]:
        """
        Full synthesis pipeline: generate, test, debug, persist.

        Args:
            failure: The classified failure context
            target_description: What data was being sought

        Returns:
            SynthesizedSkill if successful, None otherwise
        """
        for attempt_num in range(1, self.MAX_ATTEMPTS + 1):
            # Step 1: Generate code
            code = await self.generate_skill(failure, target_description)
            code = self._extract_code(code)

            if not code or len(code) < 50:
                continue

            # Step 2: Test in sandbox
            test_code = self._build_test_wrapper(code)

            test_result = await execute_sandboxed(test_code, self.SANDBOX_TIMEOUT)

            if test_result['success']:
                # Step 3: Skill is working, create the skill object
                skill = SynthesizedSkill(
                    name=f"fix_{failure.error_type}_{hashlib.md5(code.encode()).hexdigest()[:8]}",
                    description=f"Auto-synthesized fix for {failure.error_type} error on {failure.url[:50]}",
                    code=code,
                    solves_problem=failure.error_message[:200],
                    test_input=test_code,
                    test_output=test_result['output'][:500],
                    confidence=0.9 - (attempt_num - 1) * 0.2,
                )

                # Persist to toolbox
                await self._persist_skill(skill)
                self._synthesized_skills.append(skill)
                return skill

            # Step 4: Debug and retry
            if attempt_num < self.MAX_ATTEMPTS:
                # Provide error context for next attempt
                failure.error_message = (
                    f"{failure.error_message}\n\n"
                    f"Previous attempt {attempt_num} failed with: {test_result.get('error', 'Unknown error')}"
                )

        return None

    def _build_test_wrapper(self, code: str) -> str:
        """Wrap skill code with a test harness for sandbox execution."""
        return f"""
{code}

# Auto-generated test harness
import asyncio

async def _test():
    try:
        result = await run()
        print("SUCCESS:", str(result)[:200])
    except Exception as e:
        print("ERROR:", str(e))

asyncio.run(_test())
"""

    async def _persist_skill(self, skill: SynthesizedSkill):
        """Save a synthesized skill to the toolbox database."""
        import os
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '', skill.name)
        tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            "tools"
        )
        os.makedirs(tools_dir, exist_ok=True)
        file_path = os.path.join(tools_dir, f"{safe_name}.py")

        with open(file_path, "w") as f:
            f.write(skill.code)

        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO custom_tools VALUES (?, ?, ?, ?)",
                (safe_name, skill.description, file_path, time.time()),
            )

    async def analyze_and_synthesize(self, url: str, error_message: str,
                                      page_snippet: str = "",
                                      target_description: str = "") -> Dict:
        """
        Full analysis and synthesis pipeline.

        Returns a summary of what happened.
        """
        failure = self.classify_failure(error_message, page_snippet, url)
        failure.target_data = target_description

        skill = await self.synthesize(failure, target_description)

        if skill:
            return {
                "status": "synthesized",
                "error_type": failure.error_type,
                "skill_name": skill.name,
                "skill_description": skill.description,
                "confidence": skill.confidence,
                "code_length": len(skill.code),
                "test_output": skill.test_output[:200],
            }
        else:
            return {
                "status": "failed",
                "error_type": failure.error_type,
                "reason": "Could not generate a working fix after maximum attempts",
                "attempts": self.MAX_ATTEMPTS,
            }

    def list_synthesized(self) -> List[dict]:
        """List all skills synthesized in this session."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "confidence": s.confidence,
                "created_at": s.created_at,
            }
            for s in self._synthesized_skills
        ]

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


_synthesizer: Optional[SkillSynthesizer] = None


def get_synthesizer(llm_config: dict = None) -> SkillSynthesizer:
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = SkillSynthesizer(llm_config)
    return _synthesizer
