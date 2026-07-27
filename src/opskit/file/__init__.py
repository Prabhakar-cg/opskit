"""File operations — importable API and CLI sub-app.

Read-only file diagnostics (line-ending/encoding detection, structured-format validation,
type sniffing, checksums, structural diff, stat, duplicate detection) plus guarded, opt-in
write commands (format conversion, line-ending normalization, encoding transcoding,
pretty-printing) under constitution Art. X's file-utility exception (v1.3.0).
"""

from __future__ import annotations
