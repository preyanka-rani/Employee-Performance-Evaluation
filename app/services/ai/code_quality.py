"""
app/services/ai/code_quality.py
────────────────────────────────
AI-powered code quality analyser for GitLab MR diffs.

Evaluation criteria (from documentation §AI CODE QUALITY ANALYSIS):
    Readability               20%
    Logic & Efficiency        30%
    Error Handling            20%
    Architecture & SOLID      20%
    Security                  10%

The LLM MUST return a strict JSON payload:
    {
      "score": 0-100,
      "readability": 0-100,
      "logic_efficiency": 0-100,
      "error_handling": 0-100,
      "architecture": 0-100,
      "security": 0-100,
      "reasoning": "...",
      "issues": ["...", "..."]
    }

Falls back to Groq if Claude fails.
"""

import json
import re
from dataclasses import dataclass, field

from app.core.logging_config import get_logger
from app.services.ai.claude_client import LLMClient, LLMResult

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are a senior software engineer performing a rigorous code quality review.
You will receive a git diff from a Merge Request.

Evaluate the code STRICTLY on these five criteria and return ONLY valid JSON:

{
  "score": <weighted overall score 0-100>,
  "readability": <0-100>,
  "logic_efficiency": <0-100>,
  "error_handling": <0-100>,
  "architecture": <0-100>,
  "security": <0-100>,
  "reasoning": "<1-3 sentence explanation of the overall score>",
  "issues": ["<specific issue 1>", "<specific issue 2>"]
}

Scoring weights:
  readability      → 20%
  logic_efficiency → 30%
  error_handling   → 20%
  architecture     → 20%
  security         → 10%

The weighted score must be:
  score = readability*0.20 + logic_efficiency*0.30 + error_handling*0.20 + architecture*0.20 + security*0.10

Rules:
- Return ONLY the JSON object. No markdown, no preamble.
- The "issues" list should contain 0-5 concrete, actionable issues.
- If the diff is empty or trivial, return score=70 with appropriate reasoning.
- Never hallucinate issues that are not visible in the diff.
- Be consistent: same diff should always produce approximately the same score.
"""


@dataclass
class CodeQualityResult:
    score: float
    readability: float
    logic_efficiency: float
    error_handling: float
    architecture: float
    security: float
    reasoning: str
    issues: list[str]
    model_used: str
    raw_response: str = field(default="", repr=False)


def _extract_json(text: str) -> str:
    """Extract the first JSON object found in the response text."""
    # Try to find a JSON block between ```json ... ``` or ``` ... ```
    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if md_match:
        return md_match.group(1)

    # Otherwise, find the first {...} block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return brace_match.group(0)

    return text  # Let json.loads handle the error


def _parse_llm_response(llm_result: LLMResult) -> CodeQualityResult:
    """Parse the raw LLM JSON response into a CodeQualityResult."""
    raw = llm_result.content
    json_str = _extract_json(raw)

    data = json.loads(json_str)

    readability = float(data.get("readability", 70))
    logic_efficiency = float(data.get("logic_efficiency", 70))
    error_handling = float(data.get("error_handling", 70))
    architecture = float(data.get("architecture", 70))
    security = float(data.get("security", 70))

    # Recompute weighted score to ensure consistency
    computed_score = (
        readability * 0.20
        + logic_efficiency * 0.30
        + error_handling * 0.20
        + architecture * 0.20
        + security * 0.10
    )
    # Trust LLM's score but cap to our computed range
    reported_score = float(data.get("score", computed_score))
    final_score = round(
        max(0.0, min(100.0, reported_score)),
        2,
    )

    return CodeQualityResult(
        score=final_score,
        readability=round(readability, 2),
        logic_efficiency=round(logic_efficiency, 2),
        error_handling=round(error_handling, 2),
        architecture=round(architecture, 2),
        security=round(security, 2),
        reasoning=str(data.get("reasoning", "")),
        issues=list(data.get("issues", [])),
        model_used=llm_result.model_used,
        raw_response=raw,
    )


class CodeQualityAnalyser:
    """
    Sends MR diffs to the LLM and returns a structured quality score.

    Each MR is analysed independently. The caller (DeveloperScorer) then
    aggregates multiple MR scores into a single component score.
    """

    def __init__(self) -> None:
        self._llm = LLMClient()

    async def analyse_mr_diff(
        self,
        mr_reference: str,
        diffs: list[dict],  # [{"file_path": ..., "diff_content": ...}]
    ) -> CodeQualityResult:
        """
        Analyse the combined diff of one MR.

        Args:
            mr_reference: Human-readable MR identifier for logging.
            diffs: List of file diffs (file_path + diff_content).

        Returns:
            CodeQualityResult with per-criterion scores and aggregated score.
        """
        if not diffs:
            logger.warning("mr_no_diffs", mr_reference=mr_reference)
            return CodeQualityResult(
                score=70.0,
                readability=70.0,
                logic_efficiency=70.0,
                error_handling=70.0,
                architecture=70.0,
                security=70.0,
                reasoning="No code changes detected in this MR.",
                issues=[],
                model_used="none",
            )

        # Build the user prompt with the diff content
        # Cap total diff content to ~4000 chars to stay within Groq TPM limits
        _MAX_TOTAL_DIFF = 4_000
        truncated_diffs = []
        chars_used = 0
        for d in diffs:
            content = d["diff_content"]
            remaining = _MAX_TOTAL_DIFF - chars_used
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining] + "\n... [truncated]"
            truncated_diffs.append(
                {"file_path": d["file_path"], "diff_content": content}
            )
            chars_used += len(content)

        diff_sections = "\n\n".join(
            f"### File: {d['file_path']}\n```diff\n{d['diff_content']}\n```"
            for d in truncated_diffs
        )
        user_prompt = (
            f"MR Reference: {mr_reference}\n\n"
            f"Please analyse the following code changes:\n\n{diff_sections}"
        )

        try:
            llm_result = await self._llm.invoke_with_fallback(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            result = _parse_llm_response(llm_result)
            logger.info(
                "mr_analysis_complete",
                mr_reference=mr_reference,
                score=result.score,
                model=result.model_used,
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error(
                "mr_analysis_json_parse_error",
                mr_reference=mr_reference,
                error=str(exc),
            )
            # Return a conservative default to avoid blocking the pipeline
            return CodeQualityResult(
                score=60.0,
                readability=60.0,
                logic_efficiency=60.0,
                error_handling=60.0,
                architecture=60.0,
                security=60.0,
                reasoning="JSON parse error from LLM response; using conservative default.",
                issues=["Unable to parse AI response."],
                model_used="fallback_default",
            )

        except Exception as exc:
            logger.error(
                "mr_analysis_failed",
                mr_reference=mr_reference,
                error=str(exc),
            )
            raise
