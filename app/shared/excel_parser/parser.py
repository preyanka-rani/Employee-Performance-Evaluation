"""
app/shared/excel_parser/parser.py
─────────────────────────────────
Unified TL Excel parser — used by every team.

Strategy (three stages, most-to-least specific):
  1. **Static alias matching** — normalise every header and look it up in the
     combined alias dict (developer + support aliases).
  2. **AI fallback** — send raw headers + sample rows to Claude (→ Groq).
  3. **Heuristic email detection** — pick the first column whose values look
     like email addresses (only if employee_email is still unresolved).

Auto-detects the header row: many HR files start with title rows before the
real headers, so we scan the first 20 rows to find the one that looks like
column labels.

This module is **async** because Stage 2 may call the LLM API.

Public API:
    parse_tl_excel(file_bytes, team_key) → ParseResult
    ParseResult.rows   : list[CanonicalRow]
    ParseResult.errors : list[str]            (per-row warnings)
    ParseResult.col_names : dict[str, str]    (canonical → original header)
    ParseResult.team_display_name : str       (taken from first row, if present)
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from app.core.logging_config import get_logger
from app.shared.excel_parser.ai_mapper import map_columns_with_ai
from app.shared.excel_parser.row_schema import TEAM_SCHEMAS, CanonicalRow

logger = get_logger(__name__)


# ── Exception ─────────────────────────────────────────────────────────────────


class ExcelParseError(Exception):
    """Raised when the uploaded Excel cannot be parsed or fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ParseResult:
    """Output of ``parse_tl_excel()``."""

    rows: list[CanonicalRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # canonical_field → original header string from the uploaded Excel
    col_names: dict[str, str] = field(default_factory=dict)
    # First row's team_name (raw) if a team column was present
    team_display_name: str = ""


# ── Score bounds ──────────────────────────────────────────────────────────────

_BOUNDS: dict[str, tuple[float, float]] = {
    "problem_solving": (0.0, 10.0),
    "support_readiness": (0.0, 10.0),
    "kpi": (0.0, 15.0),
    "general": (0.0, 15.0),
}

# ── Static alias dictionary ───────────────────────────────────────────────────
# Keys   = normalised alias (lowercase, underscores, no special chars).
# Values = canonical field name.
# Add new rows here as real-world file variants are discovered.
# Covers BOTH the developer aliases (from the old services/data_sources/excel_parser.py)
# AND the support aliases (from the old services/support_teams/excel_parser/parser.py).

_ALIAS_MAP: dict[str, str] = {
    # ── employee_email ────────────────────────────────────────────────────────
    "employee_email": "employee_email",
    "email": "employee_email",
    "email_address": "employee_email",
    "work_email": "employee_email",
    "official_email": "employee_email",
    "company_email": "employee_email",
    "emp_email": "employee_email",
    "user_email": "employee_email",
    # ── employee_name ────────────────────────────────────────────────────────
    "employee_name": "employee_name",
    "name": "employee_name",
    "full_name": "employee_name",
    "employee": "employee_name",
    "resource_name": "employee_name",
    "staff_name": "employee_name",
    # ── employee_id ──────────────────────────────────────────────────────────
    "employee_id": "employee_id",
    "emp_id": "employee_id",
    "id": "employee_id",
    "staff_id": "employee_id",
    "personnel_id": "employee_id",
    # ── problem_solving (developer 0-10) ─────────────────────────────────────
    "problem_solving": "problem_solving",
    "tl_problem_solving": "problem_solving",
    "tl_ps": "problem_solving",
    "ps": "problem_solving",
    "critical_thinking": "problem_solving",
    # ── support_readiness (support 0-10) ─────────────────────────────────────
    "support_readiness": "support_readiness",
    "support_readiness_issue_handling": "support_readiness",
    "support_readiness_issue_handling_10": "support_readiness",
    "support_readiness_10": "support_readiness",
    "readiness": "support_readiness",
    "readiness_score": "support_readiness",
    "issue_handling": "support_readiness",
    "issue_handling_10": "support_readiness",
    "support_handling": "support_readiness",
    "service_readiness": "support_readiness",
    "technical_readiness": "support_readiness",
    "readiness_handling": "support_readiness",
    "support_issue_handling": "support_readiness",
    "readiness_and_issue_handling": "support_readiness",
    "issue_readiness": "support_readiness",
    "avg_critical_thinking_score": "support_readiness",
    "critical_thinking_score": "support_readiness",
    "support_readiness_score": "support_readiness",
    # ── kpi (0-15) ────────────────────────────────────────────────────────────
    "kpi": "kpi",
    "tl_kpi": "kpi",
    "kpi_15": "kpi",
    "kpi_agreement": "kpi",
    "kpi_agreement_15": "kpi",
    "performance_agreement": "kpi",
    "performance_agreement_15": "kpi",
    "annual_performance_agreement": "kpi",
    "annual_performance_agreement_apa_15": "kpi",
    "annual_performance_agreement_apa": "kpi",
    "apa": "kpi",
    "apa_15": "kpi",
    "key_performance_indicator": "kpi",
    "key_performance_indicators": "kpi",
    "kpi_score": "kpi",
    "performance_score": "kpi",
    "target_achievement": "kpi",
    "performance_target": "kpi",
    "performance_agreement_score": "kpi",
    # ── general (0-15) ────────────────────────────────────────────────────────
    "general": "general",
    "tl_general": "general",
    "general_15": "general",
    "general_assessment": "general",
    "general_assessment_15": "general",
    "leadership_general_assessment": "general",
    "leadership_general_assessment_15": "general",
    "team_leader_assessment": "general",
    "team_leader_assessment_15": "general",
    "team_lead_assessment": "general",
    "team_lead_assessment_15": "general",
    "tl_assessment": "general",
    "tl_assessment_15": "general",
    "leadership_assessment": "general",
    "leadership_review": "general",
    "general_review": "general",
    "overall_assessment": "general",
    "overall_general": "general",
    "behavioral_assessment": "general",
    "soft_skills": "general",
    "team_lead_assesment_score": "general",
    "tl_general_assessment": "general",
    # ── gitlab_username ──────────────────────────────────────────────────────
    "gitlab_username": "gitlab_username",
    "gitlab": "gitlab_username",
    "gitlab_user": "gitlab_username",
    "gitlab_id": "gitlab_username",
    # ── team_name ────────────────────────────────────────────────────────────
    "team": "team_name",
    "team_name": "team_name",
    "team_names": "team_name",
    "team_label": "team_name",
    "department": "team_name",
    "dept": "team_name",
    "division": "team_name",
    "group_name": "team_name",
}

# At minimum we need: employee_email + ONE of (problem_solving/support_readiness) + kpi + general
# So we resolve the email + kpi + general via static; PS-or-readiness is resolved
# if EITHER alias is found.
_REQUIRED_BASE: frozenset[str] = frozenset({"employee_email", "kpi", "general"})


# ── Header normalisation ──────────────────────────────────────────────────────


def _normalise_header(raw: str) -> str:
    """
    Normalise a raw column header so it can match alias keys.

    Steps:
      1. Strip leading/trailing whitespace.
      2. Lowercase.
      3. Replace whitespace, hyphens and forward-slashes with underscores.
      4. Remove characters: ( ) % & , . # @ ! ? ' "
      5. Collapse consecutive underscores.
      6. Strip leading/trailing underscores.
    """
    s = str(raw).strip().lower()
    s = re.sub(r"[\s\-/]+", "_", s)
    s = re.sub(r"[()%&,\.#@!?'\"]+", "", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def _build_column_map_static(headers: list[str]) -> dict[str, str]:
    """
    Attempt to build a canonical→raw_header map using the static alias dict.

    Returns:
        Dict mapping canonical_field → raw_header (the first matching alias
        per canonical field wins).  May be partial.
    """
    canonical_to_raw: dict[str, str] = {}
    for raw in headers:
        normalised = _normalise_header(raw)
        canonical = _ALIAS_MAP.get(normalised)
        if canonical and canonical not in canonical_to_raw:
            canonical_to_raw[canonical] = raw
    return canonical_to_raw


# ── Email heuristic ───────────────────────────────────────────────────────────


def _detect_email_column(df: pd.DataFrame, min_ratio: float = 0.5) -> str | None:
    """
    Return the first column name whose non-null values look mostly like emails.

    ``min_ratio`` is the fraction of non-null values that must contain "@".
    """
    for col in df.columns:
        series = df[col].dropna().astype(str)
        if series.empty:
            continue
        ratio = series.str.contains("@", na=False).mean()
        if ratio >= min_ratio:
            return col
    return None


# ── Dynamic header-row detection ─────────────────────────────────────────────

_HEADER_KEYWORDS: frozenset[str] = frozenset(
    {
        "email",
        "name",
        "kpi",
        "general",
        "readiness",
        "support",
        "assessment",
        "employee",
        "id",
        "no",
        "sl",
        "serial",
        "handling",
        "performance",
        "agreement",
        "leadership",
        "month",
        "score",
        "total",
        "marks",
        "problem",
        "solving",
    }
)


def _find_header_row(df_raw: pd.DataFrame, max_search: int = 20) -> int:
    """
    Scan the first *max_search* rows to identify which one contains column
    headers rather than data or a title.

    Scoring per row:
      +1  per non-null value that is a short string (< 80 chars)
      -3  per value that looks like a plain number
      +5  per recognised header keyword found anywhere in the row text
      +2  bonus when at least 3 non-null cells are present

    Returns the 0-based row index with the highest score.
    Defaults to 0 if nothing stands out.
    """
    best_row = 0
    best_score = -999

    for i in range(min(max_search, len(df_raw))):
        row_vals = df_raw.iloc[i].dropna()
        if len(row_vals) < 2:
            continue

        values = [str(v).strip() for v in row_vals]

        short_text = sum(1 for v in values if 0 < len(v) < 80)
        numeric = sum(1 for v in values if re.fullmatch(r"-?\d+(\.\d+)?", v))
        row_text = " ".join(v.lower() for v in values)
        kw_hits = sum(1 for kw in _HEADER_KEYWORDS if kw in row_text)
        multi_cell_bonus = 2 if len(row_vals) >= 3 else 0

        score = short_text - numeric * 3 + kw_hits * 5 + multi_cell_bonus

        if score > best_score:
            best_score = score
            best_row = i

    return best_row


def _load_with_header_detection(file_bytes: bytes) -> tuple[pd.DataFrame, list[str]]:
    """
    Load an Excel file, auto-detecting the true header row.

    Many HR/TL Excel files start with merged title rows (e.g.
    "Employee Assessment Summary for the Month February 2026") followed by
    the actual column headers on row 2 or 3.

    Returns:
        (DataFrame with real headers, raw header strings list)
    """
    # Pass 1: read without assuming a header to expose all raw rows
    try:
        df_raw = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
    except Exception as exc:
        raise ExcelParseError([f"Cannot read Excel file: {exc}"]) from exc

    if df_raw.empty or df_raw.shape[1] == 0:
        raise ExcelParseError(["Excel file appears to be empty."])

    header_row_idx = _find_header_row(df_raw)
    logger.info("header_row_detected", row_index=header_row_idx)

    # Pass 2: read again with the correct header row
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), header=header_row_idx, dtype=str)
    except Exception as exc:
        raise ExcelParseError(
            [f"Cannot re-read Excel with detected header row {header_row_idx}: {exc}"]
        ) from exc

    # Drop columns that are entirely NaN (e.g. trailing empty columns)
    df = df.dropna(axis=1, how="all")

    return df, df.columns.tolist()


# ── Sample rows helper (for AI prompt) ────────────────────────────────────────


def _get_sample_rows(df: pd.DataFrame, n: int = 3) -> list[list]:
    """Return up to *n* non-all-null rows as list-of-lists for AI context."""
    samples: list[list] = []
    for _, row in df.iterrows():
        if not row.isnull().all():
            samples.append(row.tolist())
        if len(samples) >= n:
            break
    return samples


# ── Core async parser ─────────────────────────────────────────────────────────


async def parse_tl_excel(
    file_bytes: bytes,
    team_key: str = "",
) -> ParseResult:
    """
    Parse a TL Assessment Excel upload — unified across all teams.

    The function is **async** because Stage 2 may call the LLM API.

    When ``team_key`` is provided and exists in ``TEAM_SCHEMAS``, the parser
    validates that the **team-specific** required fields are present (e.g.
    ``problem_solving`` for developer/SQA, ``support_readiness`` for support).
    Without a recognised team key the generic ``_REQUIRED_BASE`` logic applies,
    accepting either score field.

    Args:
        file_bytes: Raw bytes of the uploaded .xlsx/.xls file.
        team_key:   Team key used to look up team-specific field requirements
                    in ``TEAM_SCHEMAS``.  Pass ``"sqa"`` to enforce the SQA
                    contract (``problem_solving``, not ``support_readiness``).

    Returns:
        ParseResult with valid rows and per-row warning strings.

    Raises:
        ExcelParseError: If the file cannot be read or required columns
            cannot be resolved even after AI assistance.
    """
    log = logger.bind(team=team_key) if team_key else logger

    # Determine which score field the team requires
    team_fields = TEAM_SCHEMAS.get(team_key)
    if team_fields is not None:
        required_base: set[str] = set(team_fields) - {"problem_solving", "support_readiness"}
        ps_field: str = (
            "problem_solving" if "problem_solving" in team_fields else "support_readiness"
        )
        log.info("team_schema_active", team=team_key, required=sorted(team_fields))
    else:
        required_base = set(_REQUIRED_BASE)
        ps_field = "problem_solving"  # default — either is accepted at validation

    # ── Load file (with automatic header-row detection) ───────────────────────
    df, raw_headers = _load_with_header_detection(file_bytes)

    # ── Stage 1: static alias matching ───────────────────────────────────────
    col_map: dict[str, str] = _build_column_map_static(raw_headers)
    missing: set[str] = required_base - set(col_map.keys())
    # Need at least ONE of problem_solving or support_readiness
    has_ps = "problem_solving" in col_map or "support_readiness" in col_map
    if not has_ps:
        missing.add("problem_solving_or_support_readiness")

    log.info(
        "static_mapping_result",
        resolved=list(col_map.keys()),
        missing=sorted(missing),
    )

    # ── Stage 2: AI fallback ─────────────────────────────────────────────────
    if missing:
        log.info("attempting_ai_column_mapping", missing_fields=sorted(missing))
        try:
            sample_rows = _get_sample_rows(df)
            ai_mapping: dict[str, str] = await map_columns_with_ai(
                raw_headers, sample_rows
            )
            # ai_mapping: {raw_header → canonical_field}
            for raw_col, canonical in ai_mapping.items():
                if canonical not in col_map:  # don't override static matches
                    col_map[canonical] = raw_col
        except Exception as exc:
            log.warning("ai_mapping_skipped", reason=str(exc))

        # Recompute missing after AI attempt
        missing = required_base - set(col_map.keys())
        has_ps = "problem_solving" in col_map or "support_readiness" in col_map
        if not has_ps:
            missing.add("problem_solving_or_support_readiness")

    # ── Stage 3: heuristic email detection ───────────────────────────────────
    if "employee_email" in missing:
        detected = _detect_email_column(df)
        if detected:
            col_map["employee_email"] = detected
            missing.discard("employee_email")
            log.info("email_column_detected_heuristically", column=detected)

    # ── Fatal: still missing required columns ─────────────────────────────────
    if missing:
        raise ExcelParseError(
            [
                f"Missing required columns after static + AI mapping: "
                f"{sorted(missing)}. "
                f"Detected headers: {raw_headers}"
            ]
        )

    # ── Parse rows ────────────────────────────────────────────────────────────
    parse_errors: list[str] = []
    rows: list[CanonicalRow] = []

    # Drop rows that are completely null across all required columns
    if team_fields is not None:
        row_base_fields: set[str] = set(team_fields)
    else:
        row_base_fields = set(_REQUIRED_BASE)
        if "problem_solving" in col_map:
            row_base_fields.add("problem_solving")
        elif "support_readiness" in col_map:
            row_base_fields.add("support_readiness")
    required_raw_cols = [col_map[f] for f in row_base_fields if f in col_map]
    df = df.dropna(subset=required_raw_cols, how="all").reset_index(drop=True)

    for idx, row_series in df.iterrows():
        row_num = int(idx) + 2  # +2 = header offset + 1-based display

        def _cell(canonical: str, default: str = "") -> str:
            raw_col = col_map.get(canonical)
            if raw_col is None:
                return default
            val = row_series.get(raw_col)
            return (
                str(val).strip()
                if val is not None and str(val) not in ("nan", "None", "")
                else default
            )

        email = _cell("employee_email").lower()
        if not email or "@" not in email:
            parse_errors.append(f"Row {row_num}: invalid or missing email '{email}'")
            continue

        try:
            ps_raw = _cell("problem_solving", "0") or "0"
            sr_raw = _cell("support_readiness", "0") or "0"
            kpi_raw = _cell("kpi", "0") or "0"
            general_raw = _cell("general", "0") or "0"

            ps_val = float(ps_raw)
            sr_val = float(sr_raw)
            kpi = float(kpi_raw)
            gen = float(general_raw)
        except (ValueError, TypeError) as exc:
            parse_errors.append(f"Row {row_num}: numeric conversion error – {exc}")
            continue

        # Clamp to allowed bounds
        ps_val = min(
            max(ps_val, _BOUNDS["problem_solving"][0]),
            _BOUNDS["problem_solving"][1],
        )
        sr_val = min(
            max(sr_val, _BOUNDS["support_readiness"][0]),
            _BOUNDS["support_readiness"][1],
        )
        kpi = min(max(kpi, _BOUNDS["kpi"][0]), _BOUNDS["kpi"][1])
        gen = min(max(gen, _BOUNDS["general"][0]), _BOUNDS["general"][1])

        emp_id = _cell("employee_id")
        if emp_id in ("nan", "None"):
            emp_id = ""

        emp_name = _cell("employee_name")
        if emp_name in ("nan", "None"):
            emp_name = ""

        gitlab_user = _cell("gitlab_username") or None
        if gitlab_user in ("nan", "None", ""):
            gitlab_user = None

        team_name_val = _cell("team_name", "")
        if team_name_val in ("nan", "None"):
            team_name_val = ""

        rows.append(
            CanonicalRow(
                employee_id=emp_id,
                employee_email=email,
                employee_name=emp_name,
                problem_solving=ps_val,
                support_readiness=sr_val,
                kpi=kpi,
                general=gen,
                gitlab_username=gitlab_user,
                team_name=team_name_val,
            )
        )

    if parse_errors and not rows:
        raise ExcelParseError(parse_errors)

    log.info(
        "tl_excel_parsed",
        valid_rows=len(rows),
        warning_count=len(parse_errors),
    )

    team_display_name = rows[0].team_name if rows and rows[0].team_name else ""

    return ParseResult(
        rows=rows,
        errors=parse_errors,
        col_names=col_map,
        team_display_name=team_display_name,
    )
