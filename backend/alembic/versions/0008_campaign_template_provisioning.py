"""campaign template provisioning metadata

Revision ID: 0008_template_provisioning
Revises: 0007_employee_scheduled_status
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_template_provisioning'
down_revision = '0007_employee_scheduled_status'
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column['name'] == column_name for column in inspector.get_columns(table_name))


def upgrade():
    bind = op.get_bind()
    if not _has_column(bind, 'campaigns', 'campaign_type'):
        op.add_column('campaigns', sa.Column('campaign_type', sa.String(), nullable=False, server_default='custom'))
    if not _has_column(bind, 'campaigns', 'provisioning_state'):
        op.add_column('campaigns', sa.Column('provisioning_state', sa.String(), nullable=False, server_default='Draft'))
    if not _has_column(bind, 'campaigns', 'provisioning_result'):
        op.add_column('campaigns', sa.Column('provisioning_result', sa.JSON(), nullable=False, server_default='{}'))
    try:
        op.create_index('ix_campaigns_campaign_type', 'campaigns', ['campaign_type'])
    except Exception:
        pass
    try:
        op.create_index('ix_campaigns_provisioning_state', 'campaigns', ['provisioning_state'])
    except Exception:
        pass


def downgrade():
    try:
        op.drop_index('ix_campaigns_provisioning_state', table_name='campaigns')
    except Exception:
        pass
    try:
        op.drop_index('ix_campaigns_campaign_type', table_name='campaigns')
    except Exception:
        pass
    bind = op.get_bind()
    if _has_column(bind, 'campaigns', 'provisioning_result'):
        op.drop_column('campaigns', 'provisioning_result')
    if _has_column(bind, 'campaigns', 'provisioning_state'):
        op.drop_column('campaigns', 'provisioning_state')
    if _has_column(bind, 'campaigns', 'campaign_type'):
        op.drop_column('campaigns', 'campaign_type')
