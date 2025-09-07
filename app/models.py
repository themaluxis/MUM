# File: app/models.py
import enum
import json
import uuid
from datetime import datetime, timedelta
from app.utils.timezone_utils import utcnow
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.ext.mutable import MutableDict, MutableList
from app.extensions import db, JSONEncodedDict
import secrets
from flask import current_app 
from sqlalchemy import Table, Column, Integer, ForeignKey
from app.models_media_services import MediaServer

# Many-to-many relationship table for user app access and roles
app_user_roles = db.Table('app_user_roles',
    db.Column('app_user_id', db.Integer, db.ForeignKey('user_app_access.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True)
)

# Many-to-many relationship table for invites and servers
invite_servers = db.Table('invite_servers',
    db.Column('invite_id', db.Integer, db.ForeignKey('invites.id'), primary_key=True),
    db.Column('server_id', db.Integer, db.ForeignKey('media_servers.id'), primary_key=True)
)

class SettingValueType(enum.Enum): # ... (as before)
    STRING = "string"; INTEGER = "integer"; BOOLEAN = "boolean"; JSON = "json"; SECRET = "secret"

class EventType(enum.Enum): # ... (as before, will add bot-specific events later)
    APP_STARTUP = "APP_STARTUP"; APP_SHUTDOWN = "APP_SHUTDOWN"; SETTING_CHANGE = "SETTING_CHANGE"
    ADMIN_LOGIN_SUCCESS = "ADMIN_LOGIN_SUCCESS"; ADMIN_LOGIN_FAIL = "ADMIN_LOGIN_FAIL"; ADMIN_LOGOUT = "ADMIN_LOGOUT"
    ADMIN_PASSWORD_CHANGE = "ADMIN_PASSWORD_CHANGE"; PLEX_CONFIG_TEST_SUCCESS = "PLEX_CONFIG_TEST_SUCCESS"
    PLEX_CONFIG_TEST_FAIL = "PLEX_CONFIG_TEST_FAIL"; PLEX_CONFIG_SAVE = "PLEX_CONFIG_SAVE"
    PLEX_SYNC_USERS_START = "PLEX_SYNC_USERS_START"; PLEX_SYNC_USERS_COMPLETE = "PLEX_SYNC_USERS_COMPLETE"
    PLEX_USER_ADDED = "PLEX_USER_ADDED_TO_SERVER"; PLEX_USER_REMOVED = "PLEX_USER_REMOVED_FROM_SERVER"
    PLEX_USER_LIBS_UPDATED = "PLEX_USER_LIBS_UPDATED_ON_SERVER"; PLEX_SESSION_DETECTED = "PLEX_SESSION_DETECTED"
    MUM_USER_ADDED_FROM_PLEX = "MUM_USER_ADDED_FROM_PLEX"
    MUM_USER_REMOVED_MISSING_IN_PLEX = "MUM_USER_REMOVED_MISSING_IN_PLEX"
    MUM_USER_LIBRARIES_EDITED = "MUM_USER_LIBRARIES_EDITED"
    MUM_USER_DELETED_FROM_MUM = "MUM_USER_DELETED_FROM_MUM"; INVITE_CREATED = "INVITE_CREATED"
    INVITE_DELETED = "INVITE_DELETED"; INVITE_VIEWED = "INVITE_VIEWED"
    INVITE_USED_SUCCESS_PLEX = "INVITE_USED_SUCCESS_PLEX"
    INVITE_USED_SUCCESS_DISCORD = "INVITE_USED_SUCCESS_DISCORD"
    INVITE_USED_ACCOUNT_LINKED = "INVITE_USED_ACCOUNT_LINKED"
    INVITE_USER_ACCEPTED_AND_SHARED = "INVITE_USER_ACCEPTED_AND_SHARED"; INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_MAX_USES_REACHED = "INVITE_MAX_USES_REACHED"; DISCORD_CONFIG_SAVE = "DISCORD_CONFIG_SAVE"
    DISCORD_ADMIN_LINK_SUCCESS = "DISCORD_ADMIN_LINK_SUCCESS"
    DISCORD_ADMIN_UNLINK = "DISCORD_ADMIN_UNLINK"; ERROR_GENERAL = "ERROR_GENERAL"
    ERROR_PLEX_API = "ERROR_PLEX_API"; ERROR_DISCORD_API = "ERROR_DISCORD_API"
    DISCORD_BOT_START = "DISCORD_BOT_START"
    DISCORD_BOT_STOP = "DISCORD_BOT_STOP"
    DISCORD_BOT_ERROR = "DISCORD_BOT_ERROR"
    DISCORD_BOT_USER_LEFT_SERVER = "DISCORD_BOT_USER_LEFT_SERVER" # User left Discord
    DISCORD_BOT_USER_REMOVED_FROM_PLEX = "DISCORD_BOT_USER_REMOVED_FROM_PLEX" # Bot removed user from Plex
    DISCORD_BOT_ROLE_ADDED_INVITE_SENT = "DISCORD_BOT_ROLE_ADDED_INVITE_SENT" # Bot sent invite due to role add
    DISCORD_BOT_ROLE_REMOVED_USER_REMOVED = "DISCORD_BOT_ROLE_REMOVED_USER_REMOVED" # Bot removed user due to role removal
    DISCORD_BOT_PURGE_DM_SENT = "DISCORD_BOT_PURGE_DM_SENT" # DM sent for app-initiated purge
    DISCORD_BOT_GUILD_MEMBER_CHECK_FAIL = "DISCORD_BOT_GUILD_MEMBER_CHECK_FAIL" # Failed guild check on invite page
    # Add Bot Specific Event Types Later, e.g., BOT_USER_PURGED, BOT_INVITE_SENT

class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    # Permissions for this role will be stored as a simple JSON list of strings.
    permissions = db.Column(MutableList.as_mutable(JSONEncodedDict), nullable=True, default=list)
    color = db.Column(db.String(7), nullable=True, default='#808080') # Default to a neutral gray
    icon = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f'<Role {self.name}>'

class Owner(db.Model, UserMixin):
    """Single app owner with ultimate permissions"""
    __tablename__ = 'owners'
    
    # Should always be ID 1 - only one owner allowed
    id = db.Column(db.Integer, primary_key=True)
    
    # Core account info
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # Plex integration (optional)
    plex_uuid = db.Column(db.String(255), unique=True, nullable=True)
    plex_username = db.Column(db.String(255), nullable=True)
    plex_thumb = db.Column(db.String(512), nullable=True)
    
    # Discord integration (optional)
    discord_user_id = db.Column(db.String(255), unique=True, nullable=True)
    discord_username = db.Column(db.String(255), nullable=True)
    discord_avatar_hash = db.Column(db.String(255), nullable=True)
    discord_access_token = db.Column(db.String(255), nullable=True)
    discord_refresh_token = db.Column(db.String(255), nullable=True)
    discord_token_expires_at = db.Column(db.DateTime, nullable=True)
    discord_email = db.Column(db.String(255), nullable=True)
    discord_email_verified = db.Column(db.Boolean, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    
    # Owner preferences
    preferred_user_list_view = db.Column(db.String(10), default='cards', nullable=False)
    force_password_change = db.Column(db.Boolean, default=False, nullable=False)
    
    def __repr__(self):
        return f'<Owner {self.username}>'
    
    def set_password(self, password):
        """Set password hash for owner account"""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        """Check password for owner account"""
        return check_password_hash(self.password_hash, password) if self.password_hash else False
    
    def get_display_name(self):
        """Get the display name for the owner"""
        return self.username or self.plex_username or self.email or 'Owner'
    
    def get_avatar(self):
        """Get the avatar URL for the owner"""
        return self.plex_thumb
    
    def has_permission(self, permission_name):
        """Owner always has all permissions"""
        return True
    
    def get_id(self):
        """Return user ID with type prefix for Flask-Login"""
        return f"owner:{self.id}"
    
    @staticmethod
    def get_owner():
        """Get the single owner account (should always be ID 1)"""
        return Owner.query.first()
    
    @staticmethod
    def create_owner(username, email, password):
        """Create the owner account (should only be called once during setup)"""
        if Owner.query.first():
            raise ValueError("Owner account already exists")
        
        owner = Owner(
            username=username,
            email=email
        )
        owner.set_password(password)
        db.session.add(owner)
        db.session.commit()
        return owner
    
class Setting(db.Model): # ... (Setting model remains the same structure, new keys will be added via UI/code) ...
    __tablename__ = 'settings'; id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    value_type = db.Column(db.Enum(SettingValueType), default=SettingValueType.STRING, nullable=False)
    name = db.Column(db.String(100), nullable=True); description = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, default=False); created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    def __repr__(self): return f'<Setting {self.key}>'
    def get_value(self):
        if self.value is None: return None
        if self.value_type == SettingValueType.INTEGER: return int(self.value)
        elif self.value_type == SettingValueType.BOOLEAN: return self.value.lower() in ['true', '1', 'yes', 'on']
        elif self.value_type == SettingValueType.JSON:
            try: return json.loads(self.value)
            except json.JSONDecodeError: return None
        return self.value
    @staticmethod
    def get(key_name, default=None):
        if current_app:
            engine_conn_setting_get = None
            try:
                engine_conn_setting_get = db.engine.connect()
                if db.engine.dialect.has_table(engine_conn_setting_get, Setting.__tablename__):
                    setting_obj = Setting.query.filter_by(key=key_name).first()
                    if setting_obj: return setting_obj.get_value()
            except Exception as e: current_app.logger.debug(f"Setting.get({key_name}): DB query failed: {e}")
            finally:
                if engine_conn_setting_get: engine_conn_setting_get.close()
            if key_name in current_app.config: return current_app.config.get(key_name, default)
        return default
    @staticmethod
    def set(key_name, value, v_type=SettingValueType.STRING, name=None, description=None, is_public=False):
        setting = Setting.query.filter_by(key=key_name).first()
        if not setting: setting = Setting(key=key_name); db.session.add(setting)
        setting.value_type = v_type; setting.name = name or setting.name; setting.description = description or setting.description; setting.is_public = is_public
        if v_type == SettingValueType.JSON and not isinstance(value, str): setting.value = json.dumps(value)
        elif isinstance(value, bool) and v_type == SettingValueType.BOOLEAN: setting.value = 'true' if value else 'false'
        elif isinstance(value, int) and v_type == SettingValueType.INTEGER: setting.value = str(value)
        elif value is None: setting.value = None # Allow unsetting/nulling a value
        else: setting.value = str(value)
        db.session.commit()
        if current_app and key_name.isupper(): current_app.config[key_name] = setting.get_value()
        return setting
    @staticmethod
    def get_bool(key_name, default=False):
        val_str = Setting.get(key_name) # Setting.get already handles defaults and app.config fallback
        if val_str is None: # If Setting.get returned None (meaning not found and no default from .get itself)
            return default
        if isinstance(val_str, bool): # If Setting.get somehow returned a bool already
            return val_str
        return str(val_str).lower() in ['true', '1', 'yes', 'on']
    # --- END OF get_bool ---

# AdminAccount model removed - replaced by Owner and AppUser models


class UserAppAccess(db.Model, UserMixin):
    """User app access accounts for MUM login and management"""
    __tablename__ = 'user_app_access'
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    
    # Core account info
    username = db.Column(db.String(255), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(256), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    
    # Account status
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Additional info
    notes = db.Column(db.Text, nullable=True)
    # avatar_url removed - UserAppAccess will never have avatars
    
    # Invite relationship
    used_invite_id = db.Column(db.Integer, db.ForeignKey('invites.id'), nullable=True)
    invite = db.relationship('Invite', backref='user_app_access_created')
    
    # Global expiration (across all services)
    access_expires_at = db.Column(db.DateTime, nullable=True, index=True)
    
    # Global Discord integration
    discord_user_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    discord_username = db.Column(db.String(255), nullable=True)
    discord_avatar_hash = db.Column(db.String(255), nullable=True)
    discord_access_token = db.Column(db.String(255), nullable=True)
    discord_refresh_token = db.Column(db.String(255), nullable=True)
    discord_token_expires_at = db.Column(db.DateTime, nullable=True)
    discord_email = db.Column(db.String(255), nullable=True)
    discord_email_verified = db.Column(db.Boolean, nullable=True)
    
    # Role relationship (updated to reference new table name)
    roles = db.relationship('Role', secondary=app_user_roles, lazy='subquery',
                            backref=db.backref('user_app_access', lazy=True))
    
    # User media access (reverse relationship to UserMediaAccess)
    media_accesses = db.relationship('UserMediaAccess', back_populates='user_app_access', cascade="all, delete-orphan")
    
    
    def __repr__(self):
        return f'<UserAppAccess {self.username}>'
    
    def set_password(self, password):
        """Set password hash for user app access account"""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        """Check password for user app access account"""
        return check_password_hash(self.password_hash, password) if self.password_hash else False
    
    def has_permission(self, permission_name):
        """Check if user has a specific permission through their roles"""
        # Check if any of the user's roles contain the required permission
        for role in self.roles:
            if permission_name in (role.permissions or []):
                return True
        return False
    
    def get_avatar(self, fallback='/static/img/default_avatar.png'):
        """UserAppAccess never has avatars - always return fallback"""
        return fallback
    
    def get_display_name(self):
        """Get the best display name for this user"""
        return self.username or self.email or 'Unknown User'
    
    def get_id(self):
        """Return user ID with type prefix for Flask-Login"""
        return f"user_app_access:{self.id}"
    
    def get_media_accesses_by_service_type(self):
        """Get media accesses grouped by service type"""
        from app.models_media_services import UserMediaAccess
        
        accesses_by_type = {}
        for access in self.media_accesses:
            service_type = access.server.service_type.value if access.server else 'unknown'
            if service_type not in accesses_by_type:
                accesses_by_type[service_type] = []
            accesses_by_type[service_type].append(access)
        return accesses_by_type
    
    def get_all_servers(self):
        """Get all media servers this user has access to"""
        return [access.server for access in self.media_accesses if access.server and access.is_active]
    
    def has_access_to_server(self, server_id):
        """Check if user has access to a specific media server"""
        return any(access.server_id == server_id and access.is_active for access in self.media_accesses)
    
    def get_server_access(self, server_id):
        """Get UserMediaAccess object for a specific server"""
        return next((access for access in self.media_accesses if access.server_id == server_id), None)

# ServiceAccount model removed - replaced by UserAppAccess + UserMediaAccess architecture

# (Invite, InviteUsage, HistoryLog models as before - no immediate changes for bot setup yet)
class Invite(db.Model):
    __tablename__ = 'invites'; id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True, default=lambda: secrets.token_urlsafe(8))
    custom_path = db.Column(db.String(100), unique=True, nullable=True, index=True); expires_at = db.Column(db.DateTime, nullable=True)
    max_uses = db.Column(db.Integer, nullable=True); current_uses = db.Column(db.Integer, default=0, nullable=False) # Added nullable=False
    grant_library_ids = db.Column(MutableList.as_mutable(JSONEncodedDict), default=list)
    allow_downloads = db.Column(db.Boolean, default=False, nullable=False)
    created_by_owner_id = db.Column(db.Integer, db.ForeignKey('owners.id')); owner_creator = db.relationship('Owner')
    created_at = db.Column(db.DateTime, default=utcnow); updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True) # Added nullable=False
    invite_usages = db.relationship('InviteUsage', back_populates='invite', cascade="all, delete-orphan")
    membership_duration_days = db.Column(db.Integer, nullable=True) # Duration in days set at invite creation
    require_discord_auth = db.Column(db.Boolean, nullable=True)
    require_discord_guild_membership = db.Column(db.Boolean, nullable=True)
    grant_purge_whitelist = db.Column(db.Boolean, nullable=True, default=False)
    grant_bot_whitelist = db.Column(db.Boolean, nullable=True, default=False)
    invite_to_plex_home = db.Column(db.Boolean, nullable=True, default=False)
    allow_live_tv = db.Column(db.Boolean, nullable=True, default=False)
    servers = db.relationship('MediaServer', secondary=invite_servers, lazy='subquery',
                              backref=db.backref('invites', lazy=True))
    def __repr__(self): return f'<Invite {self.custom_path or self.token}>'
    @property
    def is_expired(self): 
        if not self.expires_at:
            return False
        # Ensure both datetimes are timezone-aware for comparison
        from datetime import timezone
        now = utcnow()
        expires = self.expires_at
        
        # If expires_at is naive, assume it's UTC
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        
        return now > expires
    @property
    def has_reached_max_uses(self): return self.max_uses is not None and self.current_uses >= self.max_uses
    @property
    def is_usable(self): return self.is_active and not self.is_expired and not self.has_reached_max_uses
    def get_full_url(self, app_base_url):
        if not app_base_url: return "#INVITE_URL_NOT_CONFIGURED"
        path_part = self.custom_path if self.custom_path else self.token
        return f"{app_base_url.rstrip('/')}/invite/{path_part}"

class InviteUsage(db.Model): # ... (as before)
    __tablename__ = 'invite_usages'; id = db.Column(db.Integer, primary_key=True)
    invite_id = db.Column(db.Integer, db.ForeignKey('invites.id'), nullable=False); invite = db.relationship('Invite', back_populates='invite_usages')
    used_at = db.Column(db.DateTime, default=utcnow); ip_address = db.Column(db.String(45), nullable=True)
    plex_user_uuid = db.Column(db.String(255), nullable=True); plex_username = db.Column(db.String(255), nullable=True)
    plex_email = db.Column(db.String(120), nullable=True); plex_thumb = db.Column(db.String(512), nullable=True)
    plex_auth_successful = db.Column(db.Boolean, default=False, nullable=False); discord_user_id = db.Column(db.String(255), nullable=True) # Added nullable=False
    discord_username = db.Column(db.String(255), nullable=True); discord_auth_successful = db.Column(db.Boolean, default=False, nullable=False) # Added nullable=False
    user_app_access_id = db.Column(db.Integer, db.ForeignKey('user_app_access.id'), nullable=True); user_app_access = db.relationship('UserAppAccess')
    accepted_invite = db.Column(db.Boolean, default=False, nullable=False); status_message = db.Column(db.String(255), nullable=True) # Added nullable=False

class HistoryLog(db.Model): # ... (as before)
    __tablename__ = 'history_logs'; id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=utcnow, index=True)
    event_type = db.Column(db.Enum(EventType), nullable=False, index=True); message = db.Column(db.Text, nullable=False)
    details = db.Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('owners.id'), nullable=True); owner = db.relationship('Owner')
    user_app_access_id = db.Column(db.Integer, db.ForeignKey('user_app_access.id'), nullable=True); affected_user_app_access = db.relationship('UserAppAccess')
    invite_id = db.Column(db.Integer, db.ForeignKey('invites.id'), nullable=True); related_invite = db.relationship('Invite')
    def __repr__(self): return f'<HistoryLog {self.timestamp} [{self.event_type.name}]: {self.message[:50]}>'

# StreamHistory model removed - replaced by MediaStreamHistory in models_media_services.py

class UserPreferences(db.Model):
    __tablename__ = 'user_preferences'
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('owners.id'), unique=True, nullable=False)
    
    # This field will store the user's choice: 'local' or 'utc'
    timezone_preference = db.Column(db.String(10), default='local', nullable=False)
    
    # This field will store the browser-detected timezone name (e.g., 'America/New_York')
    local_timezone = db.Column(db.String(100), nullable=True)
    
    # This field will store the user's time format preference: '12' or '24'
    time_format = db.Column(db.String(2), default='12', nullable=False)
    
    def __repr__(self):
        return f'<UserPreferences for Owner {self.owner_id}>'
    
    @staticmethod
    def get_timezone_preference(owner_id):
        prefs = UserPreferences.query.filter_by(owner_id=owner_id).first()
        if prefs:
            return {
                "preference": prefs.timezone_preference,
                "local_timezone": prefs.local_timezone,
                "time_format": prefs.time_format
            }
        return {"preference": "local", "local_timezone": None, "time_format": "12"}
    
    @staticmethod
    def set_timezone_preference(owner_id, preference, local_timezone=None, time_format=None):
        prefs = UserPreferences.query.filter_by(owner_id=owner_id).first()
        if not prefs:
            prefs = UserPreferences(owner_id=owner_id)
            db.session.add(prefs)
        
        prefs.timezone_preference = preference
        if preference == 'local' and local_timezone:
            prefs.local_timezone = local_timezone
        if time_format:
            prefs.time_format = time_format
        
        db.session.commit()
        return prefs
