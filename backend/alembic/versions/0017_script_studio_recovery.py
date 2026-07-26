"""script studio recovery and consent source profiles

Revision ID: 0017_script_studio_recovery
Revises: 0016_call_script_studio
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_script_studio_recovery"
down_revision = "0016_call_script_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_script_versions",
        sa.Column("publish_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column("call_script_versions", sa.Column("failure_stage", sa.String(), nullable=True))
    op.add_column("call_script_versions", sa.Column("recovery_action", sa.Text(), nullable=True))
    op.create_table(
        "consent_source_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("campaign_id", sa.String(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("approved_consent_language", sa.Text(), nullable=False),
        sa.Column("organization_authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("automated_call_permission", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consent_proof_method", sa.String(), nullable=False),
        sa.Column("default_province", sa.String(), nullable=False, server_default="Ontario"),
        sa.Column("default_timezone", sa.String(), nullable=False, server_default="America/Toronto"),
        sa.Column("source_approval_evidence", sa.Text(), nullable=False),
        sa.Column("approval_date", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "name"),
    )
    op.create_index("ix_consent_source_profiles_company_id", "consent_source_profiles", ["company_id"])
    op.create_index("ix_consent_source_profiles_campaign_id", "consent_source_profiles", ["campaign_id"])
    op.add_column(
        "consented_calling_leads",
        sa.Column("source_profile_id", sa.String(), sa.ForeignKey("consent_source_profiles.id"), nullable=True),
    )
    op.add_column(
        "consented_calling_leads",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_consented_calling_leads_source_profile_id", "consented_calling_leads", ["source_profile_id"])
    op.create_index("ix_consented_calling_leads_is_test", "consented_calling_leads", ["is_test"])


def downgrade() -> None:
    op.drop_index("ix_consented_calling_leads_is_test", table_name="consented_calling_leads")
    op.drop_index("ix_consented_calling_leads_source_profile_id", table_name="consented_calling_leads")
    op.drop_column("consented_calling_leads", "is_test")
    op.drop_column("consented_calling_leads", "source_profile_id")
    op.drop_index("ix_consent_source_profiles_campaign_id", table_name="consent_source_profiles")
    op.drop_index("ix_consent_source_profiles_company_id", table_name="consent_source_profiles")
    op.drop_table("consent_source_profiles")
    op.drop_column("call_script_versions", "recovery_action")
    op.drop_column("call_script_versions", "failure_stage")
    op.drop_column("call_script_versions", "publish_state")
