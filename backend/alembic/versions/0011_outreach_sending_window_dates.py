"""outreach sending window dates

Revision ID: 0011_outreach_window
Revises: 0010_model_policy_controls
Create Date: 2026-07-11 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_outreach_window'
down_revision = '0010_model_policy_controls'
branch_labels = None
depends_on = None


def _add_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {item['name'] for item in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    _add_if_missing('company_outreach_settings', sa.Column('allowed_sending_start_date', sa.String(), nullable=True))
    _add_if_missing('company_outreach_settings', sa.Column('allowed_sending_end_date', sa.String(), nullable=True))


def downgrade():
    op.drop_column('company_outreach_settings', 'allowed_sending_end_date')
    op.drop_column('company_outreach_settings', 'allowed_sending_start_date')
