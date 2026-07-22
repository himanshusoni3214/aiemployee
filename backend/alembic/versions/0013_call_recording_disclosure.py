"""add call recording disclosure flag

Revision ID: 0013_call_recording_disclosure
Revises: 0012_calling_retell
Create Date: 2026-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_call_recording_disclosure"
down_revision = "0012_calling_retell"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("call_campaign_settings")}
    if "call_recording_disclosure_enabled" in columns:
        return
    op.add_column(
        "call_campaign_settings",
        sa.Column("call_recording_disclosure_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("call_campaign_settings", "call_recording_disclosure_enabled", server_default=None)


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("call_campaign_settings")}
    if "call_recording_disclosure_enabled" not in columns:
        return
    op.drop_column("call_campaign_settings", "call_recording_disclosure_enabled")
