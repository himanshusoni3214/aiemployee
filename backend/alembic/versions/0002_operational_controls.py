"""operational controls

Revision ID: 0002_operational_controls
Revises: 0001_initial
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_operational_controls'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column['name'] for column in inspector.get_columns(table_name)}

def _add_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)

def _drop_if_present(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)

def upgrade():
    _add_if_missing('ai_employees', sa.Column('rate_limit_per_hour', sa.Integer(), nullable=False, server_default='20'))
    _add_if_missing('ai_employees', sa.Column('daily_email_limit', sa.Integer(), nullable=False, server_default='50'))
    _add_if_missing('ai_employees', sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'))
    _add_if_missing('ai_employees', sa.Column('circuit_breaker_open', sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_if_missing('ai_employees', sa.Column('paused_reason', sa.Text(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('last_error', sa.Text(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True))

    _add_if_missing('jobs', sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
    _add_if_missing('jobs', sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='1'))
    _add_if_missing('jobs', sa.Column('retry_after', sa.DateTime(), nullable=True))
    _add_if_missing('jobs', sa.Column('duration_seconds', sa.Integer(), nullable=True))

    _add_if_missing('schedules', sa.Column('last_run_at', sa.DateTime(), nullable=True))
    _add_if_missing('schedules', sa.Column('next_run_at', sa.DateTime(), nullable=True))
    _add_if_missing('leads', sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))

def downgrade():
    _drop_if_present('leads', 'created_at')
    for column_name in ('next_run_at', 'last_run_at'):
        _drop_if_present('schedules', column_name)
    for column_name in ('duration_seconds', 'retry_after', 'max_attempts', 'attempts'):
        _drop_if_present('jobs', column_name)
    for column_name in ('last_heartbeat_at', 'last_error', 'paused_reason', 'circuit_breaker_open', 'failure_count', 'daily_email_limit', 'rate_limit_per_hour'):
        _drop_if_present('ai_employees', column_name)
