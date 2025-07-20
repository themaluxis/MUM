# File: app/models_media_services.py
import enum
import json
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

class MediaServer(db.Model):
    """Represents a media server instance (can have multiple servers of same type)"""
    __tablename__ = 'media_servers'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)  # User-friendly name - must be unique
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
    
    # Relationships
    libraries = db.relationship('MediaLibrary', back_populates='server', cascade="all, delete-orphan")
    user_accesses = db.relationship('UserMediaAccess', back_populates='server', cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<MediaServer {self.name} ({self.service_type.value})>'
    
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
        return f'<MediaLibrary {self.name} on {self.server.name}>'

class UserMediaAccess(db.Model):
    """Represents a user's access to a specific media server"""
    __tablename__ = 'user_media_access'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False)
    
    # Service-specific user ID
    external_user_id = db.Column(db.String(255), nullable=True)
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
    access_expires_at = db.Column(db.DateTime, nullable=True)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('media_accesses', lazy='dynamic', cascade="all, delete-orphan"))
    server = db.relationship('MediaServer', back_populates='user_accesses')
    
    # Unique constraint on user_id + server_id
    __table_args__ = (db.UniqueConstraint('user_id', 'server_id', name='_user_server_uc'),)
    
    def __repr__(self):
        return f'<UserMediaAccess {self.user.plex_username} on {self.server.name}>'

class MediaStreamHistory(db.Model):
    """Enhanced stream history that supports multiple services"""
    __tablename__ = 'media_stream_history'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    server_id = db.Column(db.Integer, db.ForeignKey('media_servers.id'), nullable=False)
    
    # Session Details
    session_key = db.Column(db.String(255), nullable=True)
    external_session_id = db.Column(db.String(255), nullable=True)  # Service-specific session ID
    rating_key = db.Column(db.String(255), nullable=True)
    
    # Stream Timestamps
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
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
    
    media_duration_seconds = db.Column(db.Integer, nullable=True)
    view_offset_at_end_seconds = db.Column(db.Integer, nullable=True)
    
    # Service-specific data stored as JSON
    service_data = db.Column(MutableDict.as_mutable(JSONEncodedDict), default=dict)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('media_stream_history', lazy='dynamic', cascade="all, delete-orphan"))
    server = db.relationship('MediaServer')
    
    def __repr__(self):
        return f'<MediaStreamHistory {self.id} by {self.user.plex_username} on {self.server.name}>'