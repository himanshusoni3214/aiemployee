"""job terminal states

Revision ID: 0005_job_terminal_states
Revises: 0004_employee_archived_status
Create Date: 2026-06-27
"""
from alembic import op

revision = '0005_job_terminal_states'
down_revision = '0004_employee_archived_status'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'blocked'")
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'cancelled'")
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'skipped'")


def downgrade():
    pass
