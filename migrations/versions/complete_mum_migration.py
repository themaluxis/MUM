"""Complete MUM migration - All features in one migration

Revision ID: complete_mum_migration
Revises: 85cbf712e98d
Create Date: 2024-01-01 00:00:00.000000

This migration includes:
- Initial database schema
- Multi-service support
- Plugin system
- All enhancements for MUM (Multimedia User Management)

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = 'complete_mum_migration'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Create admin_roles association table
    op.create_table('admin_roles',
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['admin_accounts.id'], ),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
        sa.PrimaryKeyConstraint('admin_id', 'role_id')
    )

    # Create roles table
    op.create_table('roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('permissions', sa.Text(), nullable=True),
        sa.Column('color', sa.String(length=7), nullable=True),
        sa.Column('icon', sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Create settings table
    op.create_table('settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('value_type', sa.Enum('STRING', 'INTEGER', 'BOOLEAN', 'JSON', 'SECRET', name='settingvaluetype'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )
    op.create_index(op.f('ix_settings_key'), 'settings', ['key'], unique=False)

    # Create admin_accounts table
    op.create_table('admin_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=True),
        sa.Column('password_hash', sa.String(length=256), nullable=True),
        sa.Column('plex_uuid', sa.String(length=255), nullable=True),
        sa.Column('plex_username', sa.String(length=255), nullable=True),
        sa.Column('plex_thumb', sa.String(length=512), nullable=True),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('is_plex_sso_only', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_avatar_hash', sa.String(length=255), nullable=True),
        sa.Column('discord_access_token', sa.String(length=255), nullable=True),
        sa.Column('discord_refresh_token', sa.String(length=255), nullable=True),
        sa.Column('discord_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('discord_email', sa.String(length=255), nullable=True),
        sa.Column('discord_email_verified', sa.Boolean(), nullable=True),
        sa.Column('force_password_change', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('discord_user_id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('plex_uuid'),
        sa.UniqueConstraint('username')
    )

    # Create media_servers table (Multi-service support)
    op.create_table('media_servers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('service_type', sa.Enum('plex', 'emby', 'jellyfin', 'kavita', 'audiobookshelf', 'komga', 'romm', name='servicetype'), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('api_key', sa.String(length=512), nullable=True),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('password', sa.String(length=512), nullable=True),
        sa.Column('config', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create media_libraries table
    op.create_table('media_libraries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('library_type', sa.String(length=50), nullable=True),
        sa.Column('item_count', sa.Integer(), nullable=True),
        sa.Column('last_scanned', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('server_id', 'external_id', name='_server_library_uc')
    )

    # Create plugins table (Plugin system)
    op.create_table('plugins',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plugin_id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('plugin_type', sa.Enum('core', 'official', 'community', 'custom', name='plugintype'), nullable=False),
        sa.Column('status', sa.Enum('disabled', 'enabled', 'error', 'installing', 'updating', name='pluginstatus'), nullable=False),
        sa.Column('author', sa.String(length=100), nullable=True),
        sa.Column('homepage', sa.String(length=512), nullable=True),
        sa.Column('repository', sa.String(length=512), nullable=True),
        sa.Column('license', sa.String(length=50), nullable=True),
        sa.Column('module_path', sa.String(length=255), nullable=False),
        sa.Column('service_class', sa.String(length=255), nullable=False),
        sa.Column('config_schema', sa.Text(), nullable=True),
        sa.Column('default_config', sa.Text(), nullable=True),
        sa.Column('min_mum_version', sa.String(length=20), nullable=True),
        sa.Column('max_mum_version', sa.String(length=20), nullable=True),
        sa.Column('python_requirements', sa.Text(), nullable=True),
        sa.Column('supported_features', sa.Text(), nullable=True),
        sa.Column('installed_at', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('servers_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_plugins_plugin_id'), 'plugins', ['plugin_id'], unique=True)

    # Create plugin_repositories table
    op.create_table('plugin_repositories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('is_official', sa.Boolean(), nullable=False),
        sa.Column('auth_type', sa.String(length=20), nullable=True),
        sa.Column('auth_data', sa.Text(), nullable=True),
        sa.Column('last_sync', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create users table (Enhanced for multi-service)
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        # Primary identifiers
        sa.Column('primary_username', sa.String(length=255), nullable=False),
        sa.Column('primary_email', sa.String(length=255), nullable=True),
        # Legacy Plex fields (for backward compatibility)
        sa.Column('plex_user_id', sa.Integer(), nullable=True),
        sa.Column('plex_username', sa.String(length=255), nullable=True),
        sa.Column('plex_email', sa.String(length=255), nullable=True),
        sa.Column('plex_thumb_url', sa.String(length=512), nullable=True),
        sa.Column('plex_uuid', sa.String(length=255), nullable=True),
        sa.Column('is_home_user', sa.Boolean(), nullable=False),
        sa.Column('shares_back', sa.Boolean(), nullable=False),
        sa.Column('is_plex_friend', sa.Boolean(), nullable=False),
        sa.Column('plex_join_date', sa.DateTime(), nullable=True),
        # Discord integration
        sa.Column('discord_email', sa.String(length=255), nullable=True),
        sa.Column('discord_email_verified', sa.Boolean(), nullable=True),
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_avatar_hash', sa.String(length=255), nullable=True),
        # Legacy fields (deprecated in favor of UserMediaAccess)
        sa.Column('allowed_library_ids', sa.Text(), nullable=True),
        sa.Column('allowed_servers', sa.Text(), nullable=True),
        sa.Column('allow_downloads', sa.Boolean(), nullable=False),
        sa.Column('allow_4k_transcode', sa.Boolean(), nullable=False),
        # General user fields
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('avatar_url', sa.String(length=512), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_synced_with_plex', sa.DateTime(), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True),
        sa.Column('last_streamed_at', sa.DateTime(), nullable=True),
        # Access control
        sa.Column('access_expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_discord_bot_whitelisted', sa.Boolean(), nullable=False),
        sa.Column('is_purge_whitelisted', sa.Boolean(), nullable=False),
        # Invite relationship
        sa.Column('used_invite_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_access_expires_at'), 'users', ['access_expires_at'], unique=False)
    op.create_index(op.f('ix_users_discord_user_id'), 'users', ['discord_user_id'], unique=True)
    op.create_index(op.f('ix_users_plex_user_id'), 'users', ['plex_user_id'], unique=True)
    op.create_index(op.f('ix_users_plex_username'), 'users', ['plex_username'], unique=True)
    op.create_index(op.f('ix_users_plex_uuid'), 'users', ['plex_uuid'], unique=True)
    op.create_index(op.f('ix_users_primary_email'), 'users', ['primary_email'], unique=False)
    op.create_index(op.f('ix_users_primary_username'), 'users', ['primary_username'], unique=False)

    # Create user_media_access table (Multi-service user access)
    op.create_table('user_media_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('external_user_id', sa.String(length=255), nullable=True),
        sa.Column('external_username', sa.String(length=255), nullable=True),
        sa.Column('external_email', sa.String(length=255), nullable=True),
        sa.Column('allowed_library_ids', sa.Text(), nullable=True),
        sa.Column('allow_downloads', sa.Boolean(), nullable=False),
        sa.Column('allow_4k_transcode', sa.Boolean(), nullable=False),
        sa.Column('service_settings', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('access_expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'server_id', name='_user_server_uc')
    )

    # Create invites table
    op.create_table('invites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('custom_path', sa.String(length=100), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('current_uses', sa.Integer(), nullable=False),
        sa.Column('grant_library_ids', sa.Text(), nullable=True),
        sa.Column('allow_downloads', sa.Boolean(), nullable=False),
        sa.Column('created_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('membership_duration_days', sa.Integer(), nullable=True),
        sa.Column('force_discord_auth', sa.Boolean(), nullable=True),
        sa.Column('force_guild_membership', sa.Boolean(), nullable=True),
        sa.Column('grant_purge_whitelist', sa.Boolean(), nullable=True),
        sa.Column('grant_bot_whitelist', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_admin_id'], ['admin_accounts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_invites_custom_path'), 'invites', ['custom_path'], unique=True)
    op.create_index(op.f('ix_invites_is_active'), 'invites', ['is_active'], unique=False)
    op.create_index(op.f('ix_invites_token'), 'invites', ['token'], unique=True)

    # Create history_logs table
    op.create_table('history_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('event_type', sa.Enum('APP_STARTUP', 'APP_SHUTDOWN', 'SETTING_CHANGE', 'ADMIN_LOGIN_SUCCESS', 'ADMIN_LOGIN_FAIL', 'ADMIN_LOGOUT', 'ADMIN_PASSWORD_CHANGE', 'PLEX_CONFIG_TEST_SUCCESS', 'PLEX_CONFIG_TEST_FAIL', 'PLEX_CONFIG_SAVE', 'PLEX_SYNC_USERS_START', 'PLEX_SYNC_USERS_COMPLETE', 'PLEX_USER_ADDED_TO_SERVER', 'PLEX_USER_REMOVED_FROM_SERVER', 'PLEX_USER_LIBS_UPDATED_ON_SERVER', 'PLEX_SESSION_DETECTED', 'PUM_USER_ADDED_FROM_PLEX', 'PUM_USER_REMOVED_MISSING_IN_PLEX', 'PUM_USER_LIBRARIES_EDITED', 'PUM_USER_DELETED_FROM_PUM', 'INVITE_CREATED', 'INVITE_DELETED', 'INVITE_VIEWED', 'INVITE_USED_SUCCESS_PLEX', 'INVITE_USED_SUCCESS_DISCORD', 'INVITE_USED_ACCOUNT_LINKED', 'INVITE_USER_ACCEPTED_AND_SHARED', 'INVITE_EXPIRED', 'INVITE_MAX_USES_REACHED', 'DISCORD_CONFIG_SAVE', 'DISCORD_ADMIN_LINK_SUCCESS', 'DISCORD_ADMIN_UNLINK', 'ERROR_GENERAL', 'ERROR_PLEX_API', 'ERROR_DISCORD_API', 'DISCORD_BOT_START', 'DISCORD_BOT_STOP', 'DISCORD_BOT_ERROR', 'DISCORD_BOT_USER_LEFT_SERVER', 'DISCORD_BOT_USER_REMOVED_FROM_PLEX', 'DISCORD_BOT_ROLE_ADDED_INVITE_SENT', 'DISCORD_BOT_ROLE_REMOVED_USER_REMOVED', 'DISCORD_BOT_PURGE_DM_SENT', 'DISCORD_BOT_GUILD_MEMBER_CHECK_FAIL', name='eventtype'), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('invite_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['admin_id'], ['admin_accounts.id'], ),
        sa.ForeignKeyConstraint(['invite_id'], ['invites.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_history_logs_event_type'), 'history_logs', ['event_type'], unique=False)
    op.create_index(op.f('ix_history_logs_timestamp'), 'history_logs', ['timestamp'], unique=False)

    # Create invite_usages table
    op.create_table('invite_usages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invite_id', sa.Integer(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('plex_user_uuid', sa.String(length=255), nullable=True),
        sa.Column('plex_username', sa.String(length=255), nullable=True),
        sa.Column('plex_email', sa.String(length=120), nullable=True),
        sa.Column('plex_thumb', sa.String(length=512), nullable=True),
        sa.Column('plex_auth_successful', sa.Boolean(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=255), nullable=True),
        sa.Column('discord_username', sa.String(length=255), nullable=True),
        sa.Column('discord_auth_successful', sa.Boolean(), nullable=False),
        sa.Column('pum_user_id', sa.Integer(), nullable=True),
        sa.Column('accepted_invite', sa.Boolean(), nullable=False),
        sa.Column('status_message', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['invite_id'], ['invites.id'], ),
        sa.ForeignKeyConstraint(['pum_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create stream_history table (Legacy)
    op.create_table('stream_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_key', sa.String(length=255), nullable=True),
        sa.Column('rating_key', sa.String(length=255), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('stopped_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('platform', sa.String(length=255), nullable=True),
        sa.Column('product', sa.String(length=255), nullable=True),
        sa.Column('player', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('is_lan', sa.Boolean(), nullable=True),
        sa.Column('media_title', sa.String(length=255), nullable=True),
        sa.Column('media_type', sa.String(length=50), nullable=True),
        sa.Column('grandparent_title', sa.String(length=255), nullable=True),
        sa.Column('parent_title', sa.String(length=255), nullable=True),
        sa.Column('media_duration_seconds', sa.Integer(), nullable=True),
        sa.Column('view_offset_at_end_seconds', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create media_stream_history table (Enhanced multi-service)
    op.create_table('media_stream_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('session_key', sa.String(length=255), nullable=True),
        sa.Column('external_session_id', sa.String(length=255), nullable=True),
        sa.Column('rating_key', sa.String(length=255), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('stopped_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('platform', sa.String(length=255), nullable=True),
        sa.Column('product', sa.String(length=255), nullable=True),
        sa.Column('player', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('is_lan', sa.Boolean(), nullable=True),
        sa.Column('media_title', sa.String(length=255), nullable=True),
        sa.Column('media_type', sa.String(length=50), nullable=True),
        sa.Column('grandparent_title', sa.String(length=255), nullable=True),
        sa.Column('parent_title', sa.String(length=255), nullable=True),
        sa.Column('media_duration_seconds', sa.Integer(), nullable=True),
        sa.Column('view_offset_at_end_seconds', sa.Integer(), nullable=True),
        sa.Column('service_data', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Add foreign key constraints that reference tables created later
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_users_used_invite_id', 'invites', ['used_invite_id'], ['id'])

def downgrade():
    # Drop all tables in reverse order of creation
    op.drop_table('media_stream_history')
    op.drop_table('stream_history')
    op.drop_table('invite_usages')
    op.drop_index(op.f('ix_history_logs_timestamp'), table_name='history_logs')
    op.drop_index(op.f('ix_history_logs_event_type'), table_name='history_logs')
    op.drop_table('history_logs')
    op.drop_index(op.f('ix_invites_token'), table_name='invites')
    op.drop_index(op.f('ix_invites_is_active'), table_name='invites')
    op.drop_index(op.f('ix_invites_custom_path'), table_name='invites')
    op.drop_table('invites')
    op.drop_table('user_media_access')
    op.drop_index(op.f('ix_users_primary_username'), table_name='users')
    op.drop_index(op.f('ix_users_primary_email'), table_name='users')
    op.drop_index(op.f('ix_users_plex_uuid'), table_name='users')
    op.drop_index(op.f('ix_users_plex_username'), table_name='users')
    op.drop_index(op.f('ix_users_plex_user_id'), table_name='users')
    op.drop_index(op.f('ix_users_discord_user_id'), table_name='users')
    op.drop_index(op.f('ix_users_access_expires_at'), table_name='users')
    op.drop_table('users')
    op.drop_table('plugin_repositories')
    op.drop_index(op.f('ix_plugins_plugin_id'), table_name='plugins')
    op.drop_table('plugins')
    op.drop_table('media_libraries')
    op.drop_table('media_servers')
    op.drop_table('admin_accounts')
    op.drop_index(op.f('ix_settings_key'), table_name='settings')
    op.drop_table('settings')
    op.drop_table('roles')
    op.drop_table('admin_roles')