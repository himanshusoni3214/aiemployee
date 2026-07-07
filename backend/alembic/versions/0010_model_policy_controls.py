"""model policy controls

Revision ID: 0010_model_policy_controls
Revises: 0009_outreach_controls
Create Date: 2026-07-07 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_model_policy_controls'
down_revision = '0009_outreach_controls'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('global_model_policies', sa.Column('id', sa.String(), primary_key=True), sa.Column('name', sa.String(), nullable=False, server_default='default'), sa.Column('provider', sa.String(), nullable=False, server_default='openrouter'), sa.Column('model', sa.String(), nullable=False, server_default='nvidia/nemotron-3-super-120b-a12b'), sa.Column('approved_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('blocked_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('fallback_enabled', sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column('fail_closed', sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column('daily_budget_usd', sa.Integer(), nullable=False, server_default='0'), sa.Column('monthly_budget_usd', sa.Integer(), nullable=False, server_default='0'), sa.Column('max_cost_per_run_usd', sa.Integer(), nullable=False, server_default='0'), sa.Column('notes', sa.Text(), nullable=True), sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.UniqueConstraint('name'))
    op.create_index('ix_global_model_policies_name', 'global_model_policies', ['name'])
    op.create_table('company_model_policies', sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False), sa.Column('provider', sa.String(), nullable=True), sa.Column('model', sa.String(), nullable=True), sa.Column('approved_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('blocked_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('fallback_enabled', sa.Boolean(), nullable=True), sa.Column('fail_closed', sa.Boolean(), nullable=True), sa.Column('daily_budget_usd', sa.Integer(), nullable=True), sa.Column('monthly_budget_usd', sa.Integer(), nullable=True), sa.Column('max_cost_per_run_usd', sa.Integer(), nullable=True), sa.Column('notes', sa.Text(), nullable=True), sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.UniqueConstraint('company_id'))
    op.create_index('ix_company_model_policies_company_id', 'company_model_policies', ['company_id'])
    op.create_table('employee_model_policies', sa.Column('id', sa.String(), primary_key=True), sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=False), sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=True), sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=True), sa.Column('hermes_job_id', sa.String(), nullable=True), sa.Column('provider', sa.String(), nullable=True), sa.Column('model', sa.String(), nullable=True), sa.Column('approved_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('blocked_models', sa.JSON(), nullable=False, server_default='[]'), sa.Column('fallback_enabled', sa.Boolean(), nullable=True), sa.Column('fail_closed', sa.Boolean(), nullable=True), sa.Column('daily_budget_usd', sa.Integer(), nullable=True), sa.Column('monthly_budget_usd', sa.Integer(), nullable=True), sa.Column('max_cost_per_run_usd', sa.Integer(), nullable=True), sa.Column('notes', sa.Text(), nullable=True), sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), sa.UniqueConstraint('employee_id'))
    for col in ['employee_id', 'company_id', 'campaign_id', 'hermes_job_id']:
        op.create_index(f'ix_employee_model_policies_{col}', 'employee_model_policies', [col])
    op.create_table('model_usage_audits', sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=True), sa.Column('campaign_id', sa.String(), sa.ForeignKey('campaigns.id'), nullable=True), sa.Column('employee_id', sa.String(), sa.ForeignKey('ai_employees.id'), nullable=True), sa.Column('hermes_job_id', sa.String(), nullable=True), sa.Column('provider', sa.String(), nullable=False, server_default='openrouter'), sa.Column('model', sa.String(), nullable=False, server_default='nvidia/nemotron-3-super-120b-a12b'), sa.Column('normalized_model', sa.String(), nullable=False, server_default='openrouter/nvidia/nemotron-3-super-120b-a12b'), sa.Column('task_type', sa.String(), nullable=True), sa.Column('status', sa.String(), nullable=False, server_default='allowed'), sa.Column('reason', sa.Text(), nullable=True), sa.Column('estimated_cost_usd', sa.Integer(), nullable=True), sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'), sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    for col in ['company_id', 'campaign_id', 'employee_id', 'hermes_job_id', 'provider', 'model', 'normalized_model', 'status', 'created_at']:
        op.create_index(f'ix_model_usage_audits_{col}', 'model_usage_audits', [col])


def downgrade():
    op.drop_table('model_usage_audits')
    op.drop_table('employee_model_policies')
    op.drop_table('company_model_policies')
    op.drop_table('global_model_policies')
