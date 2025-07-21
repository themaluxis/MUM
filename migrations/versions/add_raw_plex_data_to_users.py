"""Add raw_plex_data field to users table

Revision ID: add_raw_plex_data
Revises: 042cecbc08c7
Create Date: 2024-01-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_raw_plex_data'
down_revision = '042cecbc08c7'
branch_labels = None
depends_on = None

def upgrade():
    # Add raw_plex_data column to users table
    op.add_column('users', sa.Column('raw_plex_data', sa.Text(), nullable=True))

def downgrade():
    # Remove raw_plex_data column from users table
    op.drop_column('users', 'raw_plex_data')