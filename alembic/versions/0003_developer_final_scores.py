"""Create developer_final_scores table

Dedicated per-team score table for developers. Stores every sub-component
used in the developer evaluation formula so the Excel report can show the
full calculation breakdown without joins.

Revision ID: 0003_developer_final_scores
Revises: 0002_component1_sub_scores
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_developer_final_scores"
down_revision: Union[str, None] = "0002_component1_sub_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "developer_final_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        # Employee identity
        sa.Column("employee_id", sa.String(length=50), nullable=False),
        sa.Column(
            "employee_name", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        # Component 1 sub-scores
        sa.Column(
            "code_quality_score", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("resolution_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "reopen_quality_score", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column(
            "lines_added_score", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column(
            "lines_deleted_score", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("component1_score", sa.Float(), nullable=False, server_default="0.0"),
        # Component 2 sub-scores
        sa.Column("work_log_hours", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("work_log_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("sentiment_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("component2_score", sa.Float(), nullable=False, server_default="0.0"),
        # Segment A
        sa.Column("segment_a_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("segment_a_marks", sa.Float(), nullable=False, server_default="0.0"),
        # Segment B
        sa.Column("attendance_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("attendance_marks", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "tl_problem_solving", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("tl_kpi", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tl_general", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tl_total", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("segment_b_marks", sa.Float(), nullable=False, server_default="0.0"),
        # Final
        sa.Column("base_total", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("reward_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("final_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_developer_final_scores_evaluation_run_id",
        "developer_final_scores",
        ["evaluation_run_id"],
    )
    op.create_index(
        "ix_developer_final_scores_employee_id",
        "developer_final_scores",
        ["employee_id"],
    )
    op.create_index(
        "ix_developer_final_scores_employee_email",
        "developer_final_scores",
        ["employee_email"],
    )
    op.create_index(
        "ix_developer_final_scores_year_month",
        "developer_final_scores",
        ["year", "month"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_developer_final_scores_year_month", table_name="developer_final_scores"
    )
    op.drop_index(
        "ix_developer_final_scores_employee_email", table_name="developer_final_scores"
    )
    op.drop_index(
        "ix_developer_final_scores_employee_id", table_name="developer_final_scores"
    )
    op.drop_index(
        "ix_developer_final_scores_evaluation_run_id",
        table_name="developer_final_scores",
    )
    op.drop_table("developer_final_scores")
