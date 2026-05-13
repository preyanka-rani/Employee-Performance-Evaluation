"""Initial schema: all tables

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # employees
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("team", sa.String(length=100), nullable=False),
        sa.Column("gitlab_username", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_employees_team", "employees", ["team"])

    # evaluation_runs
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(length=100), nullable=False),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="pending"
        ),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_runs_team", "evaluation_runs", ["team"])
    op.create_index(
        "ix_evaluation_runs_year_month", "evaluation_runs", ["year", "month"]
    )

    # code_quality_scores
    op.create_table(
        "code_quality_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("mr_reference", sa.String(length=255), nullable=False),
        sa.Column("mr_title", sa.String(length=500), nullable=True),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("readability_score", sa.Float(), nullable=True),
        sa.Column("logic_efficiency_score", sa.Float(), nullable=True),
        sa.Column("error_handling_score", sa.Float(), nullable=True),
        sa.Column("architecture_score", sa.Float(), nullable=True),
        sa.Column("security_score", sa.Float(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("issues", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cqs_run", "code_quality_scores", ["evaluation_run_id"])
    op.create_index("ix_cqs_email", "code_quality_scores", ["employee_email"])

    # attendance_scores
    op.create_table(
        "attendance_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("present_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("late_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "actual_work_days", sa.Integer(), nullable=False, server_default="22"
        ),
        sa.Column("attendance_score", sa.Float(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_att_run_email", "attendance_scores", ["evaluation_run_id", "employee_email"]
    )

    # sentiment_scores
    op.create_table(
        "sentiment_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("average_sentiment_score", sa.Float(), nullable=False),
        sa.Column("average_polarity", sa.Float(), nullable=True),
        sa.Column(
            "description_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sent_run_email", "sentiment_scores", ["evaluation_run_id", "employee_email"]
    )

    # work_log_scores
    op.create_table(
        "work_log_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("total_hours", sa.Float(), nullable=False),
        sa.Column("normalized_score", sa.Float(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wl_run_email", "work_log_scores", ["evaluation_run_id", "employee_email"]
    )

    # tl_assessment_scores
    op.create_table(
        "tl_assessment_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("problem_solving", sa.Float(), nullable=False, server_default="0"),
        sa.Column("kpi", sa.Float(), nullable=False, server_default="0"),
        sa.Column("general", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "uploaded_by",
            sa.String(length=255),
            nullable=False,
            server_default="system",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tl_run_email",
        "tl_assessment_scores",
        ["evaluation_run_id", "employee_email"],
    )
    op.create_index(
        "ix_tl_email_period",
        "tl_assessment_scores",
        ["employee_email", "year", "month"],
    )

    # final_scores
    op.create_table(
        "final_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.Integer(), nullable=False),
        sa.Column("employee_email", sa.String(length=255), nullable=False),
        sa.Column("quality_check_score", sa.Float(), nullable=False),
        sa.Column("work_log_score", sa.Float(), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=False),
        sa.Column("attendance_score", sa.Float(), nullable=False),
        sa.Column("problem_solving_score", sa.Float(), nullable=False),
        sa.Column("kpi_score", sa.Float(), nullable=False),
        sa.Column("general_score", sa.Float(), nullable=False),
        sa.Column("segment_a_marks", sa.Float(), nullable=False),
        sa.Column("segment_b_marks", sa.Float(), nullable=False),
        sa.Column("base_total", sa.Float(), nullable=False),
        sa.Column("reward_score", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fs_email_period", "final_scores", ["employee_email", "year", "month"]
    )
    op.create_index("ix_fs_run", "final_scores", ["evaluation_run_id"])


def downgrade() -> None:
    op.drop_table("final_scores")
    op.drop_table("tl_assessment_scores")
    op.drop_table("work_log_scores")
    op.drop_table("sentiment_scores")
    op.drop_table("attendance_scores")
    op.drop_table("code_quality_scores")
    op.drop_table("evaluation_runs")
    op.drop_table("employees")
