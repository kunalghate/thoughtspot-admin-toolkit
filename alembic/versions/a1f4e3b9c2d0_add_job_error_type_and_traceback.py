"""add jobs.error_type and jobs.error_traceback

Revision ID: a1f4e3b9c2d0
Revises: ee1d89caf099
Create Date: 2026-04-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'a1f4e3b9c2d0'
down_revision: Union[str, None] = 'ee1d89caf099'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('error_type', sqlmodel.AutoString(), nullable=True))
    op.add_column('jobs', sa.Column('error_traceback', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'error_traceback')
    op.drop_column('jobs', 'error_type')
