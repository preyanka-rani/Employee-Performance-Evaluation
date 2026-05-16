"""Add Component 1 sub-score columns to final_scores

Adds five new columns that store the individual sub-components used to build
the weighted Component 1 score:
  - resolution_rate       (float, default 0.0)
  - reopen_quality_score  (float, default 0.0)
  - lines_added_score     (float, default 0.0)
  - lines_deleted_score   (float, default 0.0)
  - component1_score      (float, default 0.0)

Revision ID: 0002_component1_sub_scores
Revises: 0001_initial
Create Date: 2026-05-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_component1_sub_scores"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "final_scores",
        sa.Column("resolution_rate", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "final_scores",
        sa.Column(
            "reopen_quality_score", sa.Float(), nullable=False, server_default="0.0"
        ),
    )
    op.add_column(
        "final_scores",
        sa.Column(
            "lines_added_score", sa.Float(), nullable=False, server_default="0.0"
        ),
    )
    op.add_column(
        "final_scores",
        sa.Column(
            "lines_deleted_score", sa.Float(), nullable=False, server_default="0.0"
        ),
    )
    op.add_column(
        "final_scores",
        sa.Column("component1_score", sa.Float(), nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("final_scores", "component1_score")
    op.drop_column("final_scores", "lines_deleted_score")
    op.drop_column("final_scores", "lines_added_score")
    op.drop_column("final_scores", "reopen_quality_score")
    op.drop_column("final_scores", "resolution_rate")
