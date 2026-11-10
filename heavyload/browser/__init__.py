"""Optional browser-side page metrics (stdlib Chrome DevTools Protocol)."""

from .cdp import CDPClient, CDPError, collect, list_targets  # noqa: F401

__all__ = ["CDPClient", "CDPError", "collect", "list_targets"]
