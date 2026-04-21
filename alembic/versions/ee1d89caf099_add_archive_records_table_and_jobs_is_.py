"""add archive_records table and jobs.is_cancelled

Revision ID: ee1d89caf099
Revises: 
Create Date: 2026-04-13 11:13:17.248298

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'ee1d89caf099'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # jobs.is_cancelled — cancel flag for running archive delete jobs
    op.add_column('jobs', sa.Column('is_cancelled', sa.Boolean(), nullable=False, server_default='0'))

    # archive_records — one row per deleted object; TML backup manifest + restore history
    op.create_table(
        'archive_records',
        sa.Column('id', sqlmodel.AutoString(), primary_key=True),
        sa.Column('cluster_id', sqlmodel.AutoString(), nullable=False),
        sa.Column('job_id', sqlmodel.AutoString(), nullable=False),
        sa.Column('ts_guid', sqlmodel.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.AutoString(), nullable=False),
        sa.Column('object_type', sqlmodel.AutoString(), nullable=False),
        sa.Column('owner_guid', sqlmodel.AutoString(), nullable=False),
        sa.Column('owner_name', sqlmodel.AutoString(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_accessed_at', sa.DateTime(), nullable=True),
        sa.Column('days_unused', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tags', sqlmodel.AutoString(), nullable=False, server_default='[]'),
        sa.Column('tml_path', sqlmodel.AutoString(), nullable=True),
        sa.Column('tml_export_status', sqlmodel.AutoString(), nullable=False, server_default='PENDING'),
        sa.Column('tml_export_error', sqlmodel.AutoString(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=False),
        sa.Column('restored_at', sa.DateTime(), nullable=True),
        sa.Column('restored_as_guid', sqlmodel.AutoString(), nullable=True),
        sa.Column('restored_by_job_id', sqlmodel.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id']),
    )
    op.create_index('ix_archive_records_cluster_id', 'archive_records', ['cluster_id'])
    op.create_index('ix_archive_records_job_id', 'archive_records', ['job_id'])
    op.create_index('ix_archive_records_ts_guid', 'archive_records', ['ts_guid'])


def downgrade() -> None:
    op.drop_index('ix_archive_records_ts_guid', 'archive_records')
    op.drop_index('ix_archive_records_job_id', 'archive_records')
    op.drop_index('ix_archive_records_cluster_id', 'archive_records')
    op.drop_table('archive_records')
    op.drop_column('jobs', 'is_cancelled')
