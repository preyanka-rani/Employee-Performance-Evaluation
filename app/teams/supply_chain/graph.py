from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.repositories.score_repository import TLAssessmentRepository
from app.services.ai.sentiment import compute_employee_sentiment_score
from app.shared.data_sources.mysql_client import MySQLHRClient
from app.shared.data_sources.support_crm_client import SupportCRMClient
from app.teams.supply_chain.formulas import (
    compute_attendance_marks,
    compute_segment_a_marks,
    compute_segment_b_marks,
    compute_supply_chain_final_score,
    compute_supply_chain_functional_score,
    compute_tl_total,
)

logger = get_logger(__name__)


class SupplyChainWorkerState(TypedDict, total=False):
    employee_id: str
    employee_email: str
    employee_name: str
    year: int
    month: int
    run_id: int
    db: AsyncSession

    crm_log_records: list[dict]
    crm_description_records: list[dict]

    attendance_records: list[dict]

    tl_problem_solving: float
    tl_kpi: float
    tl_general: float

    total_hours: float
    log_hours_score: float
    sentiment_avg: float
    avg_polarity: float
    monthly_functional_score: float
    segment_a_marks: float

    attendance_score: float
    attendance_present: int
    attendance_late: int
    attendance_work_days: int
    attendance_marks: float
    tl_total: float
    segment_b_marks: float

    base_total: float
    final_score: float

    fetch_error: str | None


async def fetch_crm_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])
    log.info("supply_chain_fetch_crm_start")

    crm = SupportCRMClient()
    try:
        import asyncio
        log_records, descriptions = await asyncio.gather(
            crm.get_crm_log_hours(
                employee_ids=[state["employee_id"]],
                year=state["year"],
                month=state["month"],
            ),
            crm.get_crm_descriptions(
                employee_ids=[state["employee_id"]],
                year=state["year"],
                month=state["month"],
            ),
            return_exceptions=False,
        )
    except Exception as exc:
        log.error("crm_fetch_failed", error=str(exc))
        return {"crm_log_records": [], "crm_description_records": []}
    finally:
        await crm.close()

    log.info("supply_chain_fetch_crm_done", log_records=len(log_records), descriptions=len(descriptions))
    return {"crm_log_records": log_records, "crm_description_records": descriptions}


async def fetch_hr_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])
    log.info("supply_chain_fetch_hr_start")

    hr = MySQLHRClient()
    try:
        attendance_records = await hr.get_attendance(
            employee_ids=[state["employee_id"]],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        log.error("hr_fetch_failed", error=str(exc))
        return {"attendance_records": []}
    finally:
        await hr.close()

    log.info("supply_chain_fetch_hr_done", attendance_records=len(attendance_records))
    return {"attendance_records": attendance_records}


async def load_tl_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])
    db: AsyncSession = state["db"]

    tl_repo = TLAssessmentRepository(db)
    tl_score = await tl_repo.get_for_employee_period(
        employee_email=state["employee_email"],
        year=state["year"],
        month=state["month"],
        evaluation_run_id=state["run_id"],
    )

    if tl_score:
        problem_solving = float(tl_score.problem_solving)
        kpi = float(tl_score.kpi)
        general = float(tl_score.general)
    else:
        log.warning("tl_score_not_found_using_defaults")
        problem_solving = 5.0
        kpi = 7.5
        general = 7.5

    log.info("tl_loaded", problem_solving=problem_solving, kpi=kpi, general=general)
    return {
        "tl_problem_solving": problem_solving,
        "tl_kpi": kpi,
        "tl_general": general,
    }


async def compute_functional_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])

    total_hours = sum(
        float(row.get("total_hours", 0))
        for row in state["crm_log_records"]
        if str(row.get("employee_id", "")) == state["employee_id"]
    )
    log_hours_score, monthly_functional_score = compute_supply_chain_functional_score(
        log_hours=total_hours,
        sentiment_score=0.0,
    )

    desc_texts = [
        str(row.get("description", ""))
        for row in state["crm_description_records"]
        if str(row.get("employee_id", "")) == state["employee_id"]
    ]
    sentiment_avg, avg_polarity = compute_employee_sentiment_score(desc_texts)

    log_hours_score, monthly_functional_score = compute_supply_chain_functional_score(
        log_hours=total_hours,
        sentiment_score=sentiment_avg,
    )
    segment_a_marks = compute_segment_a_marks(monthly_functional_score)

    log.info(
        "supply_chain_functional_computed",
        total_hours=total_hours,
        log_hours_score=log_hours_score,
        sentiment_score=sentiment_avg,
        monthly_functional_score=monthly_functional_score,
        segment_a_marks=segment_a_marks,
    )
    return {
        "total_hours": total_hours,
        "log_hours_score": log_hours_score,
        "sentiment_avg": sentiment_avg,
        "avg_polarity": avg_polarity,
        "monthly_functional_score": monthly_functional_score,
        "segment_a_marks": segment_a_marks,
    }


async def compute_segment_b_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])

    att_row = next(
        (
            r
            for r in state["attendance_records"]
            if str(r.get("employee_id", "")) == state["employee_id"]
        ),
        None,
    )

    if att_row:
        attendance_score = float(att_row.get("attendance_score", 60.0))
        present = int(att_row.get("present", 0))
        late = int(att_row.get("late", 0))
        actual_work_days = int(att_row.get("actual_work_days", 22))
    else:
        attendance_score = 60.0
        present = 0
        late = 0
        actual_work_days = 22

    attendance_marks = compute_attendance_marks(attendance_score)
    tl_total = compute_tl_total(
        problem_solving=state["tl_problem_solving"],
        kpi=state["tl_kpi"],
        general=state["tl_general"],
    )
    segment_b_marks = compute_segment_b_marks(attendance_marks, tl_total)

    log.info(
        "supply_chain_segment_b_computed",
        attendance_score=attendance_score,
        attendance_marks=attendance_marks,
        tl_total=tl_total,
        segment_b_marks=segment_b_marks,
    )
    return {
        "attendance_score": attendance_score,
        "attendance_present": present,
        "attendance_late": late,
        "attendance_work_days": actual_work_days,
        "attendance_marks": attendance_marks,
        "tl_total": tl_total,
        "segment_b_marks": segment_b_marks,
    }


async def compute_final_node(state: SupplyChainWorkerState) -> dict:
    log = logger.bind(employee_email=state["employee_email"])

    base_total, final_score = compute_supply_chain_final_score(
        segment_a_marks=state["segment_a_marks"],
        segment_b_marks=state["segment_b_marks"],
    )

    log.info("supply_chain_final_computed", base_total=base_total, final_score=final_score)
    return {"base_total": base_total, "final_score": final_score}


def build_supply_chain_graph() -> Any:
    builder = StateGraph(SupplyChainWorkerState)
    builder.add_node("fetch_crm", fetch_crm_node)
    builder.add_node("fetch_hr", fetch_hr_node)
    builder.add_node("load_tl", load_tl_node)
    builder.add_node("compute_functional", compute_functional_node)
    builder.add_node("compute_segment_b", compute_segment_b_node)
    builder.add_node("compute_final", compute_final_node)

    builder.add_edge(START, "fetch_crm")
    builder.add_edge("fetch_crm", "fetch_hr")
    builder.add_edge("fetch_hr", "load_tl")
    builder.add_edge("load_tl", "compute_functional")
    builder.add_edge("compute_functional", "compute_segment_b")
    builder.add_edge("compute_segment_b", "compute_final")
    builder.add_edge("compute_final", END)

    return builder.compile()


_supply_chain_graph = build_supply_chain_graph()


async def run_supply_chain_worker(
    *,
    employee_id: str,
    employee_email: str,
    employee_name: str,
    evaluation_run_id: int,
    year: int,
    month: int,
    db: AsyncSession,
) -> SupplyChainWorkerState:
    initial_state: SupplyChainWorkerState = {
        "employee_id": employee_id,
        "employee_email": employee_email,
        "employee_name": employee_name,
        "year": year,
        "month": month,
        "run_id": evaluation_run_id,
        "db": db,
        "crm_log_records": [],
        "crm_description_records": [],
        "attendance_records": [],
        "tl_problem_solving": 5.0,
        "tl_kpi": 7.5,
        "tl_general": 7.5,
        "total_hours": 0.0,
        "log_hours_score": 0.0,
        "sentiment_avg": 60.0,
        "avg_polarity": 0.0,
        "monthly_functional_score": 0.0,
        "segment_a_marks": 0.0,
        "attendance_score": 60.0,
        "attendance_present": 0,
        "attendance_late": 0,
        "attendance_work_days": 22,
        "attendance_marks": 6.0,
        "tl_total": 20.0,
        "segment_b_marks": 26.0,
        "base_total": 0.0,
        "final_score": 0.0,
        "fetch_error": None,
    }
    final_state: SupplyChainWorkerState = await _supply_chain_graph.ainvoke(initial_state)
    return final_state
