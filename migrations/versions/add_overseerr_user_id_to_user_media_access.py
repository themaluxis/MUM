"""Add overseerr_user_id to user_media_access and migrate data

Revision ID: add_overseerr_user_id
Revises: add_external_media_item_id_to_stream_history
Create Date: 2025-01-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'add_overseerr_user_id'
down_revision = '730c1a9ccff6'
branch_labels = None
depends_on = None


def upgrade():
    # Add overseerr_user_id column to user_media_access
    op.add_column('user_media_access', sa.Column('overseerr_user_id', sa.Integer(), nullable=True))
    
    # Create index for overseerr_user_id
    op.create_index('idx_user_media_access_overseerr_user_id', 'user_media_access', ['overseerr_user_id'])
    
    # Migrate existing data from overseerr_user_links to user_media_access
    # This will match users based on server_id and external_user_id (plex_user_id)
    connection = op.get_bind()
    
    # Update user_media_access with overseerr_user_id from overseerr_user_links
    migration_query = text("""
        UPDATE user_media_access 
        SET overseerr_user_id = (
            SELECT oul.overseerr_user_id 
            FROM overseerr_user_links oul 
            WHERE oul.server_id = user_media_access.server_id 
            AND oul.plex_user_id = user_media_access.external_user_id 
            AND oul.is_linked = true
            AND oul.overseerr_user_id IS NOT NULL
        )
        WHERE user_media_access.external_user_id IS NOT NULL
        AND user_media_access.server_id IN (
            SELECT DISTINCT server_id FROM overseerr_user_links WHERE overseerr_user_id IS NOT NULL
        )
    """)
    
    result = connection.execute(migration_query)
    print(f"Migrated {result.rowcount} Overseerr user links to user_media_access table")
    
    # Drop the overseerr_user_links table as it's no longer needed
    op.drop_table('overseerr_user_links')


def downgrade():
    # Recreate overseerr_user_links table
    op.create_table('overseerr_user_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plex_user_id', sa.String(length=255), nullable=False),
        sa.Column('plex_username', sa.String(length=255), nullable=False),
        sa.Column('plex_email', sa.String(length=255), nullable=True),
        sa.Column('overseerr_user_id', sa.Integer(), nullable=True),
        sa.Column('overseerr_username', sa.String(length=255), nullable=True),
        sa.Column('overseerr_email', sa.String(length=255), nullable=True),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('is_linked', sa.Boolean(), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], )
    )
    
    # Migrate data back from user_media_access to overseerr_user_links
    connection = op.get_bind()
    
    # Insert records back into overseerr_user_links for users with overseerr_user_id
    migration_query = text("""
        INSERT INTO overseerr_user_links (
            plex_user_id, plex_username, plex_email, overseerr_user_id,
            server_id, is_linked, last_sync_at, created_at, updated_at
        )
        SELECT 
            uma.external_user_id,
            uma.external_username,
            uma.external_email,
            uma.overseerr_user_id,
            uma.server_id,
            true,
            uma.updated_at,
            uma.created_at,
            uma.updated_at
        FROM user_media_access uma
        WHERE uma.overseerr_user_id IS NOT NULL
        AND uma.external_user_id IS NOT NULL
    """)
    
    connection.execute(migration_query)
    
    # Drop the overseerr_user_id column and index from user_media_access
    op.drop_index('idx_user_media_access_overseerr_user_id', table_name='user_media_access')
    op.drop_column('user_media_access', 'overseerr_user_id')