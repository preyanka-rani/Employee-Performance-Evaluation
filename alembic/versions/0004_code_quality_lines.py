"""Add lines_added and lines_deleted to code_quality_scores

Stores the approximate count of lines added and deleted per project bundle
(counted from unified diff content).  These are used in the CodeQuality
Excel report to give reviewers a quick sense of code volume.

Revision ID: 0004_code_quality_lines
Revises: 0003_developer_final_scores
Create Date: 2026-05-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_code_quality_lines"
down_revision: Union[str, None] = "0003_developer_final_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "code_quality_scores",
        sa.Column("lines_added", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "code_quality_scores",
        sa.Column("lines_deleted", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("code_quality_scores", "lines_deleted")
    op.drop_column("code_quality_scores", "lines_added")
