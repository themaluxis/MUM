"""Add overseerr_user_id to users table

Revision ID: add_overseerr_user_id
Revises: fix_history_logs_column_name
Create Date: 2025-09-28 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_overseerr_user_id'
down_revision = 'fix_history_logs_column_name'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('overseerr_user_id', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('overseerr_user_id')