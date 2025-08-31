"""Add rating_key column to media_items table

Revision ID: add_rating_key_to_media_items
Revises: 527b5672e58b
Create Date: 2025-01-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_rating_key_to_media_items'
down_revision = 'add_external_media_item_id'
branch_labels = None
depends_on = None


def upgrade():
    # Add rating_key column to media_items table
    op.add_column('media_items', sa.Column('rating_key', sa.String(length=255), nullable=True))
    
    # Create index for better performance
    op.create_index('idx_media_items_rating_key', 'media_items', ['rating_key'])
    
    # Populate rating_key from extra_metadata for existing records
    # This will extract ratingKey from the JSON and populate the new column
    connection = op.get_bind()
    
    # For PostgreSQL
    try:
        connection.execute(sa.text("""
            UPDATE media_items 
            SET rating_key = extra_metadata->>'ratingKey'
            WHERE extra_metadata IS NOT NULL 
            AND extra_metadata->>'ratingKey' IS NOT NULL
        """))
    except Exception:
        # For SQLite (fallback - will need manual population)
        pass


def downgrade():
    # Remove index first
    op.drop_index('idx_media_items_rating_key', table_name='media_items')
    
    # Remove the column
    op.drop_column('media_items', 'rating_key')