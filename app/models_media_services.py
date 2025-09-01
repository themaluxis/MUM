# File: app/models_media_services.py
import enum
import json
import uuid
from datetime import datetime
from app.utils.timezone_utils import utcnow
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.ext.mutable import MutableDict, MutableList
from app.extensions import db, JSONEncodedDict
from sqlalchemy import event
from app.models_plugins import Plugin

class ServiceType(enum.Enum):
    PLEX = "plex"
    EMBY = "emby"
    JELLYFIN = "jellyfin"
    KAVITA = "kavita"
    AUDIOBOOKSHELF = "audiobookshelf"
    KOMGA = "komga"
    ROMM = "romm"
    
    def __lt__(self, other):
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented
    
    def __le__(self, other):
        if self.__class__ is other.__class__:
            return self.value <= other.value
        return NotImplemented
    
    def __gt__(self, other):
        if self.__class__ is other.__class__:
            return self.value > other.value
        return NotImplemented
    
    def __ge__(self, other):
        if self.__class__ is other.__class__:
            return self.value >= other.value
        return NotImplemented

class MediaServer(db.Model):
    """Represents a media server instance (can have multiple servers of same type)"""
    __tablename__ = 'media_servers'
    
    id = db.Column(db.Integer, primary_key=True)
    server_nickname = db.Column(db.String(100), nullable=False, unique=True)  # User-defined nickname - must be unique
    server_name = db.Column(db.String(255), nullable=True)  # Actual server name from the service
    service_type = db.Column(db.Enum(ServiceType), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    api_key = db.Column(db.String(512), nullable=True)  # For services that use API keys
    username = db.Column(db.String(255), nullable=True)  # For services that use username/password
    password = db.Column(db.String(512), nullable=True)  # Encrypted password
    
    # Service-specific configuration stored as JSON
    config = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)
    
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    
    # Server status tracking
    last_status_check = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.Boolean, nullable=True)  # True=online, False=offline, None=never checked
    last_status_error = db.Column(db.Text, nullable=True)  # Error message if offline
    last_version = db.Column(db.String(100), nullable=True)  # Server version from last successful check
    
    # Relationships
    libraries = db.relationship('MediaLibrary', back_populates='server', cascade="all, delete-orphan")
    user_accesses = db.relationship('UserMediaAccess', back_populates='server', cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<MediaServer {self.server_nickname} ({self.service_type.value})>'
    
    def update_plugin_servers_count(self):
        """Update the servers_count for the associated plugin"""
        from app.models_plugins import Plugin
        
        plugin = Plugin.query.filter_by(plugin_id=self.service_type.value).first()
        if plugin:
            count = MediaServer.query.filter_by(service_type=self.service_type).count()
            plugin.servers_count = count
            db.session.add(plugin)
            db.session.commit()

def update_plugin_server_count(mapper, connection, target):
    """Update the server count for the associated plugin."""
    plugin_id = target.service_type.value
    
    # Use the connection directly to avoid session conflicts
    from sqlalchemy import text
    
    # Count servers of this type
    count_result = connection.execute(
        text("SELECT COUNT(*) FROM media_servers WHERE service_type = :service_type"),
        {"service_type": plugin_id}
    )
    count = count_result.scalar()
    
    # Update the plugin's servers_count
    connection.execute(
        text("UPDATE plugins SET servers_count = :count WHERE plugin_id = :plugin_id"),
        {"count": count, "plugin_id": plugin_id}
    )

@event.listens_for(MediaServer, 'after_insert')
def after_insert_media_server(mapper, connection, target):
    update_plugin_server_count(mapper, connection, target)

@event.listens_for(MediaServer, 'after_delete')
def after_delete_media_server(mapper, connection, target):
    update_plugin_server_count(mapper, connection, target)

@event.listens_for(MediaServer, 'after_update')
def after_update_media_server(mapper, connection, target):
    if db.session.is_modified(target, include_collections=False):
        update_plugin_server_count(mapper, connection, target)

class MediaLibrary(db.Model):
    """Represents a library within a media server"""
    __tablename__ = 'media_libraries'
    
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False)
    external_id = db.Column(db.String(100), nullable=False)  # ID from the service
    name = db.Column(db.String(255), nullable=False)
    library_type = db.Column(db.String(50), nullable=True)  # movies, shows, music, books, etc.
    
    # Additional metadata
    item_count = db.Column(db.Integer, nullable=True)
    last_scanned = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    server = db.relationship('MediaServer', back_populates='libraries')
    
    # Unique constraint on server_id + external_id
    __table_args__ = (db.UniqueConstraint('server_id', 'external_id', name='_server_library_uc'),)
    
    def __repr__(self):
        return f'<MediaLibrary {self.name} on {self.server.server_nickname}>'

class UserMediaAccess(db.Model):
    """Represents a user's access to a specific media server"""
    __tablename__ = 'user_media_access'
    
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    
    # Updated foreign key to UserAppAccess (nullable to support standalone server users)
    user_app_access_id = db.Column(db.Integer, db.ForeignKey('user_app_access.id'), nullable=True, index=True)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False, index=True)
    
    # Service-specific user identity
    external_user_id = db.Column(db.String(255), nullable=True, index=True)  # For Plex: plex_user_id, For Jellyfin: jellyfin_user_id
    external_user_alt_id = db.Column(db.String(255), nullable=True)  # For Plex: plex_uuid (SSO UUID)
    external_username = db.Column(db.String(255), nullable=True)
    external_email = db.Column(db.String(255), nullable=True)
    
    # Access permissions
    allowed_library_ids = db.Column(MutableList.as_mutable(JSONEncodedDict), default=list)
    allow_downloads = db.Column(db.Boolean, default=False, nullable=False)
    allow_4k_transcode = db.Column(db.Boolean, default=True, nullable=False)
    
    # Service-specific settings stored as JSON
    service_settings = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)
    
    # Status tracking
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Fields moved from ServiceAccount
    notes = db.Column(db.Text, nullable=True)  # Admin notes about this service account
    external_avatar_url = db.Column(db.String(512), nullable=True)  # Service-specific avatar
    used_invite_id = db.Column(db.Integer, db.ForeignKey('invites.id'), nullable=True, index=True)  # Which invite created this access
    service_join_date = db.Column(db.DateTime, nullable=True, index=True)  # When they joined this service
    
    # Per-service whitelist settings
    is_discord_bot_whitelisted = db.Column(db.Boolean, default=False, nullable=False)  # Per-service bot whitelist
    is_purge_whitelisted = db.Column(db.Boolean, default=False, nullable=False)  # Per-service purge whitelist
    
    # Service-specific user status (primarily for Plex)
    is_home_user = db.Column(db.Boolean, default=False, nullable=False)  # Plex Home User status
    shares_back = db.Column(db.Boolean, default=False, nullable=False)  # User shares libraries back to admin
    
    # Service-specific Discord linking (can be different per service)
    discord_user_id = db.Column(db.String(255), nullable=True)  # Service-specific Discord linking
    discord_username = db.Column(db.String(255), nullable=True)  # Service-specific Discord username
    
    # Service-specific expiration (can override global expiration)
    access_expires_at = db.Column(db.DateTime, nullable=True, index=True)  # Service-specific expiration
    
    # New raw data fields
    user_raw_data = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)  # JSON for /users page "i" button modal
    stream_raw_data = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)  # JSON for streaming-related raw data
    
    # Relationships
    user_app_access = db.relationship('UserAppAccess', back_populates='media_accesses')
    server = db.relationship('MediaServer', back_populates='user_accesses')
    invite = db.relationship('Invite', backref='user_media_accesses_created')
    
    # Unique constraints:
    # 1. For linked users: user_app_access_id + server_id must be unique
    # 2. For standalone users: external_user_id + server_id must be unique
    __table_args__ = (
        db.UniqueConstraint('user_app_access_id', 'server_id', name='_user_app_access_server_uc'),
        db.UniqueConstraint('external_user_id', 'server_id', name='_external_user_server_uc'),
    )
    
    def __repr__(self):
        username = self.external_username or self.user_app_access.username if self.user_app_access else 'Unknown'
        server_name = self.server.server_nickname if self.server else 'Unknown Server'
        return f'<UserMediaAccess {username} on {server_name}>'
    
    def get_display_name(self):
        """Get the best display name for this user on this service"""
        return self.external_username or (self.user_app_access.username if self.user_app_access else 'Unknown User')
    
    def get_service_email(self):
        """Get the service-specific email"""
        return self.external_email or (self.user_app_access.email if self.user_app_access else None)
    
    def get_avatar_url(self):
        """Get the service-specific avatar URL"""
        return self.external_avatar_url
    
    def is_expired(self):
        """Check if this service access is expired"""
        from datetime import datetime, timezone
        
        # Check service-specific expiration first
        if self.access_expires_at:
            return datetime.now(timezone.utc) > self.access_expires_at.replace(tzinfo=timezone.utc)
        
        # Check global expiration if no service-specific expiration
        if self.user_app_access and self.user_app_access.access_expires_at:
            return datetime.now(timezone.utc) > self.user_app_access.access_expires_at.replace(tzinfo=timezone.utc)
        
        return False
    
    def get_effective_expiration(self):
        """Get the effective expiration date (service-specific takes precedence over global)"""
        return self.access_expires_at or (self.user_app_access.access_expires_at if self.user_app_access else None)
    
    def get_service_type(self):
        """Get the service type for this access"""
        return self.server.service_type.value if self.server else 'unknown'
    
    def has_library_access(self, library_id):
        """Check if user has access to a specific library"""
        if not self.allowed_library_ids:
            return False
        return str(library_id) in [str(lib_id) for lib_id in self.allowed_library_ids]
    
    def get_raw_data(self, data_type='user'):
        """Get raw data for this service access"""
        if data_type == 'user':
            return self.user_raw_data or {}
        elif data_type == 'stream':
            return self.stream_raw_data or {}
        return {}
    
    def set_raw_data(self, data_type, data):
        """Set raw data for this service access"""
        if data_type == 'user':
            self.user_raw_data = data or {}
        elif data_type == 'stream':
            self.stream_raw_data = data or {}

class MediaItem(db.Model):
    """Store individual media items from libraries for faster access and search"""
    __tablename__ = 'media_items'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Foreign keys
    library_id = db.Column(db.Integer, db.ForeignKey('media_libraries.id'), nullable=False)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False)
    
    # External identifiers
    external_id = db.Column(db.String(255), nullable=False)  # ID from the media service
    parent_id = db.Column(db.String(255))  # Parent folder/collection ID
    rating_key = db.Column(db.String(255), nullable=True)  # Plex ratingKey for direct API access
    
    # Basic metadata
    title = db.Column(db.String(500), nullable=False)
    sort_title = db.Column(db.String(500))  # For proper sorting
    item_type = db.Column(db.String(50), nullable=False)  # movie, show, episode, folder, etc.
    
    # Content metadata
    summary = db.Column(db.Text)
    year = db.Column(db.Integer)
    rating = db.Column(db.Float)
    duration = db.Column(db.BigInteger)  # Duration in milliseconds/ticks
    
    # Media file info
    file_path = db.Column(db.String(1000))
    file_size = db.Column(db.BigInteger)
    
    # Thumbnail/artwork
    thumb_path = db.Column(db.String(500))  # Relative path for proxy
    art_path = db.Column(db.String(500))
    
    # Timestamps
    added_at = db.Column(db.DateTime)  # When added to media server
    updated_at = db.Column(db.DateTime)  # Last modified on media server
    last_synced = db.Column(db.DateTime, default=datetime.utcnow)  # When we last synced this item
    
    # Additional metadata (JSON for flexibility)
    extra_metadata = db.Column(db.JSON)
    
    # Relationships
    library = db.relationship('MediaLibrary', backref='media_items')
    server = db.relationship('MediaServer', backref='media_items')
    
    # Indexes for performance
    __table_args__ = (
        db.Index('idx_media_items_library_type', 'library_id', 'item_type'),
        db.Index('idx_media_items_external_id', 'external_id'),
        db.Index('idx_media_items_rating_key', 'rating_key'),
        db.Index('idx_media_items_title', 'title'),
        db.Index('idx_media_items_year', 'year'),
        db.Index('idx_media_items_added_at', 'added_at'),
        db.Index('idx_media_items_last_synced', 'last_synced'),
        db.UniqueConstraint('library_id', 'external_id', name='uq_library_external_id'),
    )
    
    def __repr__(self):
        return f'<MediaItem {self.title} ({self.item_type})>'
    
    def to_dict(self):
        """Convert to dictionary format compatible with current media grid"""
        # Handle different thumbnail formats for different services
        thumb_url = None
        if self.thumb_path:
            if self.thumb_path.startswith('/api/'):
                # Already a proxy URL (Jellyfin or other services)
                thumb_url = self.thumb_path
            else:
                # Plex format: regular path that needs proxy construction
                thumb_url = f"/api/media/{self.server.service_type.value}/images/proxy?path={self.thumb_path.lstrip('/')}"
        
        # Extract season and episode numbers for episodes
        season_number = None
        episode_number = None
        if self.item_type == 'episode' and self.extra_metadata:
            # Try different possible field names for season/episode numbers
            # Use 'is not None' to handle season 0 (specials) correctly
            if self.extra_metadata.get('seasonNumber') is not None:
                season_number = self.extra_metadata.get('seasonNumber')
            elif self.extra_metadata.get('season_number') is not None:
                season_number = self.extra_metadata.get('season_number')
            elif self.extra_metadata.get('season') is not None:
                season_number = self.extra_metadata.get('season')
            elif self.extra_metadata.get('parentIndex') is not None:
                season_number = self.extra_metadata.get('parentIndex')
            
            if self.extra_metadata.get('episodeNumber') is not None:
                episode_number = self.extra_metadata.get('episodeNumber')
            elif self.extra_metadata.get('episode_number') is not None:
                episode_number = self.extra_metadata.get('episode_number')
            elif self.extra_metadata.get('episode') is not None:
                episode_number = self.extra_metadata.get('episode')
            elif self.extra_metadata.get('index') is not None:
                episode_number = self.extra_metadata.get('index')

        return {
            'id': self.id,  # Use database ID for new URL structure
            'external_id': self.external_id,  # Keep external_id for backward compatibility
            'title': self.title,
            'year': self.year,
            'season_number': season_number,  # Add season number for episodes
            'episode_number': episode_number,  # Add episode number for episodes
            'edition': self.extra_metadata.get('edition') if self.extra_metadata else None,
            'thumb': thumb_url,
            'type': self.item_type,
            'summary': self.summary,
            'rating': self.rating,
            'duration': self.duration,
            'added_at': self.added_at.isoformat() if self.added_at else None,
            'raw_data': self.extra_metadata or {}
        }

class MediaStreamHistory(db.Model):
    """Enhanced stream history that supports multiple services"""
    __tablename__ = 'media_stream_history'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Relationships - Support both UserAppAccess (linked) and UserMediaAccess (standalone) using UUIDs
    user_app_access_uuid = db.Column(db.String(36), db.ForeignKey('user_app_access.uuid'), nullable=True, index=True)
    user_media_access_uuid = db.Column(db.String(36), db.ForeignKey('user_media_access.uuid'), nullable=True, index=True)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False, index=True)
    
    # Session Details
    session_key = db.Column(db.String(255), nullable=True)
    external_session_id = db.Column(db.String(255), nullable=True)  # Service-specific session ID
    rating_key = db.Column(db.String(255), nullable=True)
    
    # Stream Timestamps
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    stopped_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    
    # Client Info
    platform = db.Column(db.String(255), nullable=True)
    product = db.Column(db.String(255), nullable=True)
    player = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    is_lan = db.Column(db.Boolean, default=False)
    
    # Media Info
    media_title = db.Column(db.String(255), nullable=True)
    media_type = db.Column(db.String(50), nullable=True)
    grandparent_title = db.Column(db.String(255), nullable=True)
    parent_title = db.Column(db.String(255), nullable=True)
    library_name = db.Column(db.String(255), nullable=True)
    external_media_item_id = db.Column(db.String(255), nullable=True)  # Media ID from service (media array for Plex, rating_key for shows)
    
    media_duration_seconds = db.Column(db.Integer, nullable=True)
    view_offset_at_end_seconds = db.Column(db.Integer, nullable=True)
    
    # Service-specific data stored as JSON
    service_data = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)
    
    # Relationships - Updated to support both UserAppAccess and UserMediaAccess using UUIDs
    user_app_access = db.relationship('UserAppAccess', foreign_keys=[user_app_access_uuid], backref=db.backref('media_stream_history', lazy='dynamic', cascade="all, delete-orphan"))
    user_media_access = db.relationship('UserMediaAccess', foreign_keys=[user_media_access_uuid], backref=db.backref('media_stream_history', lazy='dynamic', cascade="all, delete-orphan"))
    server = db.relationship('MediaServer', backref=db.backref('stream_history', lazy='dynamic'))
    
    def __repr__(self):
        username = self.user_app_access.get_display_name() if self.user_app_access else 'Unknown User'
        server_name = self.server.server_nickname if self.server else 'Unknown Server'
        return f'<MediaStreamHistory {self.id} by {username} on {server_name}>'
    
    def get_user_display_name(self):
        """Get the display name of the user who streamed this content"""
        if self.user_app_access:
            return self.user_app_access.get_display_name()
        elif self.user_media_access:
            return self.user_media_access.get_display_name()
        else:
            return 'Unknown User'
    
    def get_server_name(self):
        """Get the name of the server where this was streamed"""
        return self.server.server_nickname if self.server else 'Unknown Server'
    
    def get_service_type(self):
        """Get the service type where this was streamed"""
        return self.server.service_type.value if self.server else 'unknown'
    
    def get_duration_formatted(self):
        """Get formatted duration string"""
        if not self.duration_seconds:
            return "Unknown"
        
        hours = self.duration_seconds // 3600
        minutes = (self.duration_seconds % 3600) // 60
        seconds = self.duration_seconds % 60
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def is_completed(self):
        """Check if the stream session is completed (has stopped_at)"""
        return self.stopped_at is not None
    
    def get_completion_percentage(self):
        """Get the percentage of media that was watched"""
        if not self.media_duration_seconds or not self.view_offset_at_end_seconds:
            return 0
        
        percentage = (self.view_offset_at_end_seconds / self.media_duration_seconds) * 100
        return min(100, max(0, percentage))  # Clamp between 0 and 100