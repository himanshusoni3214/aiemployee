"""persist deterministic call sales quality

Revision ID: 0015_call_sales_quality
Revises: 0014_call_cost_tracking
Create Date: 2026-07-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0015_call_sales_quality'
down_revision = '0014_call_cost_tracking'
branch_labels = None
depends_on = None

def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column['name'] for column in inspector.get_columns('call_transcripts')}
    if 'sales_score' not in existing:
        op.add_column('call_transcripts', sa.Column('sales_score', sa.Integer(), nullable=True))
    if 'sales_score_details' not in existing:
        op.add_column('call_transcripts', sa.Column('sales_score_details', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
        op.alter_column('call_transcripts', 'sales_score_details', server_default=None)
    if 'retell_agent_migrations' not in inspector.get_table_names():
        op.create_table(
            'retell_agent_migrations',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('legacy_agent_id', sa.String(), nullable=False),
            sa.Column('successor_agent_id', sa.String(), nullable=False),
            sa.Column('conversation_flow_id', sa.String(), nullable=False),
            sa.Column('cutover_at', sa.DateTime(), nullable=True),
            sa.Column('user_authorization', sa.Text(), nullable=False),
            sa.Column('old_number_assignment', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column('new_number_assignment', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column('rollback_status', sa.String(), nullable=False, server_default='not_required'),
            sa.Column('test_result', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('successor_agent_id'),
        )
        op.create_index('ix_retell_agent_migrations_legacy_agent_id', 'retell_agent_migrations', ['legacy_agent_id'])
        op.create_index('ix_retell_agent_migrations_successor_agent_id', 'retell_agent_migrations', ['successor_agent_id'], unique=True)
        op.create_index('ix_retell_agent_migrations_rollback_status', 'retell_agent_migrations', ['rollback_status'])

def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if 'retell_agent_migrations' in inspector.get_table_names():
        op.drop_index('ix_retell_agent_migrations_rollback_status', table_name='retell_agent_migrations')
        op.drop_index('ix_retell_agent_migrations_successor_agent_id', table_name='retell_agent_migrations')
        op.drop_index('ix_retell_agent_migrations_legacy_agent_id', table_name='retell_agent_migrations')
        op.drop_table('retell_agent_migrations')
    existing = {column['name'] for column in inspector.get_columns('call_transcripts')}
    if 'sales_score_details' in existing:
        op.drop_column('call_transcripts', 'sales_score_details')
    if 'sales_score' in existing:
        op.drop_column('call_transcripts', 'sales_score')
