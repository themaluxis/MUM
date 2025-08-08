"""Add service_join_date column to users table

Revision ID: add_service_join_date
Revises: 
Create Date: 2025-08-08 01:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_service_join_date'
down_revision = 'rename_raw_plex_data'
head = None
branch_labels = None
depends_on = None


def upgrade():
    # Add the new service_join_date column
    op.add_column('users', sa.Column('service_join_date', sa.DateTime(), nullable=True))


def downgrade():
    # Remove the service_join_date column
    op.drop_column('users', 'service_join_date')