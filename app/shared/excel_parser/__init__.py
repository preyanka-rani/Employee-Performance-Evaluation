"""
app/shared/excel_parser/__init__.py
───────────────────────────────────
Unified TL Excel parser — works for all teams (developer, support, future).

Public entry point:
    parse_tl_excel(file_bytes, team_key) → ParseResult
"""

from app.shared.excel_parser.parser import (
    ExcelParseError,
    ParseResult,
    parse_tl_excel,
)
from app.shared.excel_parser.row_schema import CanonicalRow, REQUIRED_FIELDS

__all__ = [
    "ExcelParseError",
    "ParseResult",
    "parse_tl_excel",
    "CanonicalRow",
    "REQUIRED_FIELDS",
]
