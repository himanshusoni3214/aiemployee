"""persist Retell agent version and provider cost

Revision ID: 0014_call_cost_tracking
Revises: 0013_call_recording_disclosure
Create Date: 2026-07-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_call_cost_tracking"
down_revision = "0013_call_recording_disclosure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("call_attempts")}
    columns = [
        sa.Column("provider_agent_version", sa.Integer(), nullable=True),
        sa.Column("provider_cost_cents", sa.Float(), nullable=True),
        sa.Column("provider_cost_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_cost_currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("provider_cost_breakdown", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("provider_llm_model", sa.String(), nullable=True),
        sa.Column("provider_voice_id", sa.String(), nullable=True),
    ]
    for column in columns:
        if column.name not in existing:
            op.add_column("call_attempts", column)
    op.alter_column("call_attempts", "provider_cost_final", server_default=None)
    op.alter_column("call_attempts", "provider_cost_currency", server_default=None)
    op.alter_column("call_attempts", "provider_cost_breakdown", server_default=None)


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("call_attempts")}
    for name in [
        "provider_voice_id", "provider_llm_model", "provider_cost_breakdown",
        "provider_cost_currency", "provider_cost_final", "provider_cost_cents",
        "provider_agent_version",
    ]:
        if name in existing:
            op.drop_column("call_attempts", name)
