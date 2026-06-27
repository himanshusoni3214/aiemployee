"""control center reporting

Revision ID: 0003_control_center_reporting
Revises: 0002_operational_controls
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_control_center_reporting'
down_revision = '0002_operational_controls'
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column['name'] for column in inspector.get_columns(table_name)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _add_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_if_present(table_name: str, column_name: str) -> None:
    if table_name in _tables() and column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def upgrade():
    _add_if_missing('companies', sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'))
    _add_if_missing('companies', sa.Column('default_report_recipient', sa.String(), nullable=True))
    _add_if_missing('companies', sa.Column('daily_email_limit', sa.Integer(), nullable=False, server_default='50'))
    _add_if_missing('companies', sa.Column('notes', sa.Text(), nullable=True))

    _add_if_missing('campaigns', sa.Column('description', sa.Text(), nullable=True))
    _add_if_missing('campaigns', sa.Column('target_audience', sa.Text(), nullable=True))
    _add_if_missing('campaigns', sa.Column('geographic_area', sa.String(), nullable=True))
    _add_if_missing('campaigns', sa.Column('daily_email_limit', sa.Integer(), nullable=False, server_default='0'))
    _add_if_missing('campaigns', sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'))
    _add_if_missing('campaigns', sa.Column('allowed_sending_days', sa.JSON(), nullable=False, server_default='[]'))
    _add_if_missing('campaigns', sa.Column('allowed_sending_hours', sa.JSON(), nullable=False, server_default='{}'))
    _add_if_missing('campaigns', sa.Column('internal_test_recipient', sa.String(), nullable=True))
    _add_if_missing('campaigns', sa.Column('report_recipient', sa.String(), nullable=True))
    _add_if_missing('campaigns', sa.Column('dry_run_mode', sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_if_missing('campaigns', sa.Column('start_date', sa.String(), nullable=True))
    _add_if_missing('campaigns', sa.Column('end_date', sa.String(), nullable=True))

    _add_if_missing('ai_employees', sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=True))
    _add_if_missing('ai_employees', sa.Column('hermes_job_id', sa.String(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('approved_script', sa.String(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('working_directory', sa.String(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('dry_run_mode', sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_if_missing('ai_employees', sa.Column('last_successful_run_at', sa.DateTime(), nullable=True))
    _add_if_missing('ai_employees', sa.Column('last_failed_run_at', sa.DateTime(), nullable=True))

    _add_if_missing('schedules', sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'))

    if 'outreach_events' not in _tables():
        op.create_table(
            'outreach_events',
            sa.Column('event_id', sa.String(), primary_key=True),
            sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=True, index=True),
            sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=True, index=True),
            sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=True, index=True),
            sa.Column('lead_id', sa.String(), sa.ForeignKey('leads.id'), nullable=True),
            sa.Column('recipient', sa.String(), nullable=True, index=True),
            sa.Column('business', sa.String(), nullable=True),
            sa.Column('subject', sa.String(), nullable=True),
            sa.Column('attempted_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('status', sa.String(), nullable=False, index=True),
            sa.Column('message_id', sa.String(), nullable=True, index=True),
            sa.Column('thread_id', sa.String(), nullable=True),
            sa.Column('provider', sa.String(), nullable=True),
            sa.Column('error_code', sa.String(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('dry_run', sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
            sa.Column('job_run_id', sa.String(), nullable=True, index=True),
            sa.Column('source_file', sa.String(), nullable=True),
            sa.Column('raw', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if 'report_runs' not in _tables():
        op.create_table(
            'report_runs',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=True, index=True),
            sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=True, index=True),
            sa.Column('report_date', sa.String(), nullable=False, index=True),
            sa.Column('timezone', sa.String(), nullable=False, server_default='America/Toronto'),
            sa.Column('generated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('artifact_path', sa.String(), nullable=True),
            sa.Column('metrics', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('evidence', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('delivery_result', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('status', sa.String(), nullable=False, server_default='generated', index=True),
        )


def downgrade():
    for table_name in ('report_runs', 'outreach_events'):
        if table_name in _tables():
            op.drop_table(table_name)
    _drop_if_present('schedules', 'timezone')
    for column_name in ('last_failed_run_at', 'last_successful_run_at', 'dry_run_mode', 'working_directory', 'approved_script', 'hermes_job_id', 'campaign_id'):
        _drop_if_present('ai_employees', column_name)
    for column_name in ('end_date', 'start_date', 'dry_run_mode', 'report_recipient', 'internal_test_recipient', 'allowed_sending_hours', 'allowed_sending_days', 'timezone', 'daily_email_limit', 'geographic_area', 'target_audience', 'description'):
        _drop_if_present('campaigns', column_name)
    for column_name in ('notes', 'daily_email_limit', 'default_report_recipient', 'timezone'):
        _drop_if_present('companies', column_name)
