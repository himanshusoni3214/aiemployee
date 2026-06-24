"""initial
Revision ID: 0001_initial
Revises:
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa
revision='0001_initial'; down_revision=None; branch_labels=None; depends_on=None

def upgrade():
    import app.models.entities as e
    from app.models.base import Base
    bind = op.get_bind(); Base.metadata.create_all(bind)
def downgrade():
    import app.models.entities as e
    from app.models.base import Base
    bind = op.get_bind(); Base.metadata.drop_all(bind)
