"""Shared, category-agnostic console setup.

``rich.Console`` auto-detects a TTY (plain when piped) and honors ``NO_COLOR``; ``no_color``
forces plain output regardless. Category-specific rendering lives with its category (e.g.
:mod:`opskit.dns.output`) so ``core`` stays free of category models.
"""

from __future__ import annotations

from rich.console import Console


def make_console(*, no_color: bool = False) -> Console:
    """Return a console configured for the current output context.

    ``no_color`` forces plain output when set; when unset, ``Console`` is left to its default
    (which honors the ``NO_COLOR`` environment variable and auto-detects a non-TTY).
    """
    return Console(no_color=True if no_color else None, highlight=False)
