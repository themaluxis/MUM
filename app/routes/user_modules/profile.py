"""User profile viewing and management functionality"""

from flask import render_template, redirect, url_for, flash, request, current_app, abort, make_response
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
from app.models import User, UserType, EventType
from app.models_media_services import MediaStreamHistory, ServiceType
from app.utils.helpers import permission_required, log_event
from app.extensions import db
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager
from app.services import user_service
from app.forms import UserEditForm
from . import user_bp
from .helpers import check_if_user_is_admin, enhance_history_records_with_media_ids
import urllib.parse
import json

@user_bp.route('/profile')
@login_required
def profile():
    """Redirect to user profile based on current user type"""
    
    if current_user.userType == UserType.OWNER:
        # Owner should see the first local user's profile or create one
        first_local_user = User.query.filter_by(userType=UserType.LOCAL).first()
        if first_local_user:
            return redirect(url_for('user.view_app_user', username=first_local_user.localUsername))
        else:
            flash('No local user account found.', 'warning')
            return redirect(url_for('admin_management.list_admins'))
    
    elif current_user.userType == UserType.LOCAL:
        # Regular user sees their own profile
        return redirect(url_for('user.view_app_user', username=current_user.localUsername))
    
    else:
        # Service users shouldn't access this endpoint directly
        flash('Service users cannot access user profiles directly.', 'error')
        return redirect(url_for('auth.app_login'))


@user_bp.route('/<username>')
@login_required
@permission_required('view_user')
def view_app_user(username):
    """View local user account profile by username"""
    from app.models_media_services import MediaServer
    
    # URL decode the username to handle special characters
    try:
        username = urllib.parse.unquote(username)
    except Exception as e:
        current_app.logger.warning(f"Error decoding username: {e}")
        abort(400)
    
    # Find the local user
    user = User.get_by_local_username(username)
    if not user:
        abort(404)
    
    # Get the active tab from the URL query parameter
    tab = request.args.get('tab', 'profile')
    
    # Get streaming stats and history for the user
    stream_stats = user_service.get_user_stream_stats(user.uuid)
    last_ip_map = user_service.get_bulk_last_known_ips([user.uuid])
    last_ip = last_ip_map.get(str(user.uuid))
    user.stream_stats = stream_stats
    user.total_plays = stream_stats.get('global', {}).get('all_time_plays', 0)
    user.total_duration = stream_stats.get('global', {}).get('all_time_duration_seconds', 0)
    user.last_known_ip = last_ip if last_ip else 'N/A'
    
    # Get streaming history for the history tab
    page = request.args.get('page', 1, type=int)
    stream_history_pagination = None
    
    if tab == 'history':
        stream_history_pagination = MediaStreamHistory.query.filter_by(user_uuid=user.uuid)\
            .order_by(MediaStreamHistory.started_at.desc())\
            .paginate(page=page, per_page=15, error_out=False)
        
        # Enhance history records with MediaItem database IDs for clickable links
        enhance_history_records_with_media_ids(stream_history_pagination.items)
    
    # Get linked service accounts
    linked_service_users = User.query.filter_by(userType=UserType.SERVICE, linkedUserId=user.uuid).all()
    
    # Get user service types and server names for service-aware display
    user_service_types = {user.uuid: []}
    user_server_names = {user.uuid: []}
    
    for service_user in linked_service_users:
        if hasattr(service_user, 'server') and service_user.server:
            if service_user.server.service_type not in user_service_types[user.uuid]:
                user_service_types[user.uuid].append(service_user.server.service_type)
            if service_user.server.server_nickname not in user_server_names[user.uuid]:
                user_server_names[user.uuid].append(service_user.server.server_nickname)
    
    # Context variables for template
    user_sorted_libraries = {}
    
    # For HTMX requests on history tab, return just the content
    if request.headers.get('HX-Request') and tab == 'history':
        return render_template('user/_partials/profile_tabs/history_tab_content.html', 
                             user=user, 
                             history_logs=stream_history_pagination,
                             user_service_types=user_service_types,
                             user_server_names=user_server_names)
    
    return render_template(
        'user/index.html',
        title=f"User Profile: {user.get_display_name()}",
        user=user,
        user_sorted_libraries=user_sorted_libraries,
        history_logs=stream_history_pagination,
        active_tab=tab,
        is_admin=check_if_user_is_admin(user),
        is_service_user=False,
        stream_stats=stream_stats,
        user_service_types=user_service_types,
        user_server_names=user_server_names,
        linked_service_users=linked_service_users,
        current_user=current_user,
        now_utc=datetime.now(timezone.utc)
    )


class MockServiceUser:
    """Mock user object that behaves like a User for service accounts"""
    
    def __init__(self, access_record):
        self._access_record = access_record
        # Map UserMediaAccess fields to User-like attributes
        self.id = access_record.id
        self.uuid = access_record.uuid
        self.userType = UserType.SERVICE
        self.created_at = access_record.created_at
        self.updated_at = access_record.updated_at
        self.notes = getattr(access_record, 'notes', None)
        self.is_discord_bot_whitelisted = access_record.is_discord_bot_whitelisted
        self.is_purge_whitelisted = access_record.is_purge_whitelisted
        self.allow_4k_transcode = access_record.allow_4k_transcode
        self.access_expires_at = access_record.access_expires_at
        
        # Set service user flag for template logic
        self._is_service_user = True
        self._user_type = 'service'
        
        # Attributes for template display
        self.username = access_record.external_username
        self.discord_username = None  # Service users don't have Discord usernames
        self.discord_email = None     # Service users don't have Discord emails
        self.server = access_record.server
        
    def get_avatar_url(self):
        """Get avatar URL for service user"""
        access = self._access_record
        avatar_url = "/static/img/favicon.ico"  # Default
        
        if access.server.service_type.value.lower() == 'plex':
            # For Plex, construct the avatar URL using the server's URL and the user's thumb
            if access.external_user_avatar:
                # Remove '/library/metadata/' prefix if present and construct full URL
                thumb_path = access.external_user_avatar
                if thumb_path.startswith('/library/metadata/'):
                    # Remove the prefix, we'll construct the full URL
                    thumb_path = thumb_path.replace('/library/metadata/', '')
                server_url = access.server.server_url.rstrip('/')
                avatar_url = f"{server_url}/photo/:/transcode?width=150&height=150&minSize=1&upscale=1&url=/library/metadata/{thumb_path}"
        
        elif access.server.service_type.value.lower() == 'emby':
            # For Emby, use the external_user_id to get avatar  
            if access.external_user_id:
                avatar_url = f"/api/media/emby/users/avatar?user_id={access.external_user_id}"
        
        elif access.server.service_type.value.lower() == 'jellyfin':
            # For Jellyfin, use the external_user_id to get avatar
            if access.external_user_id:
                avatar_url = f"/api/media/jellyfin/users/avatar?user_id={access.external_user_id}"
        
        return avatar_url
    
    def get_display_name(self):
        return self._access_record.external_username or 'Unknown'


# ROUTE REMOVED: This route conflicted with admin route at /admin/user/<server_nickname>/<server_username>
# Service user profiles are now only accessible via the admin route to maintain proper access control
# The admin route now includes all the rich data that was previously only available here