"""Make user_app_access_id nullable in UserMediaAccess

Revision ID: fix_user_app_access_nullable
Revises: 23fd7378a0fe
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fix_user_app_access_nullable'
down_revision = '23fd7378a0fe'
branch_labels = None
depends_on = None


def upgrade():
    # Make user_app_access_id nullable to support standalone server users
    with op.batch_alter_table('user_media_access', schema=None) as batch_op:
        batch_op.alter_column('user_app_access_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)
        # Add unique constraint for standalone users
        batch_op.create_unique_constraint('_external_user_server_uc', ['external_user_id', 'server_id'])


def downgrade():
    # Revert user_app_access_id to not nullable
    # Note: This will fail if there are NULL values in the database
    with op.batch_alter_table('user_media_access', schema=None) as batch_op:
        # Remove the unique constraint for standalone users
        batch_op.drop_constraint('_external_user_server_uc', type_='unique')
        batch_op.alter_column('user_app_access_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)