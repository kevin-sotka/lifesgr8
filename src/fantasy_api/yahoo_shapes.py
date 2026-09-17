"""
Yahoo's JSON conventions, in one place.

Yahoo encodes a list as {"0": {...}, "1": {...}, "count": 2} and an object as a
list of single-key fragments, sometimes nested a level deeper. Both ingest (for
discovery) and snapshot (for everything) read through these two helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List


def items(collection: Any) -> List[Any]:
    """Entries of a numbered collection, in order."""
    if not isinstance(collection, dict):
        return []
    keys = sorted((k for k in collection if k.isdigit()), key=int)
    return [collection[k] for k in keys]


def merge(fragments: Any) -> Dict[str, Any]:
    """Collapse a list of single-key fragments into one dict."""
    if isinstance(fragments, dict):
        return fragments
    out: Dict[str, Any] = {}
    for frag in fragments or []:
        if isinstance(frag, list):
            out.update(merge(frag))
        elif isinstance(frag, dict):
            out.update(frag)
    return out


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
