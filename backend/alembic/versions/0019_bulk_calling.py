"""Add durable Allstate bulk-calling workflow.

Revision ID: 0019_bulk_calling
Revises: 0018_script_content_hashes
"""

from alembic import op
import sqlalchemy as sa


revision = '0019_bulk_calling'
down_revision = '0018_script_content_hashes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call_campaign_settings', sa.Column('campaign_status', sa.String(), nullable=False, server_default='not_started'))
    op.add_column('call_campaign_settings', sa.Column('baseline_version', sa.String(), nullable=False, server_default='v8'))
    op.add_column('call_campaign_settings', sa.Column('automatic_retry_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    for name in ('status_changed_at', 'started_at', 'paused_at', 'stopped_at', 'completed_at'):
        op.add_column('call_campaign_settings', sa.Column(name, sa.DateTime(), nullable=True))
    op.create_index('ix_call_campaign_settings_campaign_status', 'call_campaign_settings', ['campaign_status'])
    op.add_column('consent_source_profiles', sa.Column('organization_represented', sa.String(), nullable=False, server_default='Allstate'))
    op.add_column('consented_calling_leads', sa.Column('consent_reference', sa.String(), nullable=True))
    op.create_index('ix_consented_calling_leads_consent_reference', 'consented_calling_leads', ['consent_reference'])
    op.execute("UPDATE consented_calling_leads SET consent_reference = consent_proof WHERE consent_reference IS NULL AND consent_proof IS NOT NULL")

    op.create_table(
        'call_contact_import_batches',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('source_profile_id', sa.String(), sa.ForeignKey('consent_source_profiles.id'), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='reviewed'),
        sa.Column('uploaded_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ready_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('review_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('blocked_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reason_counts', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    for name in ('company_id', 'campaign_id', 'source_profile_id', 'status'):
        op.create_index(f'ix_call_contact_import_batches_{name}', 'call_contact_import_batches', [name])

    op.create_table(
        'call_contact_import_rows',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('batch_id', sa.String(), sa.ForeignKey('call_contact_import_batches.id'), nullable=False),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('phone_number', sa.String(), nullable=True),
        sa.Column('classification', sa.String(), nullable=False),
        sa.Column('blocker_codes', sa.JSON(), nullable=False),
        sa.Column('blocker_messages', sa.JSON(), nullable=False),
        sa.Column('normalized', sa.JSON(), nullable=False),
        sa.Column('canonical_lead_id', sa.String(), sa.ForeignKey('consented_calling_leads.id'), nullable=True),
        sa.Column('is_test', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('batch_id', 'row_number'),
    )
    for name in ('batch_id', 'company_id', 'campaign_id', 'phone_number', 'classification', 'canonical_lead_id', 'is_test'):
        op.create_index(f'ix_call_contact_import_rows_{name}', 'call_contact_import_rows', [name])

    op.create_table(
        'call_queue_items',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('canonical_lead_id', sa.String(), sa.ForeignKey('consented_calling_leads.id'), nullable=False),
        sa.Column('phone_number', sa.String(), nullable=False),
        sa.Column('dedupe_key', sa.String(), nullable=False),
        sa.Column('script_version_id', sa.String(), sa.ForeignKey('call_script_versions.id'), nullable=False),
        sa.Column('script_version', sa.Integer(), nullable=False),
        sa.Column('provider_agent_id', sa.String(), nullable=False),
        sa.Column('provider_agent_version', sa.Integer(), nullable=True),
        sa.Column('consent_snapshot', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='queued'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('scheduled_after', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('provider_call_id', sa.String(), nullable=True),
        sa.Column('call_attempt_id', sa.String(), sa.ForeignKey('call_attempts.id'), nullable=True),
        sa.Column('outcome', sa.String(), nullable=True),
        sa.Column('callback_at', sa.DateTime(), nullable=True),
        sa.Column('callback_timezone', sa.String(), nullable=True),
        sa.Column('callback_reason', sa.Text(), nullable=True),
        sa.Column('callback_consent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('failure_code', sa.String(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('execution_mode', sa.String(), nullable=False, server_default='live'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    for name in ('company_id', 'campaign_id', 'canonical_lead_id', 'phone_number', 'dedupe_key', 'status', 'priority', 'scheduled_after', 'provider_call_id', 'call_attempt_id', 'outcome', 'callback_at', 'failure_code', 'execution_mode'):
        op.create_index(f'ix_call_queue_items_{name}', 'call_queue_items', [name], unique=name == 'dedupe_key')


def downgrade() -> None:
    op.drop_table('call_queue_items')
    op.drop_table('call_contact_import_rows')
    op.drop_table('call_contact_import_batches')
    op.drop_index('ix_consented_calling_leads_consent_reference', table_name='consented_calling_leads')
    op.drop_column('consented_calling_leads', 'consent_reference')
    op.drop_column('consent_source_profiles', 'organization_represented')
    op.drop_index('ix_call_campaign_settings_campaign_status', table_name='call_campaign_settings')
    for name in ('completed_at', 'stopped_at', 'paused_at', 'started_at', 'status_changed_at', 'automatic_retry_enabled', 'baseline_version', 'campaign_status'):
        op.drop_column('call_campaign_settings', name)
