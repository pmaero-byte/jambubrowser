"""
Plugin Manager for Jambubrowser
Inspired by Harness_App's plugin architecture with sandboxed execution.
"""

import asyncio
import json
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable


@dataclass
class PluginResult:
    """Result from a plugin execution."""
    success: bool
    data: Any = None
    error: str = ""
    duration_ms: int = 0
    plugin_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class Plugin(ABC):
    """Base class for all plugins."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin name."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Plugin description."""
        pass
    
    @property
    def version(self) -> str:
        """Plugin version."""
        return "1.0.0"
    
    @property
    def capabilities(self) -> List[str]:
        """List of capabilities this plugin provides."""
        return []
    
    @property
    def requires_network(self) -> bool:
        """Whether this plugin requires network access."""
        return False
    
    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        """Execute the plugin with given parameters."""
        pass
    
    async def validate_params(self, params: Dict[str, Any]) -> tuple[bool, str]:
        """Validate parameters before execution. Returns (valid, error_message)."""
        return True, ""


class WebSearchPlugin(Plugin):
    """Web search plugin using multi-engine search."""
    
    @property
    def name(self) -> str:
        return "web_search"
    
    @property
    def description(self) -> str:
        return "Search the web using multiple search engines"
    
    @property
    def capabilities(self) -> List[str]:
        return ["search", "research", "web"]
    
    @property
    def requires_network(self) -> bool:
        return True
    
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        from backend.modules.search import multi_engine_search
        query = params.get("query", "")
        if not query:
            return PluginResult(success=False, error="Missing 'query' parameter")
        
        start = time.time()
        results = await multi_engine_search(query)
        duration = int((time.time() - start) * 1000)
        
        return PluginResult(
            success=True,
            data=results,
            duration_ms=duration,
            plugin_name=self.name,
            metadata={"query": query, "result_count": len(results.get("results", []))}
        )


class WebScrapePlugin(Plugin):
    """Web scraping plugin."""
    
    @property
    def name(self) -> str:
        return "web_scrape"
    
    @property
    def description(self) -> str:
        return "Scrape content from a URL"
    
    @property
    def capabilities(self) -> List[str]:
        return ["scrape", "fetch", "web"]
    
    @property
    def requires_network(self) -> bool:
        return True
    
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        from backend.modules.scraper import scrape_url
        url = params.get("url", "")
        if not url:
            return PluginResult(success=False, error="Missing 'url' parameter")
        
        start = time.time()
        result = await scrape_url(url, params.get("query", ""))
        duration = int((time.time() - start) * 1000)
        
        return PluginResult(
            success=True,
            data=result,
            duration_ms=duration,
            plugin_name=self.name,
            metadata={"url": url}
        )


class CodeExecPlugin(Plugin):
    """Code execution plugin (sandboxed)."""
    
    @property
    def name(self) -> str:
        return "code_exec"
    
    @property
    def description(self) -> str:
        return "Execute code in a sandboxed environment"
    
    @property
    def capabilities(self) -> List[str]:
        return ["code", "exec", "programming"]
    
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        from backend.core.sandbox import execute_sandboxed
        code = params.get("code", "")
        if not code:
            return PluginResult(success=False, error="Missing 'code' parameter")
        
        timeout = params.get("timeout", 30)
        start = time.time()
        result = await execute_sandboxed(code, timeout)
        duration = int((time.time() - start) * 1000)
        
        return PluginResult(
            success=True,
            data=result,
            duration_ms=duration,
            plugin_name=self.name
        )


class MemoryPlugin(Plugin):
    """Memory storage and retrieval plugin."""
    
    @property
    def name(self) -> str:
        return "memory"
    
    @property
    def description(self) -> str:
        return "Store and retrieve information from memory"
    
    @property
    def capabilities(self) -> List[str]:
        return ["memory", "remember", "recall"]
    
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        from backend.core.database import memory_add, memory_search, memory_list
        action = params.get("action", "search")
        
        start = time.time()
        if action == "add":
            result = memory_add(
                category=params.get("category", "general"),
                key=params.get("key", ""),
                value=params.get("value", ""),
                importance=params.get("importance", 0.5)
            )
        elif action == "search":
            result = memory_search(params.get("query", ""), limit=params.get("limit", 10))
        elif action == "list":
            result = memory_list(category=params.get("category"), limit=params.get("limit", 50))
        else:
            return PluginResult(success=False, error=f"Unknown action: {action}")
        
        duration = int((time.time() - start) * 1000)
        return PluginResult(
            success=True,
            data=result,
            duration_ms=duration,
            plugin_name=self.name,
            metadata={"action": action}
        )


class LLMPlugin(Plugin):
    """LLM inference plugin using local Ollama."""
    
    @property
    def name(self) -> str:
        return "llm"
    
    @property
    def description(self) -> str:
        return "Local LLM inference via Ollama"
    
    @property
    def capabilities(self) -> List[str]:
        return ["llm", "chat", "generate", "inference"]
    
    async def execute(self, params: Dict[str, Any]) -> PluginResult:
        import httpx
        prompt = params.get("prompt", "")
        if not prompt:
            return PluginResult(success=False, error="Missing 'prompt' parameter")
        
        from backend.engine import LATEST_LLM_CONFIG
        start = time.time()
        
        try:
            response = httpx.post(
                f"{LATEST_LLM_CONFIG['baseUrl']}/chat/completions",
                json={
                    "model": LATEST_LLM_CONFIG["modelId"],
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                },
                timeout=params.get("timeout", 60)
            )
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            duration = int((time.time() - start) * 1000)
            
            return PluginResult(
                success=True,
                data={"response": content},
                duration_ms=duration,
                plugin_name=self.name,
                metadata={"model": LATEST_LLM_CONFIG["modelId"]}
            )
        except Exception as e:
            return PluginResult(success=False, error=str(e))


class PluginManager:
    """Manages plugin discovery, registration, and execution."""
    
    def __init__(self):
        self._plugins: Dict[str, Plugin] = {}
        self._load_builtin_plugins()
    
    def _load_builtin_plugins(self):
        """Load built-in plugins."""
        builtin = [
            WebSearchPlugin(),
            WebScrapePlugin(),
            CodeExecPlugin(),
            MemoryPlugin(),
            LLMPlugin(),
        ]
        for plugin in builtin:
            self.register(plugin)
    
    def register(self, plugin: Plugin):
        """Register a plugin."""
        self._plugins[plugin.name] = plugin
    
    def unregister(self, name: str):
        """Unregister a plugin."""
        self._plugins.pop(name, None)
    
    def get(self, name: str) -> Optional[Plugin]:
        """Get a plugin by name."""
        return self._plugins.get(name)
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all registered plugins."""
        return [
            {
                "name": p.name,
                "description": p.description,
                "version": p.version,
                "capabilities": p.capabilities,
                "requires_network": p.requires_network,
            }
            for p in self._plugins.values()
        ]
    
    def list_models(self) -> List[Dict[str, Any]]:
        """List available models (from LLM config)."""
        from backend.engine import LATEST_LLM_CONFIG
        return [
            {
                "id": LATEST_LLM_CONFIG["modelId"],
                "name": LATEST_LLM_CONFIG["modelId"],
                "provider": "ollama",
                "endpoint": LATEST_LLM_CONFIG["baseUrl"],
                "capabilities": ["chat", "completion", "vision"],
            }
        ]
    
    async def execute(self, plugin_name: str, params: Dict[str, Any]) -> PluginResult:
        """Execute a plugin by name."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return PluginResult(
                success=False,
                error=f"Plugin not found: {plugin_name}",
                plugin_name=plugin_name
            )
        
        # Validate params
        valid, error = await plugin.validate_params(params)
        if not valid:
            return PluginResult(
                success=False,
                error=f"Invalid parameters: {error}",
                plugin_name=plugin_name
            )
        
        # Execute with timeout
        try:
            result = await asyncio.wait_for(
                plugin.execute(params),
                timeout=params.get("timeout", 60)
            )
            return result
        except asyncio.TimeoutError:
            return PluginResult(
                success=False,
                error=f"Plugin execution timed out after {params.get('timeout', 60)}s",
                plugin_name=plugin_name
            )
        except Exception as e:
            return PluginResult(
                success=False,
                error=f"Plugin execution failed: {str(e)}",
                plugin_name=plugin_name,
                metadata={"traceback": traceback.format_exc()}
            )
    
    async def execute_chain(self, steps: List[Dict[str, Any]]) -> List[PluginResult]:
        """Execute a chain of plugins, passing output between them."""
        results = []
        context = {}
        
        for step in steps:
            plugin_name = step.get("plugin", "")
            params = step.get("params", {})
            
            # Inject context from previous steps
            if context:
                params["_context"] = context
            
            result = await self.execute(plugin_name, params)
            results.append(result)
            
            # Update context with result
            if result.success and result.data:
                context[plugin_name] = result.data
        
        return results


# Singleton instance
_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager
