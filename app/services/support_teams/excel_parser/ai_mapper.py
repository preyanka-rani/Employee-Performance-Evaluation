"""
app/services/support_teams/excel_parser/ai_mapper.py
──────────────────────────────────────────────────────
AI-powered Excel column mapper.

When static alias matching fails to resolve all required fields, this module
asks the LLM (Claude → Groq fallback via LLMClient) to inspect the raw column
headers and a few sample data rows, then return a JSON mapping of
  {raw_column_header: canonical_field_name}

Canonical field names understood by the parser:
    employee_email      – employee email address (contains "@")
    support_readiness   – support readiness / issue handling score  (0-10)
    kpi                 – KPI / performance agreement score          (0-15)
    general             – general leadership assessment score        (0-15)
    employee_name       – full name of the employee (optional)
    employee_id         – internal employee ID (optional)

Usage:
    from app.services.support_teams.excel_parser.ai_mapper import map_columns_with_ai

    mapping = await map_columns_with_ai(headers, sample_rows)
    # Returns e.g. {"Email Address": "employee_email", "Readiness": "support_readiness"}
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# ── Valid canonical field names the AI may return ─────────────────────────────

_VALID_CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        "employee_email",
        "support_readiness",
        "kpi",
        "general",
        "employee_name",
        "employee_id",
    }
)

# ── LLM prompts ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert at understanding Excel spreadsheet column headers for \
employee performance evaluations.

Your task: given a list of column headers (and optional sample data), identify \
which column corresponds to each of the following canonical fields:

  employee_email    – The employee's work email address (values contain "@")
  support_readiness – Support readiness / issue handling score (numeric, range 0-10)
  kpi               – KPI / performance agreement score (numeric, range 0-15)
  general           – General leadership / team-lead assessment score (numeric, range 0-15)
  employee_name     – Full name of the employee (optional, text)
  employee_id       – Internal employee ID number (optional, often numeric or alphanumeric)

Rules:
  - Respond ONLY with a single valid JSON object, no extra text or markdown.
  - Keys   = EXACT column header strings from the input (case-sensitive, as given).
  - Values = one of the canonical field names above.
  - Omit any column you are not confident about.
  - Never guess for numeric score fields if sample values are not clearly numeric.

Example response:
{
  "Email Address": "employee_email",
  "Readiness & Handling (0-10)": "support_readiness",
  "Annual KPI (15%)": "kpi",
  "Leadership General (15%)": "general",
  "Employee Name": "employee_name"
}
"""


def _build_user_prompt(headers: list[str], sample_rows: list[list[Any]]) -> str:
    """Format the user portion of the prompt."""
    prompt_lines = [f"Column headers:\n{json.dumps(headers, ensure_ascii=False)}"]
    for i, row in enumerate(sample_rows[:3], 1):
        row_strs = [str(v) if v is not None else "" for v in row]
        prompt_lines.append(
            f"Sample row {i}: {json.dumps(row_strs, ensure_ascii=False)}"
        )
    prompt_lines.append("\nReturn the JSON mapping now:")
    return "\n".join(prompt_lines)


def _parse_json_from_response(raw: str) -> dict[str, str]:
    """
    Extract and validate the JSON object from the LLM response.

    Handles:
    - Plain JSON
    - Markdown code blocks (```json ... ```)
    - Leading/trailing whitespace or text
    """
    raw = raw.strip()

    # Try the full string first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences if present
    fenced = re.sub(r"```[a-z]*\n?", "", raw)
    try:
        return json.loads(fenced.strip())
    except json.JSONDecodeError:
        pass

    # Extract first {...} block
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {}


async def map_columns_with_ai(
    headers: list[str],
    sample_rows: list[list[Any]],
) -> dict[str, str]:
    """
    Use the LLM to map raw Excel column headers to canonical field names.

    This is an async fallback called only when static alias matching fails
    to identify one or more required columns.

    Args:
        headers:     List of raw column header strings from the Excel file.
        sample_rows: First N data rows as list-of-lists (used for context).

    Returns:
        Dict mapping ``raw_header → canonical_field_name``.
        May be partial (not all fields necessarily identified).
        Returns empty dict on any error so the caller can decide what to do.
    """
    if not headers:
        return {}

    # Import here to avoid circular imports and to allow mocking in tests
    from app.services.ai.claude_client import LLMClient  # noqa: PLC0415

    user_prompt = _build_user_prompt(headers, sample_rows)

    try:
        client = LLMClient()
        result = await client.invoke_with_fallback(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        raw_mapping = _parse_json_from_response(result.content)

        # Keep only entries where the key exists in our header list and the
        # value is a known canonical field name
        headers_set = set(headers)
        validated: dict[str, str] = {
            k: v
            for k, v in raw_mapping.items()
            if isinstance(k, str)
            and isinstance(v, str)
            and k in headers_set
            and v in _VALID_CANONICAL_FIELDS
        }

        logger.info(
            "ai_column_mapping_success",
            model=result.model_used,
            headers_sent=len(headers),
            mapped_fields=len(validated),
            mapping=validated,
        )
        return validated

    except Exception as exc:
        logger.error(
            "ai_column_mapping_failed",
            error=str(exc),
            headers=headers[:20],  # truncate for log safety
        )
        return {}
