"""Cleanup deprecated user_app_access and user_media_access tables

Revision ID: cleanup_deprecated_user_tables
Revises: cleanup_deprecated_user_columns
Create Date: 2025-01-15 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cleanup_deprecated_user_tables'
down_revision = 'cleanup_deprecated_user_columns'
branch_labels = None
depends_on = None


def upgrade():
    """
    Remove deprecated user_app_access and user_media_access tables
    """
    print("Starting cleanup of deprecated user tables...")
    
    # Check if tables still exist before trying to drop them
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = inspector.get_table_names()
    
    # Drop user_app_access table if it exists
    if 'user_app_access' in table_names:
        print("Dropping user_app_access table...")
        op.drop_table('user_app_access')
        print("Dropped user_app_access table")
    else:
        print("user_app_access table not found, skipping")
    
    # Drop user_media_access table if it exists
    if 'user_media_access' in table_names:
        print("Dropping user_media_access table...")
        op.drop_table('user_media_access')
        print("Dropped user_media_access table")
    else:
        print("user_media_access table not found, skipping")
    
    print("Cleanup of deprecated user tables completed successfully!")


def downgrade():
    """
    Recreate the deprecated tables (for rollback purposes)
    NOTE: This will recreate empty tables. Data would need to be migrated back from users table.
    """
    print("Starting downgrade - recreating deprecated user tables...")
    print("WARNING: This creates empty tables. Data migration from users table not implemented.")
    
    # Recreate user_app_access table
    op.create_table('user_app_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('localUsername', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_avatar_hash', sa.String(length=255), nullable=True),
        sa.Column('discord_email', sa.String(length=255), nullable=True),
        sa.Column('discord_email_verified', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('localUsername'),
        sa.UniqueConstraint('uuid')
    )
    
    # Create indexes for user_app_access
    op.create_index('ix_user_app_access_email', 'user_app_access', ['email'], unique=False)
    op.create_index('ix_user_app_access_localUsername', 'user_app_access', ['localUsername'], unique=False)
    
    # Recreate user_media_access table
    op.create_table('user_media_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('external_user_id', sa.String(length=255), nullable=True),
        sa.Column('external_user_alt_id', sa.String(length=255), nullable=True),
        sa.Column('external_username', sa.String(length=255), nullable=True),
        sa.Column('external_email', sa.String(length=255), nullable=True),
        sa.Column('external_avatar_url', sa.String(length=512), nullable=True),
        sa.Column('access_expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('linkedUserId', sa.String(length=36), nullable=True),
        sa.Column('service_settings', sa.TEXT(), nullable=True),
        sa.Column('user_raw_data', sa.TEXT(), nullable=True),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid')
    )
    
    # Create indexes for user_media_access
    op.create_index('ix_user_media_access_external_user_id', 'user_media_access', ['external_user_id'], unique=False)
    op.create_index('ix_user_media_access_external_username', 'user_media_access', ['external_username'], unique=False)
    op.create_index('ix_user_media_access_server_id', 'user_media_access', ['server_id'], unique=False)
    
    print("Downgrade completed - deprecated tables recreated (empty)!")
    print("NOTE: You would need to manually migrate data back from the users table if needed.")