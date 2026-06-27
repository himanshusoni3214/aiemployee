"""employee archived status

Revision ID: 0004_employee_archived_status
Revises: 0003_control_center_reporting
Create Date: 2026-06-27
"""
from alembic import op

revision = '0004_employee_archived_status'
down_revision = '0003_control_center_reporting'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE employeestatus ADD VALUE IF NOT EXISTS 'archived'")


def downgrade():
    pass
