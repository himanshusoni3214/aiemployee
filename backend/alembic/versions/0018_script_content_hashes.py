"""Add exact Script Studio content hashes.

Revision ID: 0018_script_content_hashes
Revises: 0017_script_studio_recovery
"""

from alembic import op
import sqlalchemy as sa


revision = '0018_script_content_hashes'
down_revision = '0017_script_studio_recovery'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call_script_versions', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.add_column('call_script_versions', sa.Column('tested_content_hash', sa.String(length=64), nullable=True))
    op.add_column('call_script_versions', sa.Column('approved_content_hash', sa.String(length=64), nullable=True))
    op.add_column('call_script_versions', sa.Column('published_content_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_call_script_versions_content_hash', 'call_script_versions', ['content_hash'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_call_script_versions_content_hash', table_name='call_script_versions')
    op.drop_column('call_script_versions', 'published_content_hash')
    op.drop_column('call_script_versions', 'approved_content_hash')
    op.drop_column('call_script_versions', 'tested_content_hash')
    op.drop_column('call_script_versions', 'content_hash')
