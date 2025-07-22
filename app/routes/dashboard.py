# File: app/routes/dashboard.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, g, make_response, session
)
from flask_login import login_required, current_user, logout_user 
import secrets
from app.models import User, Invite, HistoryLog, Setting, EventType, SettingValueType, AdminAccount, Role, UserPreferences 
from app.forms import (
    GeneralSettingsForm, DiscordConfigForm, SetPasswordForm, ChangePasswordForm, AdminCreateForm, AdminEditForm, RoleEditForm, RoleCreateForm, RoleMemberForm, AdminResetPasswordForm, PluginSettingsForm, TimezonePreferenceForm
    # If you create an AdvancedSettingsForm, import it here too.
)
from app.extensions import db, scheduler # For db.func.now() if used, or db specific types
from app.utils.helpers import log_event, setup_required, permission_required, any_permission_required
# No direct plexapi imports here, plex_service should handle that.
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import ServiceType
from app.services.plugin_manager import plugin_manager
from app.services.media_service_manager import MediaServiceManager
from app.services.unified_user_service import UnifiedUserService
from app.services import history_service
import json
from urllib.parse import urlparse
from datetime import datetime 
from functools import wraps
import xml.etree.ElementTree as ET
import xmltodict
import re

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.id == 1:
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
@setup_required 
def index():
    total_users = User.query.count()
    active_invites_count = Invite.query.filter(
        Invite.is_active == True,
        (Invite.expires_at == None) | (Invite.expires_at > db.func.now()), # Use db.func.now() for DB comparison
        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses)
    ).count()

    # --- NEW: Get active streams count ---
    active_streams_count = 0
    try:
        active_sessions_list = MediaServiceManager.get_all_active_sessions() # This returns a list
        if active_sessions_list:
            active_streams_count = len(active_sessions_list)
    except Exception as e:
        current_app.logger.error(f"Dashboard: Failed to get active streams count: {e}")
    # --- END NEW ---

    # --- Server Status Card Logic ---
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    server_status_data = {}

    if server_count == 1:
        server = all_servers[0]
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            server_status_data = service.get_server_info()
            server_status_data['server_id'] = server.id
            server_status_data['name'] = server.name
            server_status_data['service_type'] = server.service_type.value
    elif server_count > 1:
        online_count = 0
        offline_count = 0
        all_server_statuses = []
        for server in all_servers:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                status = service.get_server_info()
                status['server_id'] = server.id
                status['name'] = server.name
                status['service_type'] = server.service_type.value
                all_server_statuses.append(status)
                if status.get('online'):
                    online_count += 1
                else:
                    offline_count += 1
        server_status_data = {
            'multi_server': True,
            'online_count': online_count,
            'offline_count': offline_count,
            'all_statuses': all_server_statuses
        }
    # If server_count is 0, server_status_data will be an empty dict, which the template handles.
    current_app.logger.debug(f"Dashboard.py - index(): Server status from service: {server_status_data}")

    recent_activities = HistoryLog.query.order_by(HistoryLog.timestamp.desc()).limit(10).all()
    recent_activities_count = HistoryLog.query.count()

    return render_template('dashboard/index.html',
                           title="Dashboard",
                           total_users=total_users,
                           active_invites_count=active_invites_count,
                           active_streams_count=active_streams_count,
                           server_status=server_status_data,
                           recent_activities=recent_activities,
                           recent_activities_count=recent_activities_count)

# Helper function to build the history query based on request args
def _get_history_logs_query():
    query = HistoryLog.query
    search_message = request.args.get('search_message')
    event_type_filter = request.args.get('event_type')
    related_user_filter = request.args.get('related_user')

    if search_message: query = query.filter(HistoryLog.message.ilike(f"%{search_message}%"))
    if event_type_filter:
        try: query = query.filter(HistoryLog.event_type == EventType[event_type_filter])
        except KeyError: flash(f"Invalid event type filter: {event_type_filter}", "warning") # Flash won't show on partial
    if related_user_filter:
        from sqlalchemy import or_ 
        query = query.join(AdminAccount, AdminAccount.id == HistoryLog.admin_id, isouter=True) \
                     .join(User, User.id == HistoryLog.user_id, isouter=True) \
                     .filter(or_(
                         AdminAccount.username.ilike(f"%{related_user_filter}%"), 
                         AdminAccount.plex_username.ilike(f"%{related_user_filter}%"), 
                         User.plex_username.ilike(f"%{related_user_filter}%"), 
                         HistoryLog.admin_id.cast(db.String).ilike(f"%{related_user_filter}%"), 
                         HistoryLog.user_id.cast(db.String).ilike(f"%{related_user_filter}%")
                     ))
    return query

@bp.route('/settings')
@login_required
@setup_required
def settings_index():
    # Defines the order of tabs to check for permissions.
    # The first one the user has access to will be their destination.
    permission_map = [
        ('manage_general_settings', 'dashboard.settings_general'),
        ('view_admins_tab', 'dashboard.settings_admins'),
        ('view_admins_tab', 'dashboard.settings_roles'), # Use same perm for both admin tabs
        ('manage_discord_settings', 'dashboard.settings_discord'),
        ('manage_plugins', 'dashboard.settings_plugins'),
        ('manage_advanced_settings', 'dashboard.settings_advanced'), # A placeholder for a more general 'advanced' perm
    ]

    # Super Admin (ID 1) can see everything, default to general.
    if current_user.id == 1:
        return redirect(url_for('dashboard.settings_general'))

    # Find the first settings page the user has permission to view.
    for permission, endpoint in permission_map:
        if current_user.has_permission(permission):
            return redirect(url_for(endpoint))

    # If the user has a login but no settings permissions at all, deny access.
    flash("You do not have permission to view any settings pages.", "danger")
    return redirect(url_for('dashboard.index'))

@bp.route('/settings/general', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_general_settings')
def settings_general():
    form = GeneralSettingsForm()
    if form.validate_on_submit():
        # This route now ONLY handles general app settings.
        Setting.set('APP_NAME', form.app_name.data, SettingValueType.STRING, "Application Name")
        Setting.set('APP_BASE_URL', form.app_base_url.data.rstrip('/'), SettingValueType.STRING, "Application Base URL")
        app_local_url = form.app_local_url.data.rstrip('/') if form.app_local_url.data else None
        Setting.set('APP_LOCAL_URL', app_local_url or '', SettingValueType.STRING, "Application Local URL")
        
        # Update app config
        current_app.config['APP_NAME'] = form.app_name.data
        current_app.config['APP_BASE_URL'] = form.app_base_url.data.rstrip('/')
        current_app.config['APP_LOCAL_URL'] = app_local_url
        if hasattr(g, 'app_name'): g.app_name = form.app_name.data
        if hasattr(g, 'app_base_url'): g.app_base_url = form.app_base_url.data.rstrip('/')
        if hasattr(g, 'app_local_url'): g.app_local_url = app_local_url
        
        log_event(EventType.SETTING_CHANGE, "General application settings updated.", admin_id=current_user.id)
        flash('General settings saved successfully.', 'success')
        return redirect(url_for('dashboard.settings_general'))
    elif request.method == 'GET':
        form.app_name.data = Setting.get('APP_NAME')
        form.app_base_url.data = Setting.get('APP_BASE_URL')
        form.app_local_url.data = Setting.get('APP_LOCAL_URL')
    return render_template(
        'settings/index.html',
        title="General Settings", 
        form=form, 
        active_tab='general'
    )

@bp.route('/settings/account', methods=['GET', 'POST'])
@login_required
@setup_required
def settings_account():
    set_password_form = SetPasswordForm()
    change_password_form = ChangePasswordForm()
    timezone_form = TimezonePreferenceForm()

    if 'submit_timezone' in request.form and timezone_form.validate_on_submit():
        UserPreferences.set_timezone_preference(
            admin_id=current_user.id,
            preference=timezone_form.timezone_preference.data,
            local_timezone=timezone_form.local_timezone.data,
            time_format=timezone_form.time_format.data
        )
        flash('Timezone preference saved.', 'success')
        return redirect(url_for('dashboard.settings_account'))

    # --- Handle "Change Password" Form Submission ---
    if 'submit_change_password' in request.form and change_password_form.validate_on_submit():
        admin = AdminAccount.query.get(current_user.id)
        # Verify the current password first
        if admin.check_password(change_password_form.current_password.data):
            admin.set_password(change_password_form.new_password.data)
            admin.force_password_change = False
            db.session.commit()
            log_event(EventType.ADMIN_PASSWORD_CHANGE, "Admin changed their password.", admin_id=current_user.id)
            flash('Your password has been changed successfully.', 'success')
            return redirect(url_for('dashboard.settings_account'))
        else:
            flash('Incorrect current password.', 'danger')

    # --- Handle "Set Initial Password" Form Submission (moved from general) ---
    elif 'submit_set_password' in request.form and set_password_form.validate_on_submit():
        admin = AdminAccount.query.get(current_user.id)
        admin.username = set_password_form.username.data
        admin.set_password(set_password_form.password.data)
        admin.is_plex_sso_only = False
        db.session.commit()
        log_event(EventType.ADMIN_PASSWORD_CHANGE, "Admin added username/password to their SSO-only account.", admin_id=current_user.id)
        flash('Username and password have been set successfully!', 'success')
        return redirect(url_for('dashboard.settings_account'))

    if request.method == 'GET':
        prefs = UserPreferences.get_timezone_preference(current_user.id)
        timezone_form.timezone_preference.data = prefs.get('preference')
        timezone_form.local_timezone.data = prefs.get('local_timezone')
        timezone_form.time_format.data = prefs.get('time_format', '12')

    return render_template(
        'admin/account_settings.html', #<-- Render the new standalone template
        title="My Account",
        set_password_form=set_password_form,
        change_password_form=change_password_form,
        timezone_form=timezone_form
    )




@bp.route('/settings/discord', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_discord_settings')
def settings_discord():
    form = DiscordConfigForm(request.form if request.method == 'POST' else None)
    
    app_base_url_from_settings = Setting.get('APP_BASE_URL')
    invite_callback_path = "/invites/discord_callback" 
    admin_link_callback_path = "/auth/discord_callback_admin"
    try:
        invite_callback_path = url_for('invites.discord_oauth_callback', _external=False)
        admin_link_callback_path = url_for('auth.discord_callback_admin', _external=False)
    except Exception as e_url_gen:
        current_app.logger.error(f"Error generating relative callback paths for Discord settings display: {e_url_gen}")

    if app_base_url_from_settings:
        clean_app_base = app_base_url_from_settings.rstrip('/')
        if not invite_callback_path.startswith('/'): invite_callback_path = '/' + invite_callback_path
        if not admin_link_callback_path.startswith('/'): admin_link_callback_path = '/' + admin_link_callback_path
        discord_invite_redirect_uri_generated = f"{clean_app_base}{invite_callback_path}"
        discord_admin_link_redirect_uri_generated = f"{clean_app_base}{admin_link_callback_path}"
    else:
        discord_invite_redirect_uri_generated = "APP_BASE_URL not set - Cannot generate Invite Redirect URI"
        discord_admin_link_redirect_uri_generated = "APP_BASE_URL not set - Cannot generate Admin Link Redirect URI"
    
    discord_admin_linked = bool(current_user.discord_user_id)
    discord_admin_user_info = {
        'username': current_user.discord_username, 
        'id': current_user.discord_user_id, 
        'avatar': current_user.discord_avatar_hash 
    } if discord_admin_linked else None
    
    initial_oauth_enabled_for_admin_link_section = Setting.get_bool('DISCORD_OAUTH_ENABLED', False)

    if request.method == 'POST':
        if form.validate_on_submit():
            # Store original global setting state BEFORE changes
            original_require_guild = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)

            enable_oauth_from_form = form.enable_discord_oauth.data
            enable_bot_from_form = form.enable_discord_bot.data
            require_guild_membership_from_form = form.discord_require_guild_membership.data
            require_sso_on_invite_from_form = form.discord_bot_require_sso_on_invite.data

            final_enable_oauth = enable_oauth_from_form
            if (enable_bot_from_form or require_guild_membership_from_form) and not final_enable_oauth:
                final_enable_oauth = True
                flash_msg = "Discord OAuth (Section 1) was automatically enabled because "
                if enable_bot_from_form: flash_msg += "Bot Features require it."
                elif require_guild_membership_from_form: flash_msg += "'Require Server Membership' needs it."
                flash(flash_msg, "info")
            
            Setting.set('DISCORD_OAUTH_ENABLED', final_enable_oauth, SettingValueType.BOOLEAN)
            current_app.config['DISCORD_OAUTH_ENABLED'] = final_enable_oauth
            if hasattr(g, 'discord_oauth_enabled_for_invite'):
                g.discord_oauth_enabled_for_invite = final_enable_oauth

            if final_enable_oauth:
                Setting.set('DISCORD_CLIENT_ID', form.discord_client_id.data or Setting.get('DISCORD_CLIENT_ID', ""), SettingValueType.STRING)
                if form.discord_client_secret.data: 
                    Setting.set('DISCORD_CLIENT_SECRET', form.discord_client_secret.data, SettingValueType.SECRET)
                Setting.set('DISCORD_OAUTH_AUTH_URL', form.discord_oauth_auth_url.data or Setting.get('DISCORD_OAUTH_AUTH_URL', ""), SettingValueType.STRING)
                Setting.set('DISCORD_REDIRECT_URI_INVITE', discord_invite_redirect_uri_generated, SettingValueType.STRING)
                Setting.set('DISCORD_REDIRECT_URI_ADMIN_LINK', discord_admin_link_redirect_uri_generated, SettingValueType.STRING)

                if enable_bot_from_form: 
                    Setting.set('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', True, SettingValueType.BOOLEAN)
                else: 
                    Setting.set('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', require_sso_on_invite_from_form, SettingValueType.BOOLEAN)
                
                # --- NEW LOGIC: Grandfathering invites when guild requirement is disabled ---
                if original_require_guild is True and require_guild_membership_from_form is False:
                    now = datetime.utcnow()
                    affected_invites_query = Invite.query.filter(
                        Invite.is_active == True,
                        (Invite.expires_at == None) | (Invite.expires_at > now),
                        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses),
                        Invite.force_guild_membership.is_(None)
                    )
                    affected_invites = affected_invites_query.all()
                    
                    if affected_invites:
                        updated_invite_ids = []
                        for invite in affected_invites:
                            invite.force_guild_membership = True
                            updated_invite_ids.append(invite.id)
                        
                        try:
                            # The commit for this is handled below with other settings
                            log_event(
                                EventType.SETTING_CHANGE,
                                f"Admin disabled 'Require Guild Membership'. Grandfathered {len(affected_invites)} existing invite(s) by forcing their requirement to ON.",
                                admin_id=current_user.id,
                                details={'updated_invite_ids': updated_invite_ids}
                            )
                        except Exception as e_log:
                            current_app.logger.error(f"Error logging grandfathering of invites: {e_log}")
                # --- END NEW LOGIC ---

                # Now save the new global setting
                Setting.set('DISCORD_REQUIRE_GUILD_MEMBERSHIP', require_guild_membership_from_form, SettingValueType.BOOLEAN)
                
                if enable_bot_from_form or require_guild_membership_from_form:
                    Setting.set('DISCORD_GUILD_ID', form.discord_guild_id.data or Setting.get('DISCORD_GUILD_ID', ""), SettingValueType.STRING)
                    if require_guild_membership_from_form:
                        Setting.set('DISCORD_SERVER_INVITE_URL', form.discord_server_invite_url.data or Setting.get('DISCORD_SERVER_INVITE_URL', ""), SettingValueType.STRING)
                    elif not enable_bot_from_form: 
                        Setting.set('DISCORD_SERVER_INVITE_URL', "", SettingValueType.STRING) 
                else:
                    Setting.set('DISCORD_GUILD_ID', "", SettingValueType.STRING)
                    Setting.set('DISCORD_SERVER_INVITE_URL', "", SettingValueType.STRING)
            else: 
                # If OAuth is disabled, clear all related settings
                Setting.set('DISCORD_CLIENT_ID', "", SettingValueType.STRING)
                Setting.set('DISCORD_CLIENT_SECRET', "", SettingValueType.SECRET)
                Setting.set('DISCORD_OAUTH_AUTH_URL', "", SettingValueType.STRING)
                Setting.set('DISCORD_REDIRECT_URI_INVITE', "", SettingValueType.STRING)
                Setting.set('DISCORD_REDIRECT_URI_ADMIN_LINK', "", SettingValueType.STRING)
                Setting.set('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False, SettingValueType.BOOLEAN)
                Setting.set('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False, SettingValueType.BOOLEAN)
                Setting.set('DISCORD_GUILD_ID', "", SettingValueType.STRING)
                Setting.set('DISCORD_SERVER_INVITE_URL', "", SettingValueType.STRING)

            # Bot settings save logic (unchanged)
            Setting.set('DISCORD_BOT_ENABLED', enable_bot_from_form, SettingValueType.BOOLEAN)
            if enable_bot_from_form:
                if form.discord_bot_token.data: Setting.set('DISCORD_BOT_TOKEN', form.discord_bot_token.data, SettingValueType.SECRET)
                Setting.set('DISCORD_MONITORED_ROLE_ID', form.discord_monitored_role_id.data or Setting.get('DISCORD_MONITORED_ROLE_ID', ""), SettingValueType.STRING)
                Setting.set('DISCORD_THREAD_CHANNEL_ID', form.discord_thread_channel_id.data or Setting.get('DISCORD_THREAD_CHANNEL_ID', ""), SettingValueType.STRING)
                Setting.set('DISCORD_BOT_LOG_CHANNEL_ID', form.discord_bot_log_channel_id.data or Setting.get('DISCORD_BOT_LOG_CHANNEL_ID', ""), SettingValueType.STRING)
                if not require_guild_membership_from_form:
                    Setting.set('DISCORD_SERVER_INVITE_URL', form.discord_server_invite_url.data or Setting.get('DISCORD_SERVER_INVITE_URL', ""), SettingValueType.STRING)
                Setting.set('DISCORD_BOT_WHITELIST_SHARERS', form.discord_bot_whitelist_sharers.data, SettingValueType.BOOLEAN)
                log_event(EventType.DISCORD_CONFIG_SAVE, "Discord settings updated (Bot Enabled).", admin_id=current_user.id)
            else: 
                if form.discord_bot_token.data:
                    Setting.set('DISCORD_BOT_TOKEN', "", SettingValueType.SECRET)
                Setting.set('DISCORD_BOT_WHITELIST_SHARERS', form.discord_bot_whitelist_sharers.data, SettingValueType.BOOLEAN)
                log_event(EventType.DISCORD_CONFIG_SAVE, "Discord settings updated (Bot Disabled).", admin_id=current_user.id)

            db.session.commit() # A single commit at the end to save grandfathered invites and settings
            flash('Discord settings saved successfully.', 'success')
            return redirect(url_for('dashboard.settings_discord'))

    if request.method == 'GET':
        is_oauth_enabled_db = Setting.get_bool('DISCORD_OAUTH_ENABLED', False)
        form.enable_discord_oauth.data = is_oauth_enabled_db
        if is_oauth_enabled_db:
            form.discord_client_id.data = Setting.get('DISCORD_CLIENT_ID')
            form.discord_oauth_auth_url.data = Setting.get('DISCORD_OAUTH_AUTH_URL')
        
        is_bot_enabled_db = Setting.get_bool('DISCORD_BOT_ENABLED', False)
        form.enable_discord_bot.data = is_bot_enabled_db

        if is_oauth_enabled_db:
            if is_bot_enabled_db:
                form.discord_bot_require_sso_on_invite.data = True
            else:
                form.discord_bot_require_sso_on_invite.data = Setting.get_bool('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False)
            form.discord_require_guild_membership.data = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)
        else:
            form.discord_bot_require_sso_on_invite.data = False
            form.discord_require_guild_membership.data = False
            
        form.discord_guild_id.data = Setting.get('DISCORD_GUILD_ID')
        form.discord_server_invite_url.data = Setting.get('DISCORD_SERVER_INVITE_URL')
        
        if is_bot_enabled_db:
            form.discord_monitored_role_id.data = Setting.get('DISCORD_MONITORED_ROLE_ID')
            form.discord_thread_channel_id.data = Setting.get('DISCORD_THREAD_CHANNEL_ID')
            form.discord_bot_log_channel_id.data = Setting.get('DISCORD_BOT_LOG_CHANNEL_ID')
        form.discord_bot_whitelist_sharers.data = Setting.get_bool('DISCORD_BOT_WHITELIST_SHARERS', False)
            
    return render_template('settings/index.html', 
                           title="Discord Settings", 
                           form=form,
                           active_tab='discord',
                           discord_invite_redirect_uri=discord_invite_redirect_uri_generated,
                           discord_admin_link_redirect_uri=discord_admin_link_redirect_uri_generated,
                           discord_admin_linked=discord_admin_linked,
                           discord_admin_user_info=discord_admin_user_info,
                           initial_discord_enabled_state=initial_oauth_enabled_for_admin_link_section)

@bp.route('/settings/advanced', methods=['GET'])
@login_required
@setup_required
@permission_required('manage_advanced_settings')
def settings_advanced():
    all_db_settings = Setting.query.order_by(Setting.key).all()
    return render_template('settings/index.html', title="Advanced Settings", active_tab='advanced', all_db_settings=all_db_settings)

@bp.route('/settings/plugins', methods=['GET'])
@login_required
@setup_required
@permission_required('manage_plugins')
def settings_plugins():
    # This route now just renders the main settings layout.
    # The content will be loaded via the partial included in settings/index.html
    
    # Refresh plugin servers count to ensure accuracy
    try:
        from app.models_media_services import MediaServer, ServiceType
        from app.models_plugins import Plugin
        
        plugins = Plugin.query.all()
        for plugin in plugins:
            try:
                # Find the corresponding ServiceType enum value
                service_type = None
                for st in ServiceType:
                    if st.value == plugin.plugin_id:
                        service_type = st
                        break
                
                if service_type:
                    # Count actual servers
                    actual_count = MediaServer.query.filter_by(service_type=service_type).count()
                    if plugin.servers_count != actual_count:
                        current_app.logger.debug(f"Updating plugin {plugin.plugin_id} servers_count from {plugin.servers_count} to {actual_count}")
                        plugin.servers_count = actual_count
                        db.session.add(plugin)
                else:
                    # For plugins without corresponding ServiceType, set to 0
                    if plugin.servers_count != 0:
                        current_app.logger.debug(f"Setting plugin {plugin.plugin_id} servers_count to 0 (no ServiceType)")
                        plugin.servers_count = 0
                        db.session.add(plugin)
            except Exception as e:
                current_app.logger.error(f"Error updating servers_count for plugin {plugin.plugin_id}: {e}")
        
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error refreshing plugin servers count in settings: {e}")
    
    available_plugins = plugin_manager.get_available_plugins()
    enabled_plugins = [p.plugin_id for p in plugin_manager.get_enabled_plugins()]

    return render_template(
        'settings/index.html',
        title="Plugin Manager",
        available_plugins=available_plugins,
        enabled_plugins=enabled_plugins,
        active_tab='plugins'
    )

@bp.route('/settings/plugins/<plugin_id>', methods=['GET'])
@login_required
@setup_required
@permission_required('manage_plugins')
def settings_plugin_configure(plugin_id):
    from app.models_plugins import Plugin
    from app.models_media_services import MediaServer, ServiceType, UserMediaAccess
    from app.services.media_service_factory import MediaServiceFactory
    import traceback

    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('dashboard.settings_plugins'))

    servers = MediaServer.query.filter_by(service_type=service_type_enum).all()
    
    servers_with_details = []
    for server in servers:
        member_count = UserMediaAccess.query.filter_by(server_id=server.id).count()
        server_details = {
            'server': server,
            'member_count': member_count,
            'libraries': [],
            'error': None
        }
        try:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                # Get libraries
                try:
                    libs = service.get_libraries()
                    server_details['libraries'] = [lib.get('name', 'Unknown Library') for lib in libs] if libs else []
                except Exception as e:
                    current_app.logger.error(f"Error getting libraries for server {server.name}: {e}")
                    server_details['error'] = "Could not fetch libraries."
            else:
                server_details['error'] = "Could not create media service."
        except Exception as e:
            current_app.logger.error(f"Error creating service for server {server.name}: {e}\n{traceback.format_exc()}")
            server_details['error'] = "Failed to connect to server."
            
        servers_with_details.append(server_details)

    return render_template(
        'settings/index.html',
        title=f"Configure {plugin.name}",
        plugin=plugin,
        servers_with_details=servers_with_details, # Pass new list to template
        active_tab='plugin_configure'
    )

@bp.route('/settings/plugins/<plugin_id>/<int:server_id>/edit', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def settings_plugin_edit_server(plugin_id, server_id):
    from app.models_plugins import Plugin
    from app.models_media_services import MediaServer, ServiceType
    from app.forms import MediaServerForm

    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('dashboard.settings_plugins'))

    server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first_or_404()
    
    form = MediaServerForm(server_id=server.id, obj=server)
    form.service_type.data = server.service_type.value
    
    if form.validate_on_submit():
        try:
            # Update server
            server.name = form.name.data
            server.url = form.url.data.rstrip('/')
            server.api_key = form.api_key.data
            server.username = form.username.data
            if form.password.data:  # Only update password if provided
                server.password = form.password.data
            server.is_active = form.is_active.data
            
            db.session.commit()
            
            log_event(
                EventType.SETTING_CHANGE,
                f"Updated media server: {server.name}",
                admin_id=current_user.id
            )
            
            flash(f'Media server "{server.name}" updated successfully!', 'success')
            return redirect(url_for('dashboard.settings_plugin_configure', plugin_id=plugin_id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating media server: {e}")
            flash(f'Error updating server: {str(e)}', 'danger')
    
    return render_template(
        'settings/index.html',
        title=f"Edit {server.name}",
        plugin=plugin,
        server=server,
        form=form,
        active_tab='plugin_edit_server'
    )



@bp.route('/settings/regenerate_secret_key', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_advanced_settings')
def regenerate_secret_key():
    try:
        new_secret_key = secrets.token_hex(32)
        Setting.set('SECRET_KEY', new_secret_key, SettingValueType.SECRET, "Application Secret Key"); current_app.config['SECRET_KEY'] = new_secret_key
        admin_id_for_log = current_user.id if current_user and current_user.is_authenticated else None 
        logout_user() # User's session is now invalid due to new secret key
        log_event(EventType.SETTING_CHANGE, "Application SECRET_KEY re-generated by admin.", admin_id=admin_id_for_log)
        # Flash message might not be seen if user is immediately logged out and redirected by Flask-Login
        # flash('SECRET_KEY re-generated. All users (including you) have been logged out.', 'success') 
        
        # For HTMX, explicitly tell client to redirect to login
        if request.headers.get('HX-Request'):
            response = make_response('<div class="alert alert-success p-2">SECRET_KEY re-generated. You will be logged out. Refreshing...</div>')
            response.headers['HX-Refresh'] = 'true' # Or HX-Redirect to login page
            return response
        return redirect(url_for('auth.app_login')) # Redirect to login for standard request
    except Exception as e:
        current_app.logger.error(f"Error regenerating SECRET_KEY: {e}")
        log_event(EventType.ERROR_GENERAL, f"Failed to re-generate SECRET_KEY: {str(e)}", admin_id=current_user.id if current_user and current_user.is_authenticated else None)
        flash(f'Error re-generating SECRET_KEY: {e}', 'danger')
        if request.headers.get('HX-Request'): return f'<div class="alert alert-error p-2">Error: {e}</div>', 500
        return redirect(url_for('dashboard.settings_advanced'))
    
@bp.route('/settings/logs/clear', methods=['POST'])
@login_required
@setup_required
@permission_required('clear_logs')
def clear_logs_route():
    event_types_selected = request.form.getlist('event_types_to_clear[]')
    clear_all = request.form.get('clear_all_types') == 'true'
    
    current_app.logger.info(f"Dashboard.py - clear_logs_route(): Received request to clear logs. Selected types: {event_types_selected}, Clear All: {clear_all}")

    types_to_delete_in_service = None
    if not clear_all and event_types_selected:
        types_to_delete_in_service = event_types_selected
    elif clear_all:
        types_to_delete_in_service = None
    else: 
        toast_message = "No event types selected to clear. No logs were deleted."
        toast_category = "info"
        # flash(toast_message, toast_category) # <<< REMOVE/COMMENT OUT if you don't want session flash
        
        response = make_response("") 
        trigger_payload = json.dumps({"showToastEvent": {"message": toast_message, "category": toast_category}})
        response.headers['HX-Trigger-After-Swap'] = trigger_payload 
        # Also trigger list refresh, though nothing changed
        refresh_trigger_payload = json.dumps({"refreshHistoryList": True})
        existing_trigger = response.headers.get('HX-Trigger-After-Swap')
        if existing_trigger:
            try:
                data = json.loads(existing_trigger)
                data.update(json.loads(refresh_trigger_payload))
                response.headers['HX-Trigger-After-Swap'] = json.dumps(data)
            except json.JSONDecodeError:
                 response.headers['HX-Trigger-After-Swap'] = refresh_trigger_payload # fallback
        else:
            response.headers['HX-Trigger-After-Swap'] = refresh_trigger_payload
        
        return response, 200 # Send 200 OK as an action was processed (even if no-op)

    toast_message = ""
    toast_category = "info"
    try:
        cleared_count = history_service.clear_history_logs(
            event_types_to_clear=types_to_delete_in_service,
            admin_id=current_user.id
        )
        toast_message = f"Successfully cleared {cleared_count} history log entries."
        toast_category = "success"
        # flash(toast_message, toast_category) # <<< REMOVE/COMMENT OUT if you don't want session flash
        # The log_event in history_service already records this action.
        current_app.logger.info(f"Dashboard.py - clear_logs_route(): {toast_message}")

    except Exception as e:
        current_app.logger.error(f"Dashboard.py - clear_logs_route(): Failed to clear history: {e}", exc_info=True)
        toast_message = f"Error clearing history logs: {str(e)}"
        toast_category = "danger"
        # flash(toast_message, toast_category) # <<< REMOVE/COMMENT OUT if you don't want session flash
        # The log_event in history_service (if it has an error case) or a new one here could record.

    response_content_for_form = "" 
    response = make_response(response_content_for_form)
    
    triggers = {}
    if toast_message:
        triggers["showToastEvent"] = {"message": toast_message, "category": toast_category}
    
    triggers["refreshHistoryList"] = True # Always refresh the list after attempting a clear
    
    response.headers['HX-Trigger-After-Swap'] = json.dumps(triggers)
    current_app.logger.debug(f"Dashboard.py - clear_logs_route(): Sending HX-Trigger-After-Swap: {response.headers['HX-Trigger-After-Swap']}")

    return response

@bp.route('/streaming')
@login_required
@setup_required
@permission_required('view_streaming')
def streaming_sessions():
    # Fetch the session monitoring interval from settings
    default_interval = current_app.config.get('SESSION_MONITORING_INTERVAL_SECONDS', 30) # Default fallback
    try:
        interval_seconds_str = Setting.get('SESSION_MONITORING_INTERVAL_SECONDS', str(default_interval))
        # Ensure it's a valid integer, otherwise use a sensible default for the template
        streaming_refresh_interval_seconds = int(interval_seconds_str)
        if streaming_refresh_interval_seconds < 5: # Enforce a minimum reasonable refresh interval for UI
            current_app.logger.warning(f"Streaming page refresh interval ({streaming_refresh_interval_seconds}s) is too low, defaulting to 5s for UI.")
            streaming_refresh_interval_seconds = 5 
    except ValueError:
        current_app.logger.warning(f"Invalid SESSION_MONITORING_INTERVAL_SECONDS ('{interval_seconds_str}') in settings. Using default {default_interval}s for streaming page refresh.")
        streaming_refresh_interval_seconds = default_interval
    except Exception as e_setting:
        current_app.logger.error(f"Error fetching SESSION_MONITORING_INTERVAL_SECONDS: {e_setting}. Using default {default_interval}s.")
        streaming_refresh_interval_seconds = default_interval


    current_app.logger.debug(f"Streaming page will use refresh interval: {streaming_refresh_interval_seconds} seconds.")
    
    return render_template('streaming/index.html', 
                           title="Active Streams", 
                           streaming_refresh_interval=streaming_refresh_interval_seconds)

@bp.route('/streaming/partial')
@login_required
@setup_required
@permission_required('view_streaming')
def streaming_sessions_partial():
    view_mode = request.args.get('view', 'merged')
    
    active_sessions_data = []
    sessions_by_server = {}  # For categorized view
    sessions_by_service = {}  # For service view
    summary_stats = {
        "total_streams": 0,
        "direct_play_count": 0,
        "transcode_count": 0,
        "total_bandwidth_mbps": 0.0,
        "lan_bandwidth_mbps": 0.0,
        "wan_bandwidth_mbps": 0.0
    }

    def get_standard_resolution(height_str):
        if not height_str: return "SD"
        try:
            height = int(height_str)
            if height <= 240: return "240p"
            if height <= 360: return "360p"
            if height <= 480: return "480p"
            if height <= 576: return "576p"
            if height <= 720: return "720p"
            if height <= 1080: return "1080p"
            if height <= 1440: return "1440p"
            if height <= 2160: return "4K"
            return f"{height}p"
        except (ValueError, TypeError):
            return "SD"

    try:
        raw_sessions_from_all_services = MediaServiceManager.get_all_active_sessions()
        
        if raw_sessions_from_all_services:
            summary_stats["total_streams"] = len(raw_sessions_from_all_services)
            
            # Collect user IDs from both Plex and Jellyfin sessions
            user_ids_in_session_for_query = set()
            for rs in raw_sessions_from_all_services:
                # Plex sessions have rs.user.id, Jellyfin sessions have UserId
                if hasattr(rs, 'user') and rs.user and hasattr(rs.user, 'id'):
                    user_ids_in_session_for_query.add(int(rs.user.id))
                elif isinstance(rs, dict) and rs.get('UserId'):
                    # For Jellyfin, we need to find the user by jellyfin user ID
                    pass  # We'll handle this differently below
            
            mum_users_map_by_plex_id = {u.plex_user_id: u for u in User.query.filter(User.plex_user_id.in_(list(user_ids_in_session_for_query)))} if user_ids_in_session_for_query else {}
            
            # Also get users by primary_username for Jellyfin sessions
            jellyfin_usernames = set()
            for rs in raw_sessions_from_all_services:
                if isinstance(rs, dict) and rs.get('UserName'):
                    jellyfin_usernames.add(rs.get('UserName'))
            
            mum_users_map_by_username = {u.primary_username: u for u in User.query.filter(User.primary_username.in_(list(jellyfin_usernames)))} if jellyfin_usernames else {}

            for raw_session in raw_sessions_from_all_services:
                # Determine if this is a Plex or Jellyfin session
                is_plex_session = hasattr(raw_session, 'user') and hasattr(raw_session, 'sessionKey')
                is_jellyfin_session = isinstance(raw_session, dict) and 'UserId' in raw_session
                
                if is_plex_session:
                    # Handle Plex session format
                    user_name = getattr(raw_session.user, 'title', 'Unknown User')
                    player = raw_session.player
                    player_title = getattr(player, 'title', 'Unknown Player')
                    player_platform = getattr(player, 'platform', '')
                    product = getattr(player, 'product', 'N/A')
                    media_title = getattr(raw_session, 'title', "Unknown Title")
                    media_type = getattr(raw_session, 'type', 'unknown').capitalize()
                    year = getattr(raw_session, 'year', None)
                    library_name = getattr(raw_session, 'librarySectionTitle', "N/A")
                    progress = (raw_session.viewOffset / raw_session.duration) * 100 if raw_session.duration else 0
                    thumb_path = raw_session.thumb
                    if media_type == 'Episode' and hasattr(raw_session, 'grandparentThumb'):
                        thumb_path = raw_session.grandparentThumb
                    thumb_url = url_for('api.plex_image_proxy', path=thumb_path.lstrip('/')) if thumb_path else None
                    transcode_session = raw_session.transcodeSession
                    is_transcoding = transcode_session is not None
                    
                    location_ip = getattr(player, 'address', 'N/A')
                    is_lan = getattr(player, 'local', False)
                    location_lan_wan = "LAN" if is_lan else "WAN"
                    mum_user = mum_users_map_by_plex_id.get(int(raw_session.user.id))
                    mum_user_id = mum_user.id if mum_user else None
                    session_key = raw_session.sessionKey
                    
                    # Generate Plex user avatar URL if available
                    user_avatar_url = None
                    if hasattr(raw_session.user, 'thumb') and raw_session.user.thumb:
                        try:
                            user_avatar_url = url_for('api.plex_image_proxy', path=raw_session.user.thumb.lstrip('/'))
                        except Exception as e:
                            current_app.logger.debug(f"Could not generate Plex avatar URL: {e}")
                            user_avatar_url = None
                    
                elif is_jellyfin_session:
                    # Handle Jellyfin session format
                    user_name = raw_session.get('UserName', 'Unknown User')
                    now_playing = raw_session.get('NowPlayingItem', {})
                    play_state = raw_session.get('PlayState', {})
                    
                    player_title = raw_session.get('DeviceName', 'Unknown Device')
                    player_platform = raw_session.get('Client', '')
                    product = raw_session.get('ApplicationVersion', 'N/A')
                    media_title = now_playing.get('Name', "Unknown Title")
                    media_type = now_playing.get('Type', 'unknown').capitalize()
                    year = now_playing.get('ProductionYear', None)
                    library_name = "Library"  # Generic library name for Jellyfin
                    
                    # Calculate progress for Jellyfin
                    position_ticks = play_state.get('PositionTicks', 0)
                    runtime_ticks = now_playing.get('RunTimeTicks', 0)
                    progress = (position_ticks / runtime_ticks) * 100 if runtime_ticks else 0
                    
                    # Handle Jellyfin thumbnails
                    thumb_url = None
                    item_id = now_playing.get('Id')
                    if item_id:
                        # For episodes, prefer series poster; for movies, use primary image
                        if media_type == 'Episode' and now_playing.get('SeriesId'):
                            thumb_url = url_for('api.jellyfin_image_proxy', item_id=now_playing.get('SeriesId'), image_type='Primary')
                            current_app.logger.info(f"Generated Jellyfin episode thumbnail URL: {thumb_url}")
                        else:
                            thumb_url = url_for('api.jellyfin_image_proxy', item_id=item_id, image_type='Primary')
                            current_app.logger.info(f"Generated Jellyfin movie thumbnail URL: {thumb_url} for item_id: {item_id}")
                    
                    is_transcoding = play_state.get('PlayMethod') == 'Transcode'
                    
                    location_ip = raw_session.get('RemoteEndPoint', 'N/A')
                    is_lan = not raw_session.get('IsLocal', True)  # Jellyfin logic might be inverted
                    location_lan_wan = "LAN" if is_lan else "WAN"
                    
                    # Find MUM user by username for Jellyfin
                    mum_user = mum_users_map_by_username.get(user_name)
                    mum_user_id = mum_user.id if mum_user else None
                    session_key = raw_session.get('Id', '')
                    
                    # Generate Jellyfin user avatar URL if available
                    user_avatar_url = None
                    jellyfin_user_id = raw_session.get('UserId')
                    if jellyfin_user_id:
                        try:
                            # Generate Jellyfin user avatar URL - the API route will handle checking for PrimaryImageTag
                            user_avatar_url = url_for('api.jellyfin_user_avatar_proxy', user_id=jellyfin_user_id)
                        except Exception as e:
                            current_app.logger.debug(f"Could not generate Jellyfin avatar URL for user {jellyfin_user_id}: {e}")
                            user_avatar_url = None
                    
                else:
                    # Skip unknown session formats
                    current_app.logger.warning(f"Unknown session format: {type(raw_session)}")
                    continue

                # Initialize details
                quality_detail = ""
                stream_details = ""
                video_detail = ""
                audio_detail = ""
                subtitle_detail = "None"
                container_detail = ""
                
                # Handle session details based on service type
                if is_plex_session:
                    # Find original and transcoded media parts and streams for Plex
                    original_media = next((m for m in raw_session.media if not m.selected), raw_session.media[0])
                    original_media_part = original_media.parts[0]
                    original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                    original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)

                    # Determine stream type for Plex
                    if is_transcoding:
                        # For transcodes, the session data is for the *output* stream.
                        # We need to fetch the original item to get the source quality.
                        try:
                            full_media_item = raw_session._server.fetchItem(raw_session.ratingKey)
                            original_media_part = full_media_item.media[0].parts[0]
                            original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                            original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)
                        except Exception as e:
                            current_app.logger.error(f"Could not fetch full media item for transcode session: {e}")
                            # Fallback to the potentially inaccurate session data
                            original_media_part = next((p for m in raw_session.media for p in m.parts if not p.selected), raw_session.media[0].parts[0])
                            original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                            original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)

                    speed = f"(Speed: {transcode_session.speed:.1f})" if transcode_session.speed is not None else ""
                    status = "Throttled" if transcode_session.throttled else ""
                    stream_details = f"Transcode {status} {speed}".strip()
                    
                    # Container
                    original_container = original_media_part.container.upper() if original_media_part else 'N/A'
                    transcoded_container = transcode_session.container.upper()
                    container_detail = f"Converting ({original_container} \u2192 {transcoded_container})"

                    # Video
                    original_res = get_standard_resolution(original_video_stream.height) if original_video_stream else "Unknown"
                    transcoded_res = get_standard_resolution(transcode_session.height)
                    if transcode_session.videoDecision == "copy":
                        video_detail = f"Direct Stream ({original_video_stream.codec.upper()} {original_res})"
                    else:
                        video_detail = f"Transcode ({original_video_stream.codec.upper()} {original_res} \u2192 {transcode_session.videoCodec.upper()} {transcoded_res})"

                    # Audio
                    if transcode_session.audioDecision == "copy":
                        audio_detail = f"Direct Stream ({original_audio_stream.displayTitle})"
                    else:
                        original_audio_display = original_audio_stream.displayTitle if original_audio_stream else "Unknown"
                        audio_channel_layout_map = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
                        transcoded_channel_layout = audio_channel_layout_map.get(transcode_session.audioChannels, f"{transcode_session.audioChannels}ch")
                        transcoded_audio_display = f"{transcode_session.audioCodec.upper()} {transcoded_channel_layout}"
                        audio_detail = f"Transcode ({original_audio_display} \u2192 {transcoded_audio_display})"

                    # Subtitle
                    selected_subtitle_stream = next((s for m in raw_session.media for p in m.parts for s in p.streams if s.streamType == 3 and s.selected), None)
                    if transcode_session.subtitleDecision == "transcode":
                        if selected_subtitle_stream:
                            lang = selected_subtitle_stream.language or "Unknown"
                            # The 'format' attribute seems to reliably hold the destination container (e.g., 'ass', 'srt')
                            dest_format = (getattr(selected_subtitle_stream, 'format', '???') or '???').upper()
                            display_title = selected_subtitle_stream.displayTitle
                            match = re.search(r'\((.*?)\)', display_title)
                            if match:
                                # Extracts "SRT" from "English (SRT)"
                                original_format = match.group(1).upper()
                            else:
                                original_format = '???'
                            
                            if original_format != dest_format and dest_format != '???':
                                subtitle_detail = f"Transcode ({lang} - {original_format} → {dest_format})"
                            else:
                                # Fallback to a simpler display if formats match or dest is unknown
                                subtitle_detail = f"Transcode ({display_title})"
                        else:
                            subtitle_detail = "Transcode (Unknown)"
                    elif transcode_session.subtitleDecision == "copy":
                        if selected_subtitle_stream:
                            subtitle_detail = f"Direct Stream ({selected_subtitle_stream.displayTitle})"
                        else:
                            subtitle_detail = "Direct Stream (Unknown)"

                    # Quality
                    transcoded_media = next((m for m in raw_session.media if m.selected), None)
                    quality_res = get_standard_resolution(getattr(transcoded_media, 'height', transcode_session.height))
                    if transcoded_media:
                        quality_detail = f"{quality_res} ({transcoded_media.bitrate / 1000:.1f} Mbps)"
                    else:
                        quality_detail = f"{quality_res} (Bitrate N/A)"

                elif is_plex_session and not is_transcoding:
                    # Plex Direct Play
                    stream_details = "Direct Play"
                    if any(p.decision == 'transcode' for m in raw_session.media for p in m.parts):
                        stream_details = "Direct Stream"

                    original_res = get_standard_resolution(original_video_stream.height) if original_video_stream else "Unknown"
                    container_detail = original_media_part.container.upper()
                    video_detail = f"Direct Play ({original_video_stream.codec.upper()} {original_res})" if original_video_stream else "Direct Play (Unknown Video)"
                    audio_detail = f"Direct Play ({original_audio_stream.displayTitle})" if original_audio_stream else "Direct Play (Unknown Audio)"
                    
                    selected_subtitle_stream = next((s for m in raw_session.media for p in m.parts for s in p.streams if s.streamType == 3 and s.selected), None)
                    if selected_subtitle_stream:
                        subtitle_detail = f"Direct Play ({selected_subtitle_stream.displayTitle})"

                    quality_detail = f"Original ({original_media.bitrate / 1000:.1f} Mbps)"

                else:
                    # Jellyfin session handling (simplified)
                    if is_transcoding:
                        stream_details = "Transcode"
                        container_detail = "Converting"
                        video_detail = "Transcode"
                        audio_detail = "Transcode"
                        quality_detail = "Transcoding"
                    else:
                        stream_details = "Direct Play"
                        container_detail = now_playing.get('Container', 'Unknown').upper()
                        video_detail = "Direct Play"
                        audio_detail = "Direct Play"
                        quality_detail = "Direct Play"

                # Prepare raw data for modal
                raw_session_dict = {}
                if is_plex_session:
                    if hasattr(raw_session, '_data') and raw_session._data is not None:
                        raw_xml_string = ET.tostring(raw_session._data, encoding='unicode')
                        raw_session_dict = xmltodict.parse(raw_xml_string)
                elif is_jellyfin_session:
                    raw_session_dict = raw_session  # Jellyfin sessions are already dict format
                
                raw_json_string = json.dumps(raw_session_dict, indent=2)

                # Get additional details based on session type
                if is_plex_session:
                    grandparent_title = getattr(raw_session, 'grandparentTitle', None)
                    parent_title = getattr(raw_session, 'parentTitle', None)
                    player_state = getattr(raw_session.player, 'state', 'N/A').capitalize()
                    bitrate_calc = raw_session.media[0].bitrate if raw_session.media else 0
                else:
                    grandparent_title = now_playing.get('SeriesName', None)
                    parent_title = now_playing.get('SeasonName', None)
                    player_state = 'Playing' if not play_state.get('IsPaused', False) else 'Paused'
                    bitrate_calc = 0  # Jellyfin bitrate calculation would need more work

                session_details = {
                    'user': user_name, 'mum_user_id': mum_user_id, 'player_title': player_title,
                    'player_platform': player_platform, 'product': product, 'media_title': media_title,
                    'grandparent_title': grandparent_title,
                    'parent_title': parent_title, 'media_type': media_type,
                    'library_name': library_name, 'year': year, 'state': player_state,
                    'progress': round(progress, 1), 'thumb_url': thumb_url, 'session_key': session_key,
                    'user_avatar_url': user_avatar_url,  # Add user avatar URL
                    'quality_detail': quality_detail, 'stream_detail': stream_details,
                    'container_detail': container_detail,
                    'video_detail': video_detail, 'audio_detail': audio_detail, 'subtitle_detail': subtitle_detail,
                    'location_detail': f"{location_lan_wan}: {location_ip}", 'is_public_ip': not is_lan,
                    'location_ip': location_ip, 'bandwidth_detail': f"Streaming via {location_lan_wan}",
                    'bitrate_calc': bitrate_calc, 'location_type_calc': location_lan_wan,
                    'is_transcode_calc': is_transcoding,
                    'raw_data_json': raw_json_string,
                    'raw_data_json_lines': raw_json_string.splitlines()
                }
                active_sessions_data.append(session_details)

                # For categorized and service views, group sessions
                if view_mode == 'categorized':
                    # Get the actual server name from the session data
                    server_name = getattr(raw_session, 'server_name', None)
                    
                    # Get the actual server name from the server itself (not the custom name)
                    actual_server_name = None
                    if is_plex_session:
                        # For Plex, try to get the server name from the session
                        actual_server_name = getattr(raw_session, 'machineIdentifier', None)
                        if hasattr(raw_session, '_server') and hasattr(raw_session._server, 'friendlyName'):
                            actual_server_name = raw_session._server.friendlyName
                    else:
                        # For Jellyfin, get the server name from the session data
                        actual_server_name = raw_session.get('ServerName', None)
                    
                    if not server_name:
                        # Fallback to service type if server_name not available
                        if is_plex_session:
                            server_name = "Plex Server"
                        else:
                            server_name = "Jellyfin Server"
                    if server_name not in sessions_by_server:
                        sessions_by_server[server_name] = {
                            'sessions': [],
                            'actual_server_name': actual_server_name,
                            'stats': {
                                "total_streams": 0,
                                "direct_play_count": 0,
                                "transcode_count": 0,
                                "total_bandwidth_mbps": 0.0,
                                "lan_bandwidth_mbps": 0.0,
                                "wan_bandwidth_mbps": 0.0
                            }
                        }
                    
                    sessions_by_server[server_name]['sessions'].append(session_details)
                    sessions_by_server[server_name]['stats']['total_streams'] += 1

                # For service view, group by service type
                elif view_mode == 'service':
                    # Determine service type from session
                    if is_plex_session:
                        service_name = "Plex"
                        service_type = "plex"
                    elif is_jellyfin_session:
                        service_name = "Jellyfin"
                        service_type = "jellyfin"
                    else:
                        service_name = "Unknown Service"
                        service_type = "unknown"
                    
                    if service_name not in sessions_by_service:
                        sessions_by_service[service_name] = {
                            'sessions': [],
                            'service_type': service_type,
                            'stats': {
                                "total_streams": 0,
                                "direct_play_count": 0,
                                "transcode_count": 0,
                                "total_bandwidth_mbps": 0.0,
                                "lan_bandwidth_mbps": 0.0,
                                "wan_bandwidth_mbps": 0.0
                            }
                        }
                    
                    sessions_by_service[service_name]['sessions'].append(session_details)
                    sessions_by_service[service_name]['stats']['total_streams'] += 1

                if is_transcoding:
                    summary_stats["transcode_count"] += 1
                    if view_mode == 'categorized' and server_name in sessions_by_server:
                        sessions_by_server[server_name]['stats']['transcode_count'] += 1
                    elif view_mode == 'service' and service_name in sessions_by_service:
                        sessions_by_service[service_name]['stats']['transcode_count'] += 1
                else:
                    summary_stats["direct_play_count"] += 1
                    if view_mode == 'categorized' and server_name in sessions_by_server:
                        sessions_by_server[server_name]['stats']['direct_play_count'] += 1
                    elif view_mode == 'service' and service_name in sessions_by_service:
                        sessions_by_service[service_name]['stats']['direct_play_count'] += 1
                
                # Bandwidth Calculation (moved to be unconditional)
                if is_plex_session:
                    bitrate_kbps = getattr(raw_session.session, 'bandwidth', 0)
                else:
                    bitrate_kbps = 0  # Jellyfin bandwidth calculation would need different approach
                bitrate_mbps = (bitrate_kbps or 0) / 1000
                summary_stats["total_bandwidth_mbps"] += bitrate_mbps
                if is_lan:
                    summary_stats["lan_bandwidth_mbps"] += bitrate_mbps
                else:
                    summary_stats["wan_bandwidth_mbps"] += bitrate_mbps

                # Update server-specific stats for categorized view
                if view_mode == 'categorized' and server_name in sessions_by_server:
                    sessions_by_server[server_name]['stats']['total_bandwidth_mbps'] += bitrate_mbps
                    if is_lan:
                        sessions_by_server[server_name]['stats']['lan_bandwidth_mbps'] += bitrate_mbps
                    else:
                        sessions_by_server[server_name]['stats']['wan_bandwidth_mbps'] += bitrate_mbps
                
                # Update service-specific stats for service view
                elif view_mode == 'service' and service_name in sessions_by_service:
                    sessions_by_service[service_name]['stats']['total_bandwidth_mbps'] += bitrate_mbps
                    if is_lan:
                        sessions_by_service[service_name]['stats']['lan_bandwidth_mbps'] += bitrate_mbps
                    else:
                        sessions_by_service[service_name]['stats']['wan_bandwidth_mbps'] += bitrate_mbps

        # Round bandwidth values
        summary_stats["total_bandwidth_mbps"] = round(summary_stats["total_bandwidth_mbps"], 1)
        summary_stats["lan_bandwidth_mbps"] = round(summary_stats["lan_bandwidth_mbps"], 1)
        summary_stats["wan_bandwidth_mbps"] = round(summary_stats["wan_bandwidth_mbps"], 1)

        # Round server-specific bandwidth values
        if view_mode == 'categorized':
            for server_data in sessions_by_server.values():
                server_data['stats']['total_bandwidth_mbps'] = round(server_data['stats']['total_bandwidth_mbps'], 1)
                server_data['stats']['lan_bandwidth_mbps'] = round(server_data['stats']['lan_bandwidth_mbps'], 1)
                server_data['stats']['wan_bandwidth_mbps'] = round(server_data['stats']['wan_bandwidth_mbps'], 1)
        
        # Round service-specific bandwidth values
        elif view_mode == 'service':
            for service_data in sessions_by_service.values():
                service_data['stats']['total_bandwidth_mbps'] = round(service_data['stats']['total_bandwidth_mbps'], 1)
                service_data['stats']['lan_bandwidth_mbps'] = round(service_data['stats']['lan_bandwidth_mbps'], 1)
                service_data['stats']['wan_bandwidth_mbps'] = round(service_data['stats']['wan_bandwidth_mbps'], 1)

    except Exception as e:
        current_app.logger.error(f"STREAMING_DEBUG: Error during streaming_sessions_partial: {e}", exc_info=True)
    
    if view_mode == 'categorized':
        return render_template('streaming/partials/sessions_categorized.html', 
                               sessions_by_server=sessions_by_server, 
                               summary_stats=summary_stats)
    elif view_mode == 'service':
        return render_template('streaming/partials/sessions_categorized_by_service.html', 
                               sessions_by_service=sessions_by_service, 
                               summary_stats=summary_stats)
    else:
        return render_template('streaming/partials/sessions.html', 
                               sessions=active_sessions_data, 
                               summary_stats=summary_stats)

@bp.route('/settings/admins')
@login_required
@any_permission_required(['create_admin', 'edit_admin', 'delete_admin'])
def settings_admins():
    admins = AdminAccount.query.order_by(AdminAccount.id).all()
    return render_template(
        'settings/index.html',
        title="Manage Admins",
        admins=admins,
        active_tab='admins'
    )

@bp.route('/settings/admins/create', methods=['POST'])
@login_required
@permission_required('create_admin')
def create_admin():
    form = AdminCreateForm()
    if form.validate_on_submit():
        new_admin = AdminAccount(
            username=form.username.data,
            force_password_change=True,
            roles=[] # New admins start with no explicit permissions/roles
        )
        new_admin.set_password(form.password.data)
        db.session.add(new_admin)
        db.session.commit()
        
        toast = {"showToastEvent": {"message": f"Admin '{new_admin.username}' created.", "category": "success"}}
        response = make_response("", 204) # No Content
        response.headers['HX-Trigger'] = json.dumps({"refreshAdminList": True, **toast})
        return response
    
    # If validation fails, re-render the form partial with errors
    return render_template('admins/partials/create_admin_modal.html', form=form), 422

@bp.route('/settings/admins/create_form')
@login_required
@permission_required('create_admin')
def get_admin_create_form():
    form = AdminCreateForm()
    return render_template('admins/partials/create_admin_modal.html', form=form)

@bp.route('/settings/roles/edit/<int:role_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_role')
def edit_role(role_id):
    tab = request.args.get('tab', 'display')
    role = Role.query.get_or_404(role_id)
    form = RoleEditForm(original_name=role.name, obj=role)
    member_form = RoleMemberForm()

    if current_user.id != 1 and current_user in role.admins:
        flash("You cannot edit a role you are currently assigned to.", "danger")
        return redirect(url_for('dashboard.settings_roles'))

    # --- Define the hierarchical permission structure ---
    permissions_structure = {
        'Users': {
            'label': 'Users',
            'children': {
                'view_user': {'label': 'View User', 'description': 'Can view user profile.'},
                'edit_user': {'label': 'Edit User', 'description': 'Can edit user details, notes, whitelists, and library access.'},
                'delete_user': {'label': 'Delete User', 'description': 'Can permanently remove users from MUM and the Plex server.'},
                'purge_users': {'label': 'Purge Users', 'description': 'Can use the inactivity purge feature.'},
                'mass_edit_users': {'label': 'Mass Edit Users', 'description': 'Can perform bulk actions like assigning libraries or whitelisting.'},
            }
        },
        'Invites': {
            'label': 'Invites',
            'children': {
                'create_invites': {'label': 'Create Invites', 'description': 'Can create new invite links.'},
                'delete_invites': {'label': 'Delete Invites', 'description': 'Can delete existing invite links.'},
                'edit_invites': {'label': 'Edit Invites', 'description': 'Can modify settings for existing invites.'},
            }
        },
        'Admins': { 
            'label': 'Admins & Roles', 
            'children': {
                'view_admins_tab': {'label': 'View Admin Management Section', 'description': 'Allows user to see the "Admins" and "Roles" tabs in settings.'},
                'create_admin':    {'label': 'Create Admin', 'description': 'Can create new administrator accounts.'},
                'edit_admin':      {'label': 'Edit Admin', 'description': 'Can edit other administrators. (roles, reset password etc.)'},
                'delete_admin':    {'label': 'Delete Admin', 'description': 'Can delete other non-primary administrators.'},
                'create_role':     {'label': 'Create Role', 'description': 'Can create new administrator roles.'},
                'edit_role':       {'label': 'Edit Role Permissions', 'description': 'Can edit a role\'s name, color, and permissions.'},
                'delete_role':     {'label': 'Delete Roles', 'description': 'Can delete roles that are not in use.'},
            }
        },
        'Streams': {
            'label': 'Streams',
            'children': {
                'view_streaming': {'label': 'View Streams', 'description': 'Can access the "Active Streams" page.'},
                'kill_stream': {'label': 'Terminate Stream', 'description': 'Can stop a user\'s active stream.'},
            }
        },
        'EventLogs': {
            'label': 'Application Logs',
            'children': {
                 'view_logs': {'label': 'View Application Logs', 'description': 'Can access the full "Application Logs" page in settings.'},
                 'clear_logs': {'label': 'Clear Application Logs', 'description': 'Can erase the full "Application Logs".'},
            }
        },
        'AppSettings': {
            'label': 'App Settings',
            'children': {
                'manage_general_settings': {'label': 'Manage General', 'description': 'Can change the application name and base URL.'},
                'manage_plex_settings': {'label': 'Manage Plex', 'description': 'Can change the Plex server connection details.'},
                'manage_discord_settings': {'label': 'Manage Discord', 'description': 'Can change Discord OAuth, Bot, and feature settings.'},
                'manage_plugins': {'label': 'Manage Plugins', 'description': 'Can enable/disable plugins.'},
                'manage_advanced_settings' : {'label': 'Manage Advanced', 'description': 'Can access and manage advanced settings page.'},
            }
        }
    }

    # Flatten the structure to populate the form's choices
    all_permission_choices = []
    for category_data in permissions_structure.values():
        for p_key, p_label in category_data.get('children', {}).items():
            all_permission_choices.append((p_key, p_label))
    form.permissions.choices = all_permission_choices
    
    # Populate choices for the 'Add Members' modal form
    admins_not_in_role = AdminAccount.query.filter(
        AdminAccount.id != 1, 
        ~AdminAccount.roles.any(id=role.id)
    ).order_by(AdminAccount.username).all()
    member_form.admins_to_add.choices = [(a.id, a.username or a.plex_username) for a in admins_not_in_role]

    # Handle form submissions from different tabs
    if request.method == 'POST':
        if 'submit_display' in request.form and form.validate():
            role.name = form.name.data
            role.description = form.description.data
            role.color = form.color.data
            role.icon = form.icon.data.strip()
            db.session.commit()
            flash(f"Display settings for role '{role.name}' updated.", "success")
            return redirect(url_for('dashboard.edit_role', role_id=role_id, tab='display'))
        
        elif 'submit_permissions' in request.form and form.validate():
            # The form.permissions.data will correctly contain all checked permissions
            role.permissions = form.permissions.data
            db.session.commit()
            flash(f"Permissions for role '{role.name}' updated.", "success")
            return redirect(url_for('dashboard.edit_role', role_id=role_id, tab='permissions'))
            
        elif 'submit_add_members' in request.form and member_form.validate_on_submit():
            admins_to_add = AdminAccount.query.filter(AdminAccount.id.in_(member_form.admins_to_add.data)).all()
            if admins_to_add:
                for admin in admins_to_add:
                    if admin not in role.admins:
                        role.admins.append(admin)
                db.session.commit()
                
                # On SUCCESS, send back a trigger for a toast and a list refresh
                toast = {"showToastEvent": {"message": f"Added {len(admins_to_add)} member(s) to role '{role.name}'.", "category": "success"}}
                # Create an empty 204 response because we don't need to swap any content
                response = make_response("", 204)
                # Set the header that HTMX and our JS will listen for
                response.headers['HX-Trigger'] = json.dumps({"refreshMembersList": True, **toast})
                return response

            else:
                # User submitted the form without selecting anyone
                toast = {"showToastEvent": {"message": "No members were selected to be added.", "category": "info"}}
                response = make_response("", 204)
                response.headers['HX-Trigger'] = json.dumps(toast)
                return response

    # Populate form for GET request
    if request.method == 'GET' and tab == 'permissions':
        form.permissions.data = role.permissions

    return render_template(
        'settings/index.html',
        title=f"Edit Role: {role.name}",
        role=role,
        edit_form=form,
        form=form,
        member_form=member_form,
        current_members=role.admins,
        permissions_structure=permissions_structure, # Pass the hierarchy
        active_tab='roles_edit', 
        active_role_tab=tab 
    )

@bp.route('/settings/admins/delete/<int:admin_id>', methods=['POST'])
@login_required
@permission_required('delete_admin')
def delete_admin(admin_id):
    if admin_id == 1 or admin_id == current_user.id:
        flash("The primary admin or your own account cannot be deleted.", "danger")
        return redirect(url_for('dashboard.settings_admins'))
    
    admin_to_delete = AdminAccount.query.get_or_404(admin_id)
    db.session.delete(admin_to_delete)
    db.session.commit()
    flash(f"Admin '{admin_to_delete.username}' has been deleted.", "success")
    return redirect(url_for('dashboard.settings_admins'))

@bp.route('/settings/roles') # This now ONLY lists roles
@login_required
@any_permission_required(['create_role', 'edit_role', 'delete_role'])
def settings_roles():
    roles = Role.query.order_by(Role.name).all()
    return render_template(
        'settings/index.html',
        title="Manage Roles",
        roles=roles,
        active_tab='roles'
    )

@bp.route('/settings/roles/create', methods=['GET', 'POST'])
@login_required
@permission_required('create_role')
def create_role():
    form = RoleCreateForm()
    if form.validate_on_submit():
        new_role = Role(
            name=form.name.data,
            description=form.description.data,
            color=form.color.data,
            icon=form.icon.data.strip()
        )
        db.session.add(new_role)
        db.session.commit()
        
        # --- START MODIFICATION ---
        flash(f"Role '{new_role.name}' created successfully. You can now set its permissions.", "success")
        # Redirect to the 'edit' page for the newly created role
        return redirect(url_for('dashboard.edit_role', role_id=new_role.id))
        # --- END MODIFICATION ---

    # The GET request rendering remains the same, but the template it renders will be changed.
    return render_template(
        'roles/create.html',
        title="Create New Role",
        form=form,
        active_tab='roles' # Keep 'roles' highlighted in the main settings sidebar
    )

@bp.route('/settings/roles/edit/<int:role_id>/remove_member/<int:admin_id>', methods=['POST'])
@login_required
@permission_required('edit_role')
def remove_role_member(role_id, admin_id):
    role = Role.query.get_or_404(role_id)
    admin = AdminAccount.query.get_or_404(admin_id)
    if admin in role.admins:
        role.admins.remove(admin)
        db.session.commit()
        flash(f"Removed '{admin.username}' from role '{role.name}'.", "success")
    # Redirect back to the members tab
    return redirect(url_for('dashboard.edit_role', role_id=role.id, tab='members'))

@bp.route('/settings/roles/delete/<int:role_id>', methods=['POST'])
@login_required
@permission_required('delete_role')
def delete_role(role_id):
    role = Role.query.get_or_404(role_id)

    if current_user.id != 1 and current_user in role.admins:
        flash("You cannot delete a role you are currently assigned to.", "danger")
        return redirect(url_for('dashboard.settings_roles'))
    
    if role.admins:
        flash(f"Cannot delete role '{role.name}' as it is currently assigned to one or more admins.", "danger")
        return redirect(url_for('dashboard.settings_roles'))
    
    db.session.delete(role)
    db.session.commit()
    flash(f"Role '{role.name}' deleted.", "success")
    return redirect(url_for('dashboard.settings_roles'))

@bp.route('/settings/admins/edit/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def edit_admin(admin_id):
    admin = AdminAccount.query.get_or_404(admin_id)

    if admin.id == 1:
        flash("The primary admin's roles and permissions cannot be edited.", "warning")
        return redirect(url_for('dashboard.settings_admins'))
    
    if admin_id == current_user.id:
        flash("To manage your own account, please use the 'My Account' page.", "info")
        return redirect(url_for('dashboard.settings_account'))
        
    form = AdminEditForm(obj=admin)
    form.roles.choices = [(r.id, r.name) for r in Role.query.order_by('name')]

    if form.validate_on_submit():
        admin.roles = Role.query.filter(Role.id.in_(form.roles.data)).all()
        db.session.commit()
        flash(f"Roles for '{admin.username or admin.plex_username}' updated.", "success")
        return redirect(url_for('dashboard.settings_admins'))
        
    if request.method == 'GET':
        form.roles.data = [r.id for r in admin.roles]

    return render_template(
        'admins/edit.html',
        title="Edit Admin",
        admin=admin,
        form=form,
        active_tab='admins'
    )

@bp.route('/settings/admins/reset_password/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def reset_admin_password(admin_id):
    admin = AdminAccount.query.get_or_404(admin_id)
    if admin.id == 1 or admin.id == current_user.id:
        flash("You cannot reset the password for the primary admin or yourself.", "danger")
        return redirect(url_for('dashboard.edit_admin', admin_id=admin_id))
    
    form = AdminResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            admin.set_password(form.new_password.data)
            admin.force_password_change = True # Force change on next login
            db.session.commit()
            
            log_event(EventType.ADMIN_PASSWORD_CHANGE, f"Password was reset for admin '{admin.username}'.", admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('admins/partials/reset_password_modal.html', form=form, admin=admin), 422
    
    # For GET request, just render the form
    return render_template('admins/partials/reset_password_modal.html', form=form, admin=admin)

@bp.route('/libraries')
@login_required
@setup_required
# Optional: Add a new permission check here if desired
# @permission_required('view_libraries')
def libraries():
    all_libraries = []
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    for server in all_servers:
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            try:
                libs = service.get_libraries()
                for lib in libs:
                    lib['server_name'] = server.name
                    lib['service_type'] = server.service_type.value
                all_libraries.extend(libs)
            except Exception as e:
                current_app.logger.error(f"Error getting libraries from {server.name}: {e}")

    return render_template(
        'libraries/index.html',
        title="Libraries",
        libraries=all_libraries
    )

@bp.route('/settings/logs')
@login_required
@setup_required
@permission_required('view_logs') # Renamed permission
def settings_logs():
    # This route now just renders the main settings layout.
    # The content will be loaded via the partial included in settings/index.html
    event_types = list(EventType) 
    return render_template('settings/index.html', 
                           title="Application Logs", 
                           event_types=event_types,
                           active_tab='logs')

@bp.route('/settings/logs/partial')
@login_required
@setup_required
@permission_required('view_logs') # Renamed permission
def settings_logs_partial():
    page = request.args.get('page', 1, type=int)
    session_per_page_key = 'logs_list_per_page' # New session key
    default_per_page = int(current_app.config.get('DEFAULT_HISTORY_PER_PAGE', 20)) # Can keep old config name
    
    per_page_from_request = request.args.get('per_page', type=int)
    if per_page_from_request and per_page_from_request in [20, 50, 100, 200]:
        items_per_page = per_page_from_request
        session[session_per_page_key] = items_per_page
    else:
        items_per_page = session.get(session_per_page_key, default_per_page)
        if items_per_page not in [20, 50, 100, 200]:
            items_per_page = default_per_page
            session[session_per_page_key] = items_per_page

    query = _get_history_logs_query() # This helper function can be reused as is
    logs = query.order_by(HistoryLog.timestamp.desc()).paginate(page=page, per_page=items_per_page, error_out=False)
    event_types = list(EventType) 
    
    # This now renders the new partial for the log list content
    return render_template('settings/partials/logs_list.html', 
                           logs=logs, 
                           event_types=event_types,
                           current_per_page=items_per_page)