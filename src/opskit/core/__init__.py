"""Shared cross-cutting concerns for opskit commands.

Holds the pieces every diagnostic category reuses: the exit-code map, the base exception
hierarchy, the versioned result envelope, and output rendering. Category packages (e.g.
``opskit.dns``) build on these; nothing here is category-specific.
"""

from __future__ import annotations
