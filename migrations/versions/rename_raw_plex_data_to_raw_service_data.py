"""Rename raw_plex_data to raw_service_data

Revision ID: rename_raw_plex_data
Revises: 
Create Date: 2025-01-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'rename_raw_plex_data'
down_revision = 'add_password_hash'  # Points to the latest migration in the chain
branch_labels = None
depends_on = None


def upgrade():
    """Rename raw_plex_data column to raw_service_data"""
    # Rename the column
    op.alter_column('users', 'raw_plex_data', new_column_name='raw_service_data')


def downgrade():
    """Rename raw_service_data column back to raw_plex_data"""
    # Rename the column back
    op.alter_column('users', 'raw_service_data', new_column_name='raw_plex_data')