"""
Plugin System for Jambubrowser
Inspired by Harness_App's plugin architecture.
Provides extensible task execution with sandboxed plugins.
"""

from .manager import PluginManager, Plugin, PluginResult

__all__ = ["PluginManager", "Plugin", "PluginResult"]
