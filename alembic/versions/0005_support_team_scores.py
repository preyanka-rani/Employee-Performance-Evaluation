"""Create support team score tables

Adds three tables for Support Team (Impl & ITS, Onsite Support,
Production, Tech Support) evaluation results:

    support_crm_log_scores  – CRM activity log metrics and scores
    support_ticket_scores   – Ticket volume + resolution speed scores
    support_final_scores    – Full score breakdown per employee per run

Revision ID: 0005_support_team_scores
Revises: 0004_code_quality_lines
Create Date: 2026-06-01 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_support_team_scores"
down_revision: Union[str, None] = "0004_code_quality_lines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── support_crm_log_scores ────────────────────────────────────────────────
    op.create_table(
        "support_crm_log_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("employee_email", sa.String(255), nullable=False, index=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("total_log_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "total_log_entries", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("log_hours_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sentiment_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "average_sentiment_polarity", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("crm_log_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── support_ticket_scores ─────────────────────────────────────────────────
    op.create_table(
        "support_ticket_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("employee_email", sa.String(255), nullable=False, index=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("total_tickets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_taken_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "monthly_tickets_score", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "monthly_ticket_resolved_score",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tickets_evaluation_score", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── support_final_scores ──────────────────────────────────────────────────
    op.create_table(
        "support_final_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("employee_email", sa.String(255), nullable=False, index=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        # CRM Log
        sa.Column("total_log_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("log_hours_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sentiment_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("crm_log_score", sa.Float(), nullable=False, server_default="0"),
        # Tickets
        sa.Column("total_tickets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_taken_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "tickets_evaluation_score", sa.Float(), nullable=False, server_default="0"
        ),
        # Segment A
        sa.Column(
            "monthly_functional_score", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("segment_a_marks", sa.Float(), nullable=False, server_default="0"),
        # Segment B
        sa.Column("attendance_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attendance_marks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("support_readiness", sa.Float(), nullable=False, server_default="0"),
        sa.Column("kpi", sa.Float(), nullable=False, server_default="0"),
        sa.Column("general", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tl_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("segment_b_marks", sa.Float(), nullable=False, server_default="0"),
        # Final
        sa.Column("base_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("final_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("support_final_scores")
    op.drop_table("support_ticket_scores")
    op.drop_table("support_crm_log_scores")
