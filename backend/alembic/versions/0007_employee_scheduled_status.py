"""employee scheduled status

Revision ID: 0007_employee_scheduled_status
Revises: 0006_job_delivery_evidence
Create Date: 2026-06-29
"""
from alembic import op

revision = '0007_employee_scheduled_status'
down_revision = '0006_job_delivery_evidence'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE employeestatus ADD VALUE IF NOT EXISTS 'scheduled'")


def downgrade():
    pass
