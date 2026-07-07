"""outreach controls

Revision ID: 0009_outreach_controls
Revises: 0008_template_provisioning
Create Date: 2026-07-06 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_outreach_controls'
down_revision = '0008_template_provisioning'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'company_outreach_settings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('sender_name', sa.String(), nullable=True),
        sa.Column('sender_email', sa.String(), nullable=True),
        sa.Column('reply_to_email', sa.String(), nullable=True),
        sa.Column('physical_mailing_address', sa.Text(), nullable=True),
        sa.Column('unsubscribe_text', sa.Text(), nullable=True),
        sa.Column('daily_send_limit', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('hourly_send_limit', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('allowed_sending_days', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('allowed_sending_hours', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'),
        sa.Column('approved_sender_connected', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('compliance_acknowledged', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('prospect_sending_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('internal_test_recipient', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('company_id'),
    )
    op.create_index('ix_company_outreach_settings_company_id', 'company_outreach_settings', ['company_id'])

    op.create_table(
        'suppression_entries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('kind', sa.String(), nullable=False, server_default='email'),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(), nullable=False, server_default='dashboard'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('company_id', 'kind', 'value'),
    )
    op.create_index('ix_suppression_entries_company_id', 'suppression_entries', ['company_id'])
    op.create_index('ix_suppression_entries_kind', 'suppression_entries', ['kind'])
    op.create_index('ix_suppression_entries_value', 'suppression_entries', ['value'])

    op.create_table(
        'lead_approvals',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=True),
        sa.Column('hermes_job_id', sa.String(), nullable=True),
        sa.Column('source_run_id', sa.String(), nullable=True),
        sa.Column('lead_key', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('domain', sa.String(), nullable=True),
        sa.Column('business', sa.String(), nullable=True),
        sa.Column('state', sa.String(), nullable=False, server_default='new'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('history', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('campaign_id', 'lead_key'),
    )
    for col in ['company_id', 'campaign_id', 'employee_id', 'hermes_job_id', 'source_run_id', 'lead_key', 'email', 'domain', 'state']:
        op.create_index(f'ix_lead_approvals_{col}', 'lead_approvals', [col])

    op.create_table(
        'outreach_drafts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=True),
        sa.Column('hermes_job_id', sa.String(), nullable=True),
        sa.Column('source_run_id', sa.String(), nullable=True),
        sa.Column('lead_key', sa.String(), nullable=False),
        sa.Column('lead_email', sa.String(), nullable=True),
        sa.Column('business', sa.String(), nullable=True),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='draft_created'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('approved_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('campaign_id', 'lead_key', 'version'),
    )
    for col in ['company_id', 'campaign_id', 'employee_id', 'hermes_job_id', 'source_run_id', 'lead_key', 'lead_email', 'status']:
        op.create_index(f'ix_outreach_drafts_{col}', 'outreach_drafts', [col])

    op.create_table(
        'reply_monitor_events',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=True),
        sa.Column('hermes_job_id', sa.String(), nullable=True),
        sa.Column('lead_key', sa.String(), nullable=True),
        sa.Column('recipient', sa.String(), nullable=True),
        sa.Column('thread_id', sa.String(), nullable=True),
        sa.Column('classification', sa.String(), nullable=False, server_default='unclassified'),
        sa.Column('status', sa.String(), nullable=False, server_default='detected'),
        sa.Column('raw', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    for col in ['company_id', 'campaign_id', 'employee_id', 'hermes_job_id', 'lead_key', 'recipient', 'thread_id', 'classification', 'status']:
        op.create_index(f'ix_reply_monitor_events_{col}', 'reply_monitor_events', [col])


def downgrade():
    op.drop_table('reply_monitor_events')
    op.drop_table('outreach_drafts')
    op.drop_table('lead_approvals')
    op.drop_table('suppression_entries')
    op.drop_table('company_outreach_settings')
