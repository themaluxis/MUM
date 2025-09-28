"""User Unification Migration - Step 1.2

Revision ID: user_unification
Revises: c278f68ebade
Create Date: 2025-01-26 12:00:00.000000

This migration creates the new unified User table and migrates data from:
- owners -> users (userType='owner')
- user_app_access -> users (userType='local') 
- user_media_access -> users (userType='service')

Preserves all relationships and establishes linkedUserId connections.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'user_unification'
down_revision = 'c278f68ebade'
branch_labels = None
depends_on = None


def upgrade():
    # Create UserType enum
    user_type_enum = sa.Enum('owner', 'local', 'service', name='usertype')
    user_type_enum.create(op.get_bind())
    
    # Create the new unified users table
    op.create_table('users',
        # Core Identity
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('userType', user_type_enum, nullable=False),
        
        # Linking & Server
        sa.Column('linkedUserId', sa.String(length=36), nullable=True),
        sa.Column('server_id', sa.Integer(), nullable=True),
        
        # External Service Identity
        sa.Column('external_user_id', sa.String(length=255), nullable=True),
        sa.Column('external_user_alt_id', sa.String(length=255), nullable=True),
        sa.Column('external_username', sa.String(length=255), nullable=True),
        sa.Column('external_email', sa.String(length=120), nullable=True),
        
        # Service Access Permissions
        sa.Column('allowed_library_ids', sa.Text(), nullable=True),
        sa.Column('allow_downloads', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('allow_4k_transcode', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('service_settings', sa.Text(), nullable=True),
        
        # Status & Activity
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        
        # Additional Info
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('external_avatar_url', sa.String(length=512), nullable=True),
        sa.Column('used_invite_id', sa.Integer(), nullable=True),
        sa.Column('service_join_date', sa.DateTime(), nullable=True),
        
        # Whitelist Settings
        sa.Column('is_discord_bot_whitelisted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('is_purge_whitelisted', sa.Boolean(), nullable=False, server_default='0'),
        
        # Service-Specific Status
        sa.Column('is_home_user', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('shares_back', sa.Boolean(), nullable=False, server_default='0'),
        
        # Discord Integration
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_avatar_hash', sa.String(length=255), nullable=True),
        sa.Column('discord_access_token', sa.String(length=255), nullable=True),
        sa.Column('discord_refresh_token', sa.String(length=255), nullable=True),
        sa.Column('discord_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('discord_email', sa.String(length=255), nullable=True),
        sa.Column('discord_email_verified', sa.Boolean(), nullable=True),
        
        # Access Expiration
        sa.Column('access_expires_at', sa.DateTime(), nullable=True),
        
        # Raw Data Storage
        sa.Column('user_raw_data', sa.Text(), nullable=True),
        sa.Column('stream_raw_data', sa.Text(), nullable=True),
        
        # Overseerr Integration
        sa.Column('overseerr_user_id', sa.Integer(), nullable=True),
        
        # Local Account Fields
        sa.Column('localUsername', sa.String(length=255), nullable=True),
        sa.Column('password_hash', sa.String(length=256), nullable=True),
        
        # Owner-Specific Fields
        sa.Column('preferred_user_list_view', sa.String(length=10), nullable=False, server_default='cards'),
        sa.Column('force_password_change', sa.Boolean(), nullable=False, server_default='0'),
        
        # Plex Integration (for owners)
        sa.Column('plex_uuid', sa.String(length=255), nullable=True),
        sa.Column('plex_username', sa.String(length=255), nullable=True),
        sa.Column('plex_thumb', sa.String(length=512), nullable=True),
        
        # Primary Key
        sa.PrimaryKeyConstraint('id'),
        
        # Foreign Keys
        sa.ForeignKeyConstraint(['linkedUserId'], ['users.uuid'], ),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.ForeignKeyConstraint(['used_invite_id'], ['invites.id'], ),
        
        # Unique Constraints
        sa.UniqueConstraint('uuid', name='uq_users_uuid'),
        sa.UniqueConstraint('localUsername', name='uq_users_local_username'),
        sa.UniqueConstraint('external_user_id', 'server_id', name='uq_users_external_server'),
        sa.UniqueConstraint('linkedUserId', 'server_id', name='uq_users_linked_server'),
        sa.UniqueConstraint('discord_user_id', name='uq_users_discord_user_id'),
        sa.UniqueConstraint('plex_uuid', name='uq_users_plex_uuid'),
    )
    
    # Create indexes for performance
    op.create_index('idx_users_uuid', 'users', ['uuid'])
    op.create_index('idx_users_usertype', 'users', ['userType'])
    op.create_index('idx_users_type_server', 'users', ['userType', 'server_id'])
    op.create_index('idx_users_linked_active', 'users', ['linkedUserId', 'is_active'])
    op.create_index('idx_users_external_user', 'users', ['external_user_id', 'server_id'])
    op.create_index('idx_users_local_username', 'users', ['localUsername'])
    op.create_index('idx_users_activity', 'users', ['last_activity_at'])
    op.create_index('idx_users_expiration', 'users', ['access_expires_at'])
    op.create_index('idx_users_service_join', 'users', ['service_join_date'])
    op.create_index('idx_users_discord_user', 'users', ['discord_user_id'])
    op.create_index('idx_users_overseerr', 'users', ['overseerr_user_id'])
    
    # Get database connection for data migration
    connection = op.get_bind()
    
    print("Starting user data migration...")
    
    # Step 1: Migrate owners -> users (userType='owner')
    print("Migrating owners to users table...")
    owners_query = text("""
        INSERT INTO users (
            id, uuid, userType, localUsername, password_hash, 
            plex_uuid, plex_username, plex_thumb,
            discord_user_id, discord_username, discord_avatar_hash,
            discord_access_token, discord_refresh_token, discord_token_expires_at,
            discord_email, discord_email_verified,
            created_at, last_login_at, preferred_user_list_view, force_password_change
        )
        SELECT 
            id,
            CONCAT('owner-', id),  -- Generate UUID for owner
            'owner',  -- userType
            username,  -- localUsername
            password_hash,
            plex_uuid,
            plex_username,
            plex_thumb,
            discord_user_id,
            discord_username,
            discord_avatar_hash,
            discord_access_token,
            discord_refresh_token,
            discord_token_expires_at,
            email,  -- Store in discord_email field
            discord_email_verified,
            created_at,
            last_login_at,
            preferred_user_list_view,
            force_password_change
        FROM owners
    """)
    result1 = connection.execute(owners_query)
    print(f"Migrated {result1.rowcount} owners")
    
    # Step 2: Migrate user_app_access -> users (userType='local')
    print("Migrating user_app_access to users table...")
    user_app_access_query = text("""
        INSERT INTO users (
            id, uuid, userType, localUsername, password_hash,
            created_at, updated_at, last_login_at, is_active,
            notes, used_invite_id, access_expires_at,
            discord_user_id, discord_username, discord_avatar_hash,
            discord_access_token, discord_refresh_token, discord_token_expires_at,
            discord_email, discord_email_verified
        )
        SELECT 
            id + 1000000,  -- Offset to avoid ID conflicts
            uuid,
            'local',  -- userType
            username,  -- localUsername
            password_hash,
            created_at,
            updated_at,
            last_login_at,
            is_active,
            notes,
            used_invite_id,
            access_expires_at,
            discord_user_id,
            discord_username,
            discord_avatar_hash,
            discord_access_token,
            discord_refresh_token,
            discord_token_expires_at,
            email,  -- Store in discord_email field
            discord_email_verified
        FROM user_app_access
    """)
    result2 = connection.execute(user_app_access_query)
    print(f"Migrated {result2.rowcount} user_app_access records")
    
    # Step 3: Migrate user_media_access -> users (userType='service')
    print("Migrating user_media_access to users table...")
    user_media_access_query = text("""
        INSERT INTO users (
            id, uuid, userType, linkedUserId, server_id,
            external_user_id, external_user_alt_id, external_username, external_email,
            allowed_library_ids, allow_downloads, allow_4k_transcode, service_settings,
            is_active, last_activity_at, created_at, updated_at,
            notes, external_avatar_url, used_invite_id, service_join_date,
            is_discord_bot_whitelisted, is_purge_whitelisted,
            is_home_user, shares_back,
            discord_user_id, discord_username, access_expires_at,
            user_raw_data, stream_raw_data, overseerr_user_id
        )
        SELECT 
            id + 2000000,  -- Offset to avoid ID conflicts
            uuid,
            'service',  -- userType
            (SELECT u.uuid FROM user_app_access u WHERE u.id = user_media_access.user_app_access_id),  -- linkedUserId via UUID lookup
            server_id,
            external_user_id,
            external_user_alt_id,
            external_username,
            external_email,
            allowed_library_ids,
            allow_downloads,
            allow_4k_transcode,
            service_settings,
            is_active,
            last_activity_at,
            created_at,
            updated_at,
            notes,
            external_avatar_url,
            used_invite_id,
            service_join_date,
            is_discord_bot_whitelisted,
            is_purge_whitelisted,
            is_home_user,
            shares_back,
            discord_user_id,
            discord_username,
            access_expires_at,
            user_raw_data,
            stream_raw_data,
            overseerr_user_id
        FROM user_media_access
    """)
    result3 = connection.execute(user_media_access_query)
    print(f"Migrated {result3.rowcount} user_media_access records")
    
    # Step 4: Update foreign key references to point to new users table
    print("Updating foreign key references...")
    
    # Update media_stream_history to use new user UUIDs
    print("Updating media_stream_history references...")
    
    # For user_app_access references
    update_stream_history_app_query = text("""
        UPDATE media_stream_history 
        SET user_app_access_uuid = (
            SELECT uuid FROM users 
            WHERE users.userType = 'local' 
            AND users.id - 1000000 = (
                SELECT id FROM user_app_access 
                WHERE user_app_access.uuid = media_stream_history.user_app_access_uuid
            )
        )
        WHERE user_app_access_uuid IS NOT NULL
    """)
    
    # For user_media_access references  
    update_stream_history_media_query = text("""
        UPDATE media_stream_history 
        SET user_media_access_uuid = (
            SELECT uuid FROM users 
            WHERE users.userType = 'service' 
            AND users.id - 2000000 = (
                SELECT id FROM user_media_access 
                WHERE user_media_access.uuid = media_stream_history.user_media_access_uuid
            )
        )
        WHERE user_media_access_uuid IS NOT NULL
    """)
    
    connection.execute(update_stream_history_app_query)
    connection.execute(update_stream_history_media_query)
    
    # Update invite_usages table references
    print("Updating invite_usages references...")
    update_invite_usages_query = text("""
        UPDATE invite_usages 
        SET user_app_access_id = (
            SELECT id FROM users 
            WHERE users.userType = 'local' 
            AND users.id - 1000000 = invite_usages.user_app_access_id
        )
        WHERE user_app_access_id IS NOT NULL
    """)
    connection.execute(update_invite_usages_query)
    
    # Update history_logs table references
    print("Updating history_logs references...")
    update_history_owner_query = text("""
        UPDATE history_logs 
        SET owner_id = (
            SELECT id FROM users 
            WHERE users.userType = 'owner' 
            AND users.id = history_logs.owner_id
        )
        WHERE owner_id IS NOT NULL
    """)
    
    update_history_user_query = text("""
        UPDATE history_logs 
        SET user_app_access_id = (
            SELECT id FROM users 
            WHERE users.userType = 'local' 
            AND users.id - 1000000 = history_logs.user_app_access_id
        )
        WHERE user_app_access_id IS NOT NULL
    """)
    
    connection.execute(update_history_owner_query)
    connection.execute(update_history_user_query)
    
    # Update user_preferences table references
    print("Updating user_preferences references...")
    update_preferences_query = text("""
        UPDATE user_preferences 
        SET owner_id = (
            SELECT id FROM users 
            WHERE users.userType = 'owner' 
            AND users.id = user_preferences.owner_id
        )
        WHERE owner_id IS NOT NULL
    """)
    connection.execute(update_preferences_query)
    
    # Update app_user_roles junction table
    print("Updating app_user_roles references...")
    update_roles_query = text("""
        UPDATE app_user_roles 
        SET app_user_id = (
            SELECT id FROM users 
            WHERE users.userType = 'local' 
            AND users.id - 1000000 = app_user_roles.app_user_id
        )
        WHERE app_user_id IS NOT NULL
    """)
    connection.execute(update_roles_query)
    
    print("Data migration completed successfully!")
    print(f"Total migrated: {result1.rowcount} owners + {result2.rowcount} local users + {result3.rowcount} service users")


def downgrade():
    """
    Downgrade migration - restore original three-table structure
    """
    connection = op.get_bind()
    
    print("Rolling back user unification...")
    
    # Recreate original tables
    print("Recreating original user tables...")
    
    # Recreate owners table
    op.create_table('owners_restored',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('plex_uuid', sa.String(length=255), nullable=True),
        sa.Column('plex_username', sa.String(length=255), nullable=True),
        sa.Column('plex_thumb', sa.String(length=512), nullable=True),
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_avatar_hash', sa.String(length=255), nullable=True),
        sa.Column('discord_access_token', sa.String(length=255), nullable=True),
        sa.Column('discord_refresh_token', sa.String(length=255), nullable=True),
        sa.Column('discord_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('discord_email_verified', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('preferred_user_list_view', sa.String(length=10), nullable=False),
        sa.Column('force_password_change', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('discord_user_id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('plex_uuid'),
        sa.UniqueConstraint('username')
    )
    
    # Migrate owners back
    restore_owners_query = text("""
        INSERT INTO owners_restored (
            id, username, email, password_hash, plex_uuid, plex_username, plex_thumb,
            discord_user_id, discord_username, discord_avatar_hash,
            discord_access_token, discord_refresh_token, discord_token_expires_at,
            discord_email_verified, created_at, last_login_at,
            preferred_user_list_view, force_password_change
        )
        SELECT 
            id, localUsername, discord_email, password_hash, plex_uuid, plex_username, plex_thumb,
            discord_user_id, discord_username, discord_avatar_hash,
            discord_access_token, discord_refresh_token, discord_token_expires_at,
            discord_email_verified, created_at, last_login_at,
            preferred_user_list_view, force_password_change
        FROM users WHERE userType = 'owner'
    """)
    connection.execute(restore_owners_query)
    
    # Drop the unified users table
    op.drop_table('users')
    
    # Rename restored table
    op.rename_table('owners_restored', 'owners')
    
    print("Rollback completed - restored original table structure")