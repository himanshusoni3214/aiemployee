"""add call script studio and consented pilot controls

Revision ID: 0016_call_script_studio
Revises: 0015_call_sales_quality
Create Date: 2026-07-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0016_call_script_studio'
down_revision = '0015_call_sales_quality'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'call_script_versions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('retell_agent_id', sa.String(), nullable=False),
        sa.Column('conversation_flow_id', sa.String(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='draft'),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('opening_internal', sa.Text(), nullable=False),
        sa.Column('opening_consented', sa.Text(), nullable=False),
        sa.Column('purpose_statement', sa.Text(), nullable=False),
        sa.Column('discovery_content', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('objection_library', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('closing_library', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('voicemail_content', sa.Text(), nullable=False, server_default=''),
        sa.Column('voice_settings', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('compliance_content', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('talking_points', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('estimated_prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('node_changes', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('test_result', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('created_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('compliance_approved_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('compliance_approved_at', sa.DateTime(), nullable=True),
        sa.Column('published_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('retell_agent_version', sa.Integer(), nullable=True),
        sa.Column('retell_flow_version', sa.Integer(), nullable=True),
        sa.Column('rollback_from_version', sa.Integer(), nullable=True),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.UniqueConstraint('campaign_id', 'version_number'),
    )
    op.create_index('ix_call_script_versions_campaign_id', 'call_script_versions', ['campaign_id'])
    op.create_index('ix_call_script_versions_status', 'call_script_versions', ['status'])
    op.create_index(
        'uq_call_script_versions_one_published',
        'call_script_versions',
        ['campaign_id'],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        'call_script_audits',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('script_version_id', sa.String(), sa.ForeignKey('call_script_versions.id'), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('actor', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('before_value', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('after_value', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('retell_result', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('test_result', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index('ix_call_script_audits_script_version_id', 'call_script_audits', ['script_version_id'])
    op.create_table(
        'call_compliance_items',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('item_key', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False, server_default='campaign'),
        sa.Column('mandatory', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('status', sa.String(), nullable=False, server_default='incomplete'),
        sa.Column('approver', sa.String(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('effective_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('campaign_id', 'item_key'),
    )
    op.create_index('ix_call_compliance_items_campaign_id', 'call_compliance_items', ['campaign_id'])
    op.create_table(
        'consented_calling_leads',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('first_name', sa.String(), nullable=False),
        sa.Column('last_name', sa.String(), nullable=True),
        sa.Column('phone_number', sa.String(), nullable=False),
        sa.Column('timezone', sa.String(), nullable=False),
        sa.Column('province', sa.String(), nullable=False),
        sa.Column('product_interest', sa.String(), nullable=False),
        sa.Column('consent_status', sa.String(), nullable=False, server_default='under_review'),
        sa.Column('consent_type', sa.String(), nullable=False),
        sa.Column('consent_source', sa.String(), nullable=False),
        sa.Column('consent_text', sa.Text(), nullable=False),
        sa.Column('consent_timestamp', sa.DateTime(), nullable=False),
        sa.Column('consented_number', sa.String(), nullable=False),
        sa.Column('automated_or_synthesized_call_consent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('organization_authorized', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('consent_proof', sa.Text(), nullable=False),
        sa.Column('consent_withdrawn', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('consent_expiry', sa.DateTime(), nullable=True),
        sa.Column('renewal_month', sa.String(), nullable=True),
        sa.Column('preferred_call_time', sa.String(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('dncl_status', sa.String(), nullable=False, server_default='review_required'),
        sa.Column('internal_dnc_clear', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('suppression_clear', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('eligibility_status', sa.String(), nullable=False, server_default='Consent under review'),
        sa.Column('eligibility_reasons', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('approved_for_call', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('approved_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('campaign_id', 'phone_number'),
    )
    op.create_index('ix_consented_calling_leads_campaign_id', 'consented_calling_leads', ['campaign_id'])
    op.create_index('ix_consented_calling_leads_eligibility_status', 'consented_calling_leads', ['eligibility_status'])
    op.create_table(
        'pilot_call_entries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('lead_id', sa.String(), sa.ForeignKey('consented_calling_leads.id'), nullable=False),
        sa.Column('script_version_id', sa.String(), sa.ForeignKey('call_script_versions.id'), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending_approval'),
        sa.Column('approved_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('confirmation_text', sa.String(), nullable=True),
        sa.Column('agent_id_snapshot', sa.String(), nullable=False),
        sa.Column('agent_version_snapshot', sa.Integer(), nullable=True),
        sa.Column('script_version_snapshot', sa.Integer(), nullable=False),
        sa.Column('estimated_max_cost_usd', sa.Float(), nullable=False, server_default='1'),
        sa.Column('call_attempt_id', sa.String(), sa.ForeignKey('call_attempts.id'), nullable=True),
        sa.Column('blocked_reasons', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('campaign_id', 'lead_id'),
    )
    op.create_index('ix_pilot_call_entries_campaign_id', 'pilot_call_entries', ['campaign_id'])
    op.create_index('ix_pilot_call_entries_status', 'pilot_call_entries', ['status'])
    op.add_column('call_attempts', sa.Column('script_version_id', sa.String(), nullable=True))
    op.add_column('call_attempts', sa.Column('consented_calling_lead_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_call_attempts_script_version', 'call_attempts', 'call_script_versions', ['script_version_id'], ['id'])
    op.create_foreign_key('fk_call_attempts_consented_lead', 'call_attempts', 'consented_calling_leads', ['consented_calling_lead_id'], ['id'])
    op.create_index('ix_call_attempts_script_version_id', 'call_attempts', ['script_version_id'])
    op.create_index('ix_call_attempts_consented_calling_lead_id', 'call_attempts', ['consented_calling_lead_id'])


def downgrade() -> None:
    op.drop_index('ix_call_attempts_consented_calling_lead_id', table_name='call_attempts')
    op.drop_index('ix_call_attempts_script_version_id', table_name='call_attempts')
    op.drop_constraint('fk_call_attempts_consented_lead', 'call_attempts', type_='foreignkey')
    op.drop_constraint('fk_call_attempts_script_version', 'call_attempts', type_='foreignkey')
    op.drop_column('call_attempts', 'consented_calling_lead_id')
    op.drop_column('call_attempts', 'script_version_id')
    op.drop_table('pilot_call_entries')
    op.drop_table('consented_calling_leads')
    op.drop_table('call_compliance_items')
    op.drop_table('call_script_audits')
    op.drop_index('uq_call_script_versions_one_published', table_name='call_script_versions')
    op.drop_table('call_script_versions')
