"""Composable guided acquisition. No browser launch or network activity on import."""
from .contracts import Card, PageSnapshot, SiteAdapter
from .adapters import Registry, builtins

__all__ = ["Card", "PageSnapshot", "SiteAdapter", "Registry", "builtins"]
