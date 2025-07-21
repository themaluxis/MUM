# File: app/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, SelectField, IntegerField, TextAreaField, HiddenField, DateField
from wtforms.validators import DataRequired, EqualTo, Length, Optional, URL, NumberRange, Regexp, ValidationError
from wtforms import SelectMultipleField
from app.models import Setting, AdminAccount # For custom validator if checking existing secrets
from wtforms.widgets import ListWidget, CheckboxInput # <--- ADDED THIS IMPORT
import urllib.parse 
from flask_login import current_user
from datetime import date
from app.utils.timezone_utils import get_all_timezones

def date_not_in_past(form, field):
    if field.data and field.data < date.today():
        raise ValidationError("Date cannot be in the past.")

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign In')

class PlexSSOLoginForm(FlaskForm): # This might just be a button, not a full form if handled by redirect
    submit = SubmitField('Sign In with Plex')

class AccountSetupForm(FlaskForm):
    login_method = SelectField('Admin Account Setup Method', choices=[('plex_sso', 'Sign in with Plex (Recommended)'), ('username_password', 'Create Username and Password')], validators=[DataRequired()])
    username = StringField('Username', validators=[DataRequired(message='Username is required'), Length(min=3, max=80, message='Username must be between 3 and 80 characters')])
    password = PasswordField('Password', validators=[DataRequired(message='Password is required'), Length(min=8, max=128, message='Password must be at least 8 characters')])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(message='Please confirm your password'), EqualTo('password', message='Passwords must match')])
    submit_username_password = SubmitField('Create Admin Account')
    submit_plex_sso = SubmitField('Continue with Plex')

class PlexConfigForm(FlaskForm):
    plex_url = StringField('Plex URL', validators=[DataRequired(), URL(message="Invalid URL format. Include http(s)://")])
    plex_token = StringField('Plex Token', validators=[DataRequired(), Length(min=19, max=24, message="Plex token is usually 19-24 characters long.")])
    connection_tested_successfully = HiddenField(default="false")
    submit = SubmitField('Save Plex Configuration')

class MediaServerForm(FlaskForm):
    name = StringField('Server Name', validators=[DataRequired(), Length(min=1, max=100)])
    service_type = SelectField('Service Type', validators=[DataRequired()])
    url = StringField('Server URL', validators=[DataRequired(), URL(message="Invalid URL format. Include http(s)://")])
    api_key = StringField('API Key/Token', validators=[Optional()])
    username = StringField('Username', validators=[Optional()])
    password = PasswordField('Password', validators=[Optional()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Server')
    
    def __init__(self, server_id=None, *args, **kwargs):
        super(MediaServerForm, self).__init__(*args, **kwargs)
        self.server_id = server_id  # Store server ID for edit validation
        
        # Populate service type choices from plugins
        from app.services.media_service_factory import MediaServiceFactory
        from app.models_plugins import Plugin
        
        # Get available service types from all discovered plugins, not just enabled ones
        available_services = []
        plugins = Plugin.query.all() # Changed from filter_by(is_enabled=True)
        
        from app.services.plugin_manager import plugin_manager
        for plugin in plugins:
            try:
                # Use the plugin manager to get the display name
                plugin_info = plugin_manager.get_plugin_info(plugin.plugin_id)
                if plugin_info:
                    display_name = plugin_info.get('name', plugin.plugin_id.title())
                    available_services.append((plugin.plugin_id, display_name))
            except Exception:
                # Fallback for any issues
                available_services.append((plugin.plugin_id, plugin.plugin_id.title()))
                continue
        
        # Sort by display name
        available_services.sort(key=lambda x: x[1])
        self.service_type.choices = available_services
    
    def validate_name(self, field):
        from app.models_media_services import MediaServer
        # Check if name is unique (excluding current server if editing)
        query = MediaServer.query.filter_by(name=field.data)
        if self.server_id:
            query = query.filter(MediaServer.id != self.server_id)
        
        if query.first():
            raise ValidationError('A server with this name already exists.')

class AppBaseUrlForm(FlaskForm):
    app_name = StringField("Application Name", validators=[DataRequired(), Length(max=100)])
    app_base_url = StringField('Public Application URL', validators=[DataRequired(message="This URL is required."), URL(message="Invalid URL. Must be full public URL (e.g., https://mum.example.com).")], description="Full public URL where this application is accessible from the internet.")
    app_local_url = StringField('Local Application URL', validators=[Optional(), URL(message="Invalid URL. Must be full local URL (e.g., http://192.168.1.100:5000).")], description="Local URL for internal network access (optional).")
    submit = SubmitField('Save and Continue')

class DiscordConfigForm(FlaskForm):
    enable_discord_oauth = BooleanField(
        'Enable Discord OAuth Login', 
        default=False,
        description="Allow users to log in using their Discord accounts."
    )
    discord_client_id = StringField(
        'Discord Client ID', 
        validators=[Optional(), Length(min=17, max=20)],
        description="Your Discord application's Client ID."
    )
    discord_client_secret = PasswordField(
        'Discord Client Secret', 
        validators=[Optional(), Length(min=32, max=32)],
        description="Your Discord application's Client Secret."
    )
    discord_oauth_auth_url = StringField(
        'Discord OAuth Authorization URL',
        validators=[Optional(), URL()],
        description="The authorization URL for your Discord OAuth application. Usually auto-generated."
    )
    discord_bot_require_sso_on_invite = BooleanField(
        'Make Discord Login Mandatory', 
        default=False,
        description="Require Discord authentication for all new invites (can be overridden per invite)."
    )
    discord_require_guild_membership = BooleanField(
        'Require Discord Server Membership', 
        default=False,
        description="Require users to be members of your Discord server."
    )
    discord_guild_id = StringField( # Used by "Require Guild Membership" and/or Bot Features
        'Discord Server (Guild) ID', 
        validators=[Optional(), Length(min=17, max=20)],
        description="Your Discord server's ID. Required for guild membership checks and bot features."
    )
    discord_server_invite_url = StringField( # Used if "Require Guild Membership" is ON
        'Discord Server Invite URL', 
        validators=[Optional(), URL()],
        description="Invite link to your Discord server. Shown to users who need to join."
    )
    enable_discord_bot = BooleanField(
        'Enable Discord Bot Features', 
        default=False,
        description="Enable automated Discord bot functionality."
    )
    discord_bot_token = PasswordField(
        'Discord Bot Token', 
        validators=[Optional()],
        description="Your Discord bot's token for automated features."
    )
    discord_monitored_role_id = StringField(
        'Monitored Role ID', 
        validators=[Optional(), Length(min=17, max=20)],
        description="Discord role ID to monitor for automatic user management."
    )
    discord_thread_channel_id = StringField(
        'Thread Channel ID', 
        validators=[Optional(), Length(min=17, max=20)],
        description="Channel ID where the bot will create threads for user discussions."
    )
    discord_bot_log_channel_id = StringField(
        'Bot Log Channel ID', 
        validators=[Optional(), Length(min=17, max=20)],
        description="Channel ID where the bot will send log messages."
    )
    discord_bot_whitelist_sharers = BooleanField(
        'Whitelist Server Sharers from Bot Actions', 
        default=True,
        description="Prevent the bot from taking actions against users who share their own servers."
    )
    submit = SubmitField('Save Discord Settings')

    def validate_discord_client_id(self, field):
        if self.enable_discord_oauth.data and not field.data:
            raise ValidationError('Discord Client ID is required when Discord OAuth is enabled.')

    def validate_discord_client_secret(self, field):
        if self.enable_discord_oauth.data and not field.data:
            raise ValidationError('Discord Client Secret is required when Discord OAuth is enabled.')

    def validate_discord_guild_id(self, field):
        if (self.discord_require_guild_membership.data or self.enable_discord_bot.data) and not field.data:
            raise ValidationError('Discord Server ID is required when guild membership is required or bot features are enabled.')

    def validate_discord_server_invite_url(self, field):
        if self.discord_require_guild_membership.data and not field.data:
            raise ValidationError('Discord Server Invite URL is required when guild membership is required.')

    def validate_discord_bot_token(self, field):
        if self.enable_discord_bot.data and not field.data:
            raise ValidationError('Discord Bot Token is required when bot features are enabled.')

class UserEditForm(FlaskForm): # As updated for whitelist fields
    plex_username = StringField('Plex Username', render_kw={'readonly': True})
    plex_email = StringField('Plex Email', render_kw={'readonly': True})
    is_home_user = BooleanField('Plex Home User', render_kw={'disabled': True})
    libraries = SelectMultipleField(
        'Grant Access to Libraries', 
        coerce=str, 
        validators=[Optional()],
        widget=ListWidget(prefix_label=False), 
        option_widget=CheckboxInput()
    )
    access_expiration = DateField('Access Expiration Date', validators=[Optional(), date_not_in_past], format='%Y-%m-%d')
    membership_duration_days = IntegerField('Membership Duration (days)', validators=[Optional(), NumberRange(min=1)], default=None)
    is_discord_bot_whitelisted = BooleanField('Whitelist from Discord Bot Actions')
    is_purge_whitelisted = BooleanField('Whitelist from Inactivity Purge')
    notes = TextAreaField('User Notes', validators=[Optional(), Length(max=1000)])
    
    # Clear checkboxes for nullable fields
    clear_access_expiration = BooleanField(
        'Clear Access Expiration (Set to Never Expire)', 
        default=False,
        description="Check this to remove the access expiration date."
    )
    allow_downloads = BooleanField(
        'Enable Downloads (Allow Sync)', 
        default=False,
        description="Allow the user to download/sync content from shared libraries."
    )
    allow_4k_transcode = BooleanField(
        'Allow 4K Transcoding', 
        default=False,
        description="Allow the user to transcode 4K content (resource intensive)."
    )
    submit = SubmitField('Save Changes')

class MassUserEditForm(FlaskForm): # As updated
    action = SelectField('Action', choices=[
        ('update_libraries', 'Update Libraries'),
        ('extend_access', 'Extend Access by Days'),
        ('set_expiration', 'Set Expiration Date'),
        ('clear_expiration', 'Clear Expiration (Never Expire)'),
        ('enable_downloads', 'Enable Downloads'),
        ('disable_downloads', 'Disable Downloads'),
        ('whitelist_purge', 'Whitelist from Purge'),
        ('unwhitelist_purge', 'Remove Purge Whitelist'),
        ('delete_users', 'Delete Users')
    ], validators=[DataRequired()])
    libraries = SelectMultipleField('Libraries', coerce=str, validators=[Optional()])
    days_to_extend = IntegerField('Days to Extend', validators=[Optional(), NumberRange(min=1)])
    new_expiration_date = DateField('New Expiration Date', validators=[Optional(), date_not_in_past], format='%Y-%m-%d')
    confirm_delete = BooleanField('Confirm Deletion (for "Delete Users")', validators=[Optional()])
    submit = SubmitField('Apply Changes')

class InviteCreateForm(FlaskForm):
    custom_path = StringField('Invite Code String', validators=[Optional(), Length(min=3, max=100), Regexp(r'^[a-zA-Z0-9_-]*')])
    expires_in_days = IntegerField('Expiration', validators=[Optional(), NumberRange(min=0)], default=0, description="0 for no expiration.")
    expires_at = DateField('Expiration Date', validators=[Optional(), date_not_in_past], description="Invite is valid until the end of this day. Leave blank for no expiry.")
    number_of_uses = IntegerField('Number of Uses', validators=[Optional(), NumberRange(min=0)], default=0, description="0 for unlimited uses.")
    membership_duration_days = IntegerField('Membership Duration (days)', validators=[Optional(), NumberRange(min=1)], default=None, description="Leave blank for permanent access.")
    libraries = SelectMultipleField('Grant Access to Libraries', coerce=str, validators=[Optional()])
    allow_downloads = BooleanField('Enable Downloads (Allow Sync)', default=False, description="Allow the invited user to download/sync content from shared libraries.")
    invite_to_plex_home = BooleanField('Invite to Plex Home', default=False, description="Invite the user to your Plex Home. This allows them to switch between users.")
    allow_live_tv = BooleanField('Allow Live TV Access', default=False, description="Grant access to Live TV channels.")
    override_force_discord_auth = BooleanField("Override 'Make Discord Login Mandatory'")
    override_force_guild_membership = BooleanField("Override 'Require Discord Server Membership'")
    grant_purge_whitelist = BooleanField('Whitelist user from Inactivity Purge')
    grant_bot_whitelist = BooleanField('Whitelist user from Discord Bot Actions')
    membership_expires_at = DateField(
        'Membership Expiration Date', 
        validators=[Optional(), date_not_in_past],
        description="User's access will expire at the end of this day. Leave blank for permanent access."
    )
    submit = SubmitField('Create Invite')

class InviteEditForm(FlaskForm):
    # Note: custom_path is intentionally omitted as it should not be editable.
    expires_in_days = IntegerField('Expiration', validators=[Optional(), NumberRange(min=0)], default=0)
    clear_expiry = BooleanField('Clear Expiration (Set to Never Expire)', default=False)
    
    number_of_uses = IntegerField('Number of Uses', validators=[Optional(), NumberRange(min=0)], default=0)
    clear_max_uses = BooleanField('Clear Max Uses (Set to Unlimited)', default=False)
    
    membership_duration_days = IntegerField('Membership Duration (days)', validators=[Optional(), NumberRange(min=1)], default=None)
    clear_membership_duration = BooleanField('Clear Membership Duration (Set to Permanent)', default=False)
    
    libraries = SelectMultipleField('Grant Access to Libraries', coerce=str, validators=[Optional()])
    allow_downloads = BooleanField('Enable Downloads (Allow Sync)', default=False)
    invite_to_plex_home = BooleanField('Invite to Plex Home', default=False)
    allow_live_tv = BooleanField('Allow Live TV Access', default=False)
    
    override_force_discord_auth = BooleanField("Override 'Make Discord Login Mandatory'")
    override_force_guild_membership = BooleanField("Override 'Require Discord Server Membership'")
    grant_purge_whitelist = BooleanField('Whitelist user from Inactivity Purge')
    grant_bot_whitelist = BooleanField('Whitelist user from Discord Bot Actions')
    submit = SubmitField('Save Changes')

class PurgeUsersForm(FlaskForm):
    inactive_days = IntegerField(
        'Days of Inactivity', 
        validators=[DataRequired(), NumberRange(min=1)], 
        default=30,
        description="Users inactive for this many days will be purged."
    )
    exclude_sharers = BooleanField('Exclude users who share back their servers', default=True)
    submit = SubmitField('Preview Purge')

class SetPasswordForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])
    submit_set_password = SubmitField('Set Username & Password')

    def validate_username(self, field):
        from app.models import AdminAccount
        if AdminAccount.query.filter_by(username=field.data).first():
            raise ValidationError('Username already exists. Choose a different one.')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=8)])
    confirm_new_password = PasswordField(
        'Confirm New Password', 
        validators=[DataRequired(), EqualTo('new_password', message='Passwords must match.')]
    )
    submit_change_password = SubmitField('Change Password')

class AdminCreateForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Temporary Password', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirm Temporary Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Create Admin')

    def validate_username(self, field):
        from app.models import AdminAccount
        if AdminAccount.query.filter_by(username=field.data).first():
            raise ValidationError('Username already exists. Choose a different one.')

class AdminEditForm(FlaskForm):
    username = StringField('Username', render_kw={'readonly': True})
    roles = SelectMultipleField('Roles', coerce=int, validators=[Optional()])
    is_discord_bot_whitelisted = BooleanField('Whitelist from Discord Bot Actions')
    is_purge_whitelisted = BooleanField('Whitelist from Inactivity Purge')
    
    def __init__(self, *args, **kwargs):
        super(AdminEditForm, self).__init__(*args, **kwargs)
        from app.models import Role
        self.roles.choices = [(role.id, role.name) for role in Role.query.all()]
    
    submit = SubmitField('Save Changes')

class RoleCreateForm(FlaskForm):
    name = StringField('Role Name', validators=[DataRequired(), Length(min=3, max=80)])
    description = StringField('Description', validators=[Optional(), Length(max=255)])
    color = StringField('Badge Color', default='#808080', validators=[
        Optional(), Regexp(r'^#[0-9A-Fa-f]{6}$', message='Must be a valid hex color (e.g., #FF5733)')
    ])
    icon = StringField('Badge Icon Classes', validators=[
        Optional(), Length(max=100)
    ], description='CSS classes for the badge icon (e.g., "fas fa-star")')
    
    # Permissions
    can_manage_users = BooleanField('Can Manage Users')
    can_manage_invites = BooleanField('Can Manage Invites') 
    can_manage_settings = BooleanField('Can Manage Settings')
    can_view_logs = BooleanField('Can View Logs')
    
    submit = SubmitField('Create Role')

class RoleEditForm(FlaskForm):
    name = StringField('Role Name', validators=[DataRequired(), Length(min=3, max=80)])
    description = StringField('Description', validators=[Optional(), Length(max=255)])
    color = StringField('Badge Color', default='#808080', validators=[
        Optional(), Regexp(r'^#[0-9A-Fa-f]{6}$', message='Must be a valid hex color (e.g., #FF5733)')
    ])
    icon = StringField('Badge Icon Classes', validators=[
        Optional(), Length(max=100)
    ], description='CSS classes for the badge icon (e.g., "fas fa-star")')
    
    # Permissions
    can_manage_users = BooleanField('Can Manage Users')
    can_manage_invites = BooleanField('Can Manage Invites') 
    can_manage_settings = BooleanField('Can Manage Settings')
    can_view_logs = BooleanField('Can View Logs')
    
    submit = SubmitField('Save Changes')

class RoleMemberForm(FlaskForm):
    # This is the corrected field definition
    admins_to_add = SelectMultipleField(
        'Add Admins to Role', 
        coerce=int, 
        validators=[Optional()],
        widget=ListWidget(prefix_label=False), 
        option_widget=CheckboxInput()
    )
    submit_add_members = SubmitField('Add to Role')

class AdminResetPasswordForm(FlaskForm):
    new_password = PasswordField(
        'New Temporary Password', 
        validators=[DataRequired(), Length(min=8)]
    )
    confirm_new_password = PasswordField(
        'Confirm Temporary Password', 
        validators=[DataRequired(), EqualTo('new_password', message='Passwords must match.')]
    )
    submit_reset_password = SubmitField('Set New Password')

class PluginSettingsForm(FlaskForm):
    enabled_plugins = SelectMultipleField('Enabled Plugins', coerce=str, validators=[Optional()])
    submit = SubmitField('Save Changes', message="Letters, numbers, hyphens, underscores only.", description="e.g., 'friends' -> /invite/friends")
    
    expires_at = DateField('Expiration Date', validators=[Optional(), date_not_in_past], description="Invite is valid until the end of this day. Leave blank for no expiry.")
    
    number_of_uses = IntegerField('Number of Uses', validators=[Optional(), NumberRange(min=0)], default=0, description="0 for unlimited uses.")
    libraries = SelectMultipleField('Grant Access to Libraries', coerce=str, validators=[Optional()], description="Default: all libraries.")
    allow_downloads = BooleanField('Enable Downloads (Allow Sync)', default=False, description="Allow the invited user to download/sync content from shared libraries.")
    invite_to_plex_home = BooleanField('Invite to Plex Home', default=False, description="Invite the user to your Plex Home. This allows them to switch between users.")
    allow_live_tv = BooleanField('Allow Live TV Access', default=False, description="Grant access to Live TV and DVR.")
    
    membership_expires_at = DateField(
        'Membership Expiration Date', 
        validators=[Optional(), date_not_in_past],
        description="User's access will expire at the end of this day. Leave blank for permanent access."
    )
    
    override_force_discord_auth = BooleanField(
        "Override 'Make Discord Login Mandatory'",
        default=False, # The route will set the default based on global settings
        description="Override the global setting for requiring Discord login for this specific invite."
    )
    override_force_guild_membership = BooleanField(
        "Override 'Require Discord Server Membership'",
        default=False, # The route will set the default based on global settings
        description="Override the global setting for requiring server membership for this specific invite."
    )
    grant_purge_whitelist = BooleanField(
        'Whitelist user from Inactivity Purge',
        default=False,
        description="The created user will be automatically whitelisted from inactivity purges."
    )
    grant_bot_whitelist = BooleanField(
        'Whitelist user from Discord Bot Actions',
        default=False,
        description="The created user will be immune to automated Discord Bot actions."
    )
    
    submit = SubmitField('Create Invite')

class GeneralSettingsForm(FlaskForm): # As before
    app_name = StringField("Application Name", validators=[Optional(), Length(max=100)])
    app_base_url = StringField(
        'Application Base URL', 
        validators=[DataRequired(message="This URL is required."), URL(message="Invalid URL. Must be full public URL (e.g., https://mum.example.com).")],
        description="Full public URL where this application is accessible. Essential for generating correct invite and callback links."
    )
    submit = SubmitField('Save General Settings')

    def __init__(self, *args, **kwargs):
        super(GeneralSettingsForm, self).__init__(*args, **kwargs)

class TimezonePreferenceForm(FlaskForm):
    timezone_preference = SelectField(
        'Display Time In',
        choices=[('local', 'My Timezone'), ('utc', 'UTC')],
        validators=[DataRequired()]
    )
    time_format = SelectField(
        'Time Format',
        choices=[('12', '12-hour (AM/PM)'), ('24', '24-hour')],
        validators=[DataRequired()],
        default='12'
    )
    local_timezone = HiddenField() # This will be populated by JavaScript
    submit = SubmitField('Save Timezone Setting')