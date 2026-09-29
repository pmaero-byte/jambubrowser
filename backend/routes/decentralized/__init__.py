"""
Decentralized & crypto routes.

Gate this entire subsystem behind ``JAMBU_ENABLE_DECENTRALIZED=1``.
When disabled, the routes are not registered and the decentralized
package imports are lazy no-ops.
"""

from backend.decentralized.gate import is_enabled

__all__ = ["is_enabled"]
