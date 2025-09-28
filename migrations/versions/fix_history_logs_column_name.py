"""Fix history_logs column name from user_app_access_id to local_user_id

Revision ID: fix_history_logs_column_name
Revises: cleanup_deprecated_user_tables
Create Date: 2025-01-15 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'fix_history_logs_column_name'
down_revision = 'cleanup_deprecated_user_tables'
branch_labels = None
depends_on = None


def upgrade():
    """
    Rename user_app_access_id column to local_user_id in history_logs table
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('history_logs')]

    with op.batch_alter_table('history_logs', schema=None) as batch_op:
        if 'user_app_access_id' in columns and 'local_user_id' not in columns:
            batch_op.alter_column('user_app_access_id', new_column_name='local_user_id')
        elif 'user_app_access_id' not in columns and 'local_user_id' not in columns:
            batch_op.add_column(sa.Column('local_user_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key('fk_history_logs_local_user_id', 'users', ['local_user_id'], ['id'])


def downgrade():
    """
    Rename local_user_id column back to user_app_access_id in history_logs table
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('history_logs')]

    with op.batch_alter_table('history_logs', schema=None) as batch_op:
        if 'local_user_id' in columns:
            batch_op.drop_constraint('fk_history_logs_local_user_id', type_='foreignkey')
            batch_op.alter_column('local_user_id', new_column_name='user_app_access_id')