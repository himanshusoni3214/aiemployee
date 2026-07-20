"""calling retell integration

Revision ID: 0012_calling_retell
Revises: 0011_outreach_window
Create Date: 2026-07-20 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0012_calling_retell'
down_revision = '0011_outreach_window'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _table_exists('call_campaign_settings'):
        op.create_table(
            'call_campaign_settings',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
            sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
            sa.Column('provider', sa.String(), nullable=False, server_default='retell'),
            sa.Column('provider_connected', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('provider_agent_id', sa.String(), nullable=True),
            sa.Column('from_number', sa.String(), nullable=True),
            sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'),
            sa.Column('allowed_calling_days', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('allowed_calling_hours', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('daily_call_limit', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('hourly_call_limit', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('concurrent_call_limit', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('internal_test_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('internal_test_numbers', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('prospect_calling_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('automated_queue_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('recording_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('transcription_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('appointment_booking_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('campaign_id'),
        )
        op.create_index('ix_call_campaign_settings_company_id', 'call_campaign_settings', ['company_id'])
        op.create_index('ix_call_campaign_settings_campaign_id', 'call_campaign_settings', ['campaign_id'])
        op.create_index('ix_call_campaign_settings_provider_agent_id', 'call_campaign_settings', ['provider_agent_id'])

    if not _table_exists('lead_phone_consents'):
        op.create_table(
            'lead_phone_consents',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
            sa.Column('canonical_lead_id', sa.String(), nullable=True),
            sa.Column('phone_number', sa.String(), nullable=False),
            sa.Column('consent_status', sa.String(), nullable=False, server_default='granted'),
            sa.Column('consent_type', sa.String(), nullable=False, server_default='internal_self_test'),
            sa.Column('consent_text', sa.Text(), nullable=True),
            sa.Column('consent_source', sa.String(), nullable=True),
            sa.Column('consent_timestamp', sa.DateTime(), nullable=True),
            sa.Column('consent_expiry', sa.DateTime(), nullable=True),
            sa.Column('consent_proof_path', sa.String(), nullable=True),
            sa.Column('consented_number', sa.String(), nullable=True),
            sa.Column('automated_or_ai_call_consent', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('internal_self_test', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('verified_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('verified_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_lead_phone_consents_company_id', 'lead_phone_consents', ['company_id'])
        op.create_index('ix_lead_phone_consents_canonical_lead_id', 'lead_phone_consents', ['canonical_lead_id'])
        op.create_index('ix_lead_phone_consents_phone_number', 'lead_phone_consents', ['phone_number'])
        op.create_index('ix_lead_phone_consents_consent_type', 'lead_phone_consents', ['consent_type'])
        op.create_index('ix_lead_phone_consents_consent_status', 'lead_phone_consents', ['consent_status'])

    if not _table_exists('call_attempts'):
        op.create_table(
            'call_attempts',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
            sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
            sa.Column('canonical_lead_id', sa.String(), nullable=True),
            sa.Column('provider', sa.String(), nullable=False, server_default='retell'),
            sa.Column('provider_call_id', sa.String(), nullable=True, unique=True),
            sa.Column('provider_agent_id', sa.String(), nullable=True),
            sa.Column('from_number', sa.String(), nullable=True),
            sa.Column('to_number', sa.String(), nullable=False),
            sa.Column('mode', sa.String(), nullable=False, server_default='internal_test'),
            sa.Column('status', sa.String(), nullable=False, server_default='requested'),
            sa.Column('confirmation_text', sa.String(), nullable=True),
            sa.Column('requested_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('requested_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('answered_at', sa.DateTime(), nullable=True),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('termination_reason', sa.Text(), nullable=True),
            sa.Column('provider_receipt', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('internal_test', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        for column in ['company_id', 'campaign_id', 'canonical_lead_id', 'provider_call_id', 'provider_agent_id', 'to_number', 'mode', 'status']:
            op.create_index(f'ix_call_attempts_{column}', 'call_attempts', [column])

    if not _table_exists('call_transcripts'):
        op.create_table(
            'call_transcripts',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('call_attempt_id', sa.String(), sa.ForeignKey('call_attempts.id'), nullable=False, unique=True),
            sa.Column('transcript', sa.Text(), nullable=True),
            sa.Column('transcript_segments', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('recording_url', sa.Text(), nullable=True),
            sa.Column('sentiment', sa.String(), nullable=True),
            sa.Column('objections', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('extracted_fields', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('provider_artifacts', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_call_transcripts_call_attempt_id', 'call_transcripts', ['call_attempt_id'])

    if not _table_exists('call_dispositions'):
        op.create_table(
            'call_dispositions',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('call_attempt_id', sa.String(), sa.ForeignKey('call_attempts.id'), nullable=False, unique=True),
            sa.Column('disposition', sa.String(), nullable=False, server_default='incomplete'),
            sa.Column('interested', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('appointment_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('appointment_booked', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('callback_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('do_not_call_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('wrong_number', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('voicemail', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_call_dispositions_call_attempt_id', 'call_dispositions', ['call_attempt_id'])
        op.create_index('ix_call_dispositions_disposition', 'call_dispositions', ['disposition'])

    if not _table_exists('call_appointments'):
        op.create_table(
            'call_appointments',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('call_attempt_id', sa.String(), sa.ForeignKey('call_attempts.id'), nullable=False),
            sa.Column('canonical_lead_id', sa.String(), nullable=True),
            sa.Column('assigned_agent', sa.String(), nullable=True),
            sa.Column('start_time', sa.String(), nullable=True),
            sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'),
            sa.Column('status', sa.String(), nullable=False, server_default='requested'),
            sa.Column('insurance_interest', sa.String(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('calendar_event_id', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_call_appointments_call_attempt_id', 'call_appointments', ['call_attempt_id'])
        op.create_index('ix_call_appointments_canonical_lead_id', 'call_appointments', ['canonical_lead_id'])
        op.create_index('ix_call_appointments_status', 'call_appointments', ['status'])

    if not _table_exists('retell_webhook_events'):
        op.create_table(
            'retell_webhook_events',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('provider_call_id', sa.String(), nullable=True),
            sa.Column('event_type', sa.String(), nullable=False),
            sa.Column('event_hash', sa.String(), nullable=False, unique=True),
            sa.Column('received_at', sa.DateTime(), nullable=False),
            sa.Column('processed_at', sa.DateTime(), nullable=True),
            sa.Column('processing_status', sa.String(), nullable=False, server_default='received'),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('payload_redacted', sa.JSON(), nullable=False, server_default='{}'),
        )
        op.create_index('ix_retell_webhook_events_provider_call_id', 'retell_webhook_events', ['provider_call_id'])
        op.create_index('ix_retell_webhook_events_event_type', 'retell_webhook_events', ['event_type'])
        op.create_index('ix_retell_webhook_events_event_hash', 'retell_webhook_events', ['event_hash'])
        op.create_index('ix_retell_webhook_events_processing_status', 'retell_webhook_events', ['processing_status'])


def downgrade():
    for table in [
        'retell_webhook_events',
        'call_appointments',
        'call_dispositions',
        'call_transcripts',
        'call_attempts',
        'lead_phone_consents',
        'call_campaign_settings',
    ]:
        if _table_exists(table):
            op.drop_table(table)
