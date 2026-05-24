"""
app/services/support_teams/excel_parser/parser.py
──────────────────────────────────────────────────
Dynamic Excel parser for Support Team TL Assessment uploads.

Parsing strategy (three-stage, most-to-least specific):

  Stage 1 – Static alias matching
    Normalise every column header (lowercase, strip, replace special chars
    with underscores) and attempt to map it to a canonical field using an
    extensive alias dictionary.  Fast path; no network calls.

  Stage 2 – AI-powered column mapping (fallback)
    If Stage 1 misses one or more required fields, the raw headers plus up
    to three sample rows are sent to the LLM (Claude → Groq fallback).  The
    model returns a JSON map of {raw_header: canonical_field}.

  Stage 3 – Heuristic email detection (last resort)
    If the email column is still unresolved, scan every column and pick the
    first one whose non-null values mostly look like email addresses.

Required canonical fields:
    employee_email    – work email (must contain "@")
    support_readiness – 0-10  (Support Readiness & Issue Handling)
    kpi               – 0-15  (KPI / Performance Agreement)
    general           – 0-15  (General Leadership Assessment)

Optional canonical fields (parsed when available):
    employee_name  – full name
    employee_id    – internal ID (numeric or alphanumeric)

Returns:
    SupportTLParseResult with validated rows and per-row warning messages.
Raises:
    SupportExcelParseError on unrecoverable structural failures.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ── Exception ─────────────────────────────────────────────────────────────────


class SupportExcelParseError(Exception):
    """Raised when the uploaded Excel cannot be parsed or fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class SupportTLRow:
    """Validated TL assessment row for one support team employee."""

    employee_email: str
    support_readiness: float  # 0-10
    kpi: float  # 0-15
    general: float  # 0-15
    employee_id: str = ""  # optional – resolved from MySQL by the endpoint if blank
    employee_name: str = ""  # optional – used for Employee upsert
    team_name: str = ""  # raw team name value from input Excel, if column exists


@dataclass
class SupportTLParseResult:
    rows: list[SupportTLRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    col_names: dict[str, str] = field(default_factory=dict)
    # Maps canonical field → original header string from the uploaded Excel, e.g.
    # {"support_readiness": "Support Readiness & Issue Handling (0-10)",
    #  "kpi": "KPI Agreement (0-15)", "general": "Leadership General Assessment (0-15)",
    #  "team_name": "Team"}


# ── Score bounds ──────────────────────────────────────────────────────────────

_BOUNDS: dict[str, tuple[float, float]] = {
    "support_readiness": (0.0, 10.0),
    "kpi": (0.0, 15.0),
    "general": (0.0, 15.0),
}

_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"employee_email", "support_readiness", "kpi", "general"}
)

# ── Static alias dictionary ───────────────────────────────────────────────────
# Keys   = normalised alias (lowercase, underscores, no special chars).
# Values = canonical field name.
# Add new rows here as real-world file variants are discovered.

_ALIAS_MAP: dict[str, str] = {
    # ── employee_email ────────────────────────────────────────────────────────
    "employee_email": "employee_email",
    "email": "employee_email",
    "email_address": "employee_email",
    "work_email": "employee_email",
    "official_email": "employee_email",
    "company_email": "employee_email",
    "emp_email": "employee_email",
    # ── support_readiness (0-10) ──────────────────────────────────────────────
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
    # ── kpi (0-15) ────────────────────────────────────────────────────────────
    "kpi": "kpi",
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
    # ── general (0-15) ────────────────────────────────────────────────────────
    "general": "general",
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
    # ── employee_name (optional) ──────────────────────────────────────────────
    "name": "employee_name",
    "full_name": "employee_name",
    "employee_name": "employee_name",
    "employee": "employee_name",
    "resource_name": "employee_name",
    "staff_name": "employee_name",
    # ── employee_id (optional) ────────────────────────────────────────────────
    "employee_id": "employee_id",
    "emp_id": "employee_id",
    "id": "employee_id",
    "staff_id": "employee_id",
    "personnel_id": "employee_id",
    # ── team_name (optional) ──────────────────────────────────────────────────
    "team": "team_name",
    "team_name": "team_name",
    "team_names": "team_name",
    "team_label": "team_name",
    "department": "team_name",
    "dept": "team_name",
    "division": "team_name",
    "group_name": "team_name",
}


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
        raise SupportExcelParseError([f"Cannot read Excel file: {exc}"]) from exc

    if df_raw.empty or df_raw.shape[1] == 0:
        raise SupportExcelParseError(["Excel file appears to be empty."])

    header_row_idx = _find_header_row(df_raw)
    logger.info("header_row_detected", row_index=header_row_idx)

    # Pass 2: read again with the correct header row
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), header=header_row_idx, dtype=str)
    except Exception as exc:
        raise SupportExcelParseError(
            [f"Cannot re-read Excel with detected header row {header_row_idx}: {exc}"]
        ) from exc

    # Drop columns that are entirely NaN (e.g. trailing empty columns)
    df = df.dropna(axis=1, how="all")

    return df, df.columns.tolist()


# ── Sample rows helper ────────────────────────────────────────────────────────


def _get_sample_rows(df: pd.DataFrame, n: int = 3) -> list[list[Any]]:
    """Return up to *n* non-all-null rows as list-of-lists for AI context."""
    samples: list[list[Any]] = []
    for _, row in df.iterrows():
        if not row.isnull().all():
            samples.append(row.tolist())
        if len(samples) >= n:
            break
    return samples


# ── Sample rows helper ────────────────────────────────────────────────────────


# ── Core async parser ─────────────────────────────────────────────────────────


async def parse_support_tl_excel(file_bytes: bytes) -> SupportTLParseResult:
    """
    Parse a Support Team TL Assessment Excel upload dynamically.

    The function is **async** because Stage 2 may call the LLM API.

    Args:
        file_bytes: Raw bytes of the uploaded .xlsx/.xls file.

    Returns:
        SupportTLParseResult with valid rows and per-row warning strings.

    Raises:
        SupportExcelParseError: If the file cannot be read or required columns
            cannot be resolved even after AI assistance.
    """
    # ── Load file (with automatic header-row detection) ───────────────────────
    df, raw_headers = _load_with_header_detection(file_bytes)

    # ── Stage 1: static alias matching ───────────────────────────────────────
    col_map: dict[str, str] = _build_column_map_static(raw_headers)
    missing: set[str] = _REQUIRED_KEYS - col_map.keys()

    logger.info(
        "static_mapping_result",
        resolved=list(col_map.keys()),
        missing=sorted(missing),
    )

    # ── Stage 2: AI fallback ──────────────────────────────────────────────────
    if missing:
        logger.info(
            "attempting_ai_column_mapping",
            missing_fields=sorted(missing),
        )
        try:
            from app.services.support_teams.excel_parser.ai_mapper import (  # noqa: PLC0415
                map_columns_with_ai,
            )

            sample_rows = _get_sample_rows(df)
            ai_mapping: dict[str, str] = await map_columns_with_ai(
                raw_headers, sample_rows
            )
            # ai_mapping: {raw_header → canonical_field}
            for raw_col, canonical in ai_mapping.items():
                if canonical not in col_map:  # don't override static matches
                    col_map[canonical] = raw_col
        except Exception as exc:
            logger.warning("ai_mapping_skipped", reason=str(exc))

        missing = _REQUIRED_KEYS - col_map.keys()

    # ── Stage 3: heuristic email detection ───────────────────────────────────
    if "employee_email" in missing:
        detected = _detect_email_column(df)
        if detected:
            col_map["employee_email"] = detected
            missing.discard("employee_email")
            logger.info("email_column_detected_heuristically", column=detected)

    # ── Fatal: still missing required columns ─────────────────────────────────
    if missing:
        raise SupportExcelParseError(
            [
                f"Missing required columns after static + AI mapping: "
                f"{sorted(missing)}. "
                f"Detected headers: {raw_headers}"
            ]
        )

    # ── Parse rows ────────────────────────────────────────────────────────────
    parse_errors: list[str] = []
    rows: list[SupportTLRow] = []

    # Drop rows that are completely null across all required columns
    required_raw_cols = [col_map[f] for f in _REQUIRED_KEYS]
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
            readiness_raw = _cell("support_readiness", "0")
            kpi_raw = _cell("kpi", "0")
            general_raw = _cell("general", "0")

            sr = float(readiness_raw) if readiness_raw else 0.0
            kp = float(kpi_raw) if kpi_raw else 0.0
            gn = float(general_raw) if general_raw else 0.0
        except (ValueError, TypeError) as exc:
            parse_errors.append(f"Row {row_num}: numeric conversion error – {exc}")
            continue

        # Clamp to allowed bounds
        sr = min(
            max(sr, _BOUNDS["support_readiness"][0]), _BOUNDS["support_readiness"][1]
        )
        kp = min(max(kp, _BOUNDS["kpi"][0]), _BOUNDS["kpi"][1])
        gn = min(max(gn, _BOUNDS["general"][0]), _BOUNDS["general"][1])

        emp_id = _cell("employee_id")
        if emp_id in ("nan", "None"):
            emp_id = ""

        emp_name = _cell("employee_name")
        if emp_name in ("nan", "None"):
            emp_name = ""

        team_name_val = _cell("team_name", "")
        if team_name_val in ("nan", "None"):
            team_name_val = ""

        rows.append(
            SupportTLRow(
                employee_email=email,
                support_readiness=sr,
                kpi=kp,
                general=gn,
                employee_id=emp_id,
                employee_name=emp_name,
                team_name=team_name_val,
            )
        )

    if parse_errors and not rows:
        raise SupportExcelParseError(parse_errors)

    logger.info(
        "support_excel_parsed",
        valid_rows=len(rows),
        warning_count=len(parse_errors),
    )
    return SupportTLParseResult(rows=rows, errors=parse_errors, col_names=col_map)
    col_index: dict[str, int] = {}
    for idx, cell_value in enumerate(header_row):
        if cell_value is None:
            continue
        normalised = _COLUMN_MAP.get(str(cell_value).strip().lower())
        if normalised:
            col_index[normalised] = idx

    missing_cols = _REQUIRED_KEYS - col_index.keys()
    if missing_cols:
        raise SupportExcelParseError(
            [f"Missing required columns: {', '.join(sorted(missing_cols))}"]
        )

    # ── Parse data rows ───────────────────────────────────────────────────────
    for row_num, row in enumerate(
        sheet.iter_rows(min_row=2, values_only=True), start=2
    ):
        if all(cell is None for cell in row):
            continue

        def _get(key: str):
            return row[col_index[key]]

        raw_email = _get("employee_email")
        if not raw_email:
            errors.append(f"Row {row_num}: missing employee_email — skipped.")
            continue

        employee_email = str(raw_email).strip().lower()
        if "@" not in employee_email:
            errors.append(f"Row {row_num}: invalid email '{employee_email}' — skipped.")
            continue

        row_errors: list[str] = []
        scores: dict[str, float] = {}

        for field_name, (lo, hi) in _BOUNDS.items():
            raw_val = _get(field_name)
            try:
                val = float(raw_val if raw_val is not None else 0.0)
            except (TypeError, ValueError):
                row_errors.append(
                    f"Row {row_num} [{field_name}]: cannot parse '{raw_val}' as number."
                )
                val = 0.0

            if not (lo <= val <= hi):
                row_errors.append(
                    f"Row {row_num} [{field_name}]: {val} is outside allowed range [{lo}, {hi}]."
                )
                val = max(lo, min(hi, val))  # clamp and continue

            scores[field_name] = val

        errors.extend(row_errors)

        rows.append(
            SupportTLRow(
                employee_email=employee_email,
                support_readiness=scores["support_readiness"],
                kpi=scores["kpi"],
                general=scores["general"],
            )
        )

    logger.info(
        "support_tl_excel_parsed",
        valid_rows=len(rows),
        error_count=len(errors),
    )

    return SupportTLParseResult(rows=rows, errors=errors)
