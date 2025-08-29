"""Add library_name column to media_stream_history

Revision ID: add_library_name_to_stream_history
Revises: update_media_stream_history_uuids
Create Date: 2024-01-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_library_name_to_stream_history'
down_revision = 'update_media_stream_history_uuids'
branch_labels = None
depends_on = None


def upgrade():
    # Add library_name column to media_stream_history table
    op.add_column('media_stream_history', sa.Column('library_name', sa.String(255), nullable=True))


def downgrade():
    # Remove library_name column from media_stream_history table
    op.drop_column('media_stream_history', 'library_name')