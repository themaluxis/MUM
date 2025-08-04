"""Add password_hash column to users table

Revision ID: add_password_hash
Revises: add_invite_servers
Create Date: 2025-01-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_password_hash'
down_revision = 'add_invite_servers'
branch_labels = None
depends_on = None


def upgrade():
    # Add password_hash column to users table
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('password_hash', sa.String(length=256), nullable=True))


def downgrade():
    # Remove password_hash column from users table
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_hash')