"""opskit — cross-platform diagnostics for engineers, one toolkit for every OS.

This is the package root. Public, category-scoped APIs (e.g. ``opskit.dns``) are added
per feature; see the project constitution for the API-first design rules.
"""

from __future__ import annotations

import logging

__version__ = "0.1.6"  # x-release-please-version

__all__ = ["__version__"]

# Good-citizen library logging: silent unless the host app configures a handler.
logging.getLogger("opskit").addHandler(logging.NullHandler())
