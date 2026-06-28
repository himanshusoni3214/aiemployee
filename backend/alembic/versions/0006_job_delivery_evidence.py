"""job delivery evidence

Revision ID: 0006_job_delivery_evidence
Revises: 0005_job_terminal_states
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_job_delivery_evidence'
down_revision = '0005_job_terminal_states'
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _add_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_present(table_name: str, index_name: str) -> None:
    if index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_if_present(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'imported'")
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'synced'")

    _add_if_missing('jobs', sa.Column('provider_message_id', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('recipient_email', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('sent_at', sa.DateTime(), nullable=True))
    _add_if_missing('jobs', sa.Column('delivery_status', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('evidence_type', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('source_output_path', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('verification_reason', sa.Text(), nullable=True))
    _add_if_missing('jobs', sa.Column('hermes_job_id', sa.String(), nullable=True))
    _add_if_missing('jobs', sa.Column('hermes_run_timestamp', sa.DateTime(), nullable=True))
    _add_if_missing('jobs', sa.Column('external_execution_key', sa.String(), nullable=True))

    for column in (
        'provider_message_id',
        'recipient_email',
        'sent_at',
        'delivery_status',
        'evidence_type',
        'hermes_job_id',
        'hermes_run_timestamp',
        'external_execution_key',
    ):
        _index_if_missing('jobs', f'ix_jobs_{column}', [column])


def downgrade():
    for column in (
        'provider_message_id',
        'recipient_email',
        'sent_at',
        'delivery_status',
        'evidence_type',
        'hermes_job_id',
        'hermes_run_timestamp',
        'external_execution_key',
    ):
        _drop_index_if_present('jobs', f'ix_jobs_{column}')

    for column in (
        'external_execution_key',
        'hermes_run_timestamp',
        'hermes_job_id',
        'verification_reason',
        'source_output_path',
        'evidence_type',
        'delivery_status',
        'sent_at',
        'recipient_email',
        'provider_message_id',
    ):
        _drop_if_present('jobs', column)
