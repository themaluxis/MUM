"""Add public_url field to media_servers table

Revision ID: add_public_url_to_media_servers
Revises: 
Create Date: 2025-01-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_public_url_to_media_servers'
down_revision = 'add_rating_key_to_media_items'  # This should be set to the previous migration
branch_labels = None
depends_on = None

def upgrade():
    # Add public_url column to media_servers table
    op.add_column('media_servers', sa.Column('public_url', sa.String(length=512), nullable=True))

def downgrade():
    # Remove public_url column from media_servers table
    op.drop_column('media_servers', 'public_url')