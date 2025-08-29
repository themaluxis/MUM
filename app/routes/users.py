# File: app/routes/users.py
from flask import Blueprint, render_template, request, current_app, session, make_response, redirect, url_for, flash 
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.models import UserAppAccess, Setting, EventType, Owner
from app.models_media_services import ServiceType, MediaStreamHistory
from app.models_media_services import ServiceType
from app.forms import UserEditForm, MassUserEditForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.services import user_service
from app.services.unified_user_service import UnifiedUserService
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
import json
from datetime import datetime, timezone, timedelta # Ensure these are imported
from sqlalchemy.exc import IntegrityError

bp = Blueprint('users', __name__)

# parse_user_id function removed - now using UUID-based identification only

# Library data is now fetched from database instead of API calls

def get_libraries_from_database(servers):
    """Get library data from database - NO API CALLS"""
    from app.models_media_services import MediaLibrary
    
    libraries_by_server = {}
    
    for server in servers:
        # Get libraries from database for this server
        db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
        server_lib_dict = {}
        
        for lib in db_libraries:
            # Use external_id as the key (this matches what the API would return)
            lib_id = lib.external_id
            lib_name = lib.name
            if lib_id:
                server_lib_dict[str(lib_id)] = lib_name
        
        libraries_by_server[server.id] = server_lib_dict
    
    return libraries_by_server

def _get_local_user_avatar_url(app_user):
    """Get avatar URL for local users by checking their linked media access accounts"""
    from app.models_media_services import UserMediaAccess
    
    # Get all media access records for this local user
    access_records = UserMediaAccess.query.filter_by(user_app_access_id=app_user.id).all()
    
    for access in access_records:
        # First check for external avatar URL
        if access.external_avatar_url:
            return access.external_avatar_url
        elif access.server.service_type.value.lower() == 'plex':
            # For Plex, check multiple possible locations for the thumb URL
            thumb_url = None
            
            # First try service_settings
            if access.service_settings and access.service_settings.get('thumb'):
                thumb_url = access.service_settings['thumb']
            # Then try raw_data from the user sync
            elif access.user_raw_data and access.user_raw_data.get('thumb'):
                thumb_url = access.user_raw_data['thumb']
            # Also check nested raw data structure
            elif (access.user_raw_data and 
                  access.user_raw_data.get('plex_user_obj_attrs') and 
                  access.user_raw_data['plex_user_obj_attrs'].get('thumb')):
                thumb_url = access.user_raw_data['plex_user_obj_attrs']['thumb']
            
            if thumb_url:
                # Check if it's already a full URL (plex.tv avatars) or needs proxy
                if thumb_url.startswith('https://plex.tv/') or thumb_url.startswith('http://plex.tv/'):
                    return thumb_url
                else:
                    return f"/api/media/plex/images/proxy?path={thumb_url.lstrip('/')}"
        
        elif access.server.service_type.value.lower() == 'jellyfin':
            # For Jellyfin, use the external_user_id to get avatar
            if access.external_user_id:
                return f"/api/media/jellyfin/users/avatar?user_id={access.external_user_id}"
    
    # No avatar found
    return None

@bp.route('/')
@login_required
@setup_required
@permission_required('view_users')
def list_users():
    # Redirect regular users away from admin pages
    if isinstance(current_user, UserAppAccess) and not current_user.has_permission('view_users'):
        flash('You do not have permission to access the users management page.', 'danger')
        return redirect(url_for('user.index'))
    
    import time
    start_time = time.time()
    current_app.logger.debug(f"Loading users page for user {current_user.id}")
    
    is_htmx = request.headers.get('HX-Request')

    # If it's a direct browser load and 'view' is missing from the URL
    if 'view' not in request.args and not is_htmx:
        # Get the preferred view, default to 'cards' if not set
        # Only Owner has preferred_user_list_view, AppUser doesn't
        if hasattr(current_user, 'preferred_user_list_view'):
            preferred_view = current_user.preferred_user_list_view or 'cards'
        else:
            preferred_view = 'cards'  # Default for AppUser
        
        # Preserve other query params and redirect
        args = request.args.to_dict()
        args['view'] = preferred_view
        return redirect(url_for('users.list_users', **args))

    # For all other cases (redirected request or HTMX), determine view_mode from args
    view_mode = request.args.get('view', 'cards')

    page = request.args.get('page', 1, type=int)
   
    session_per_page_key = 'users_list_per_page'
    default_per_page_config = current_app.config.get('DEFAULT_USERS_PER_PAGE', 12)
    try:
        items_per_page = int(request.args.get('per_page'))
        if items_per_page not in [12, 24, 48, 96]:
            raise ValueError("Invalid per_page value from request.args")
        session[session_per_page_key] = items_per_page
    except (TypeError, ValueError):
        items_per_page = session.get(session_per_page_key, default_per_page_config)
        if items_per_page not in [12, 24, 48, 96]:
            items_per_page = default_per_page_config
            session[session_per_page_key] = items_per_page

    # Check if we should show local users, service users, or both
    user_type_filter = request.args.get('user_type', 'all')  # 'all', 'local', 'service'
    
    # Handle separate search fields
    search_username = request.args.get('search_username', '').strip()
    search_email = request.args.get('search_email', '').strip()
    search_notes = request.args.get('search_notes', '').strip()
    
    # Legacy search field for backward compatibility with the main search bar
    search_term = request.args.get('search', '').strip()
    
    # Get both local users and service users
    current_app.logger.info(f"=== USERS LIST DEBUG: Loading users page ===")
    current_app.logger.info(f"User type filter: {user_type_filter}")
    current_app.logger.info(f"Search filters - username: '{search_username}', email: '{search_email}', notes: '{search_notes}', term: '{search_term}'")
    
    app_users = []
    service_users = []
    
    if user_type_filter in ['all', 'local']:
        # Query local users (UserAppAccess records)
        current_app.logger.info("=== QUERYING LOCAL USERS ===")
        app_user_query = UserAppAccess.query
        
        # Build search filters for local users
        local_search_filters = []
        if search_username:
            local_search_filters.append(UserAppAccess.username.ilike(f"%{search_username}%"))
        if search_email:
            local_search_filters.append(UserAppAccess.email.ilike(f"%{search_email}%"))
        if search_notes:
            local_search_filters.append(UserAppAccess.notes.ilike(f"%{search_notes}%"))
        if search_term:
            local_search_filters.append(or_(UserAppAccess.username.ilike(f"%{search_term}%"), UserAppAccess.email.ilike(f"%{search_term}%")))
        
        if local_search_filters:
            app_user_query = app_user_query.filter(or_(*local_search_filters))
        
        app_users = app_user_query.all()
        current_app.logger.info(f"Found {len(app_users)} app users")
        for app_user in app_users:
            linked_count = len(app_user.media_accesses)
            current_app.logger.info(f"  App user: {app_user.username} (ID: {app_user.id}) - {linked_count} media accesses")
            for media_access in app_user.media_accesses:
                current_app.logger.info(f"    Media access: {media_access.external_username} (ID: {media_access.id}, Server: {media_access.server.server_nickname if media_access.server else 'Unknown'})")
    
    if user_type_filter in ['all', 'service']:
        # Query service users - these are standalone UserMediaAccess records without linked UserAppAccess
        current_app.logger.info("=== QUERYING SERVICE USERS ===")
        from app.models_media_services import UserMediaAccess
        
        # Get ALL UserMediaAccess records (both standalone and linked)
        # We want to show each service account as a separate card
        all_access_query = UserMediaAccess.query
        
        if user_type_filter == 'service':
            # If only service users requested, get standalone access records
            pass  # We'll process the query below
        else:
            # If 'all' users requested, we still need to get standalone service users
            pass  # We'll process the query below
        
        # Build search filters for standalone service users
        search_filters = []
        if search_username:
            search_filters.append(UserMediaAccess.external_username.ilike(f"%{search_username}%"))
        if search_email:
            search_filters.append(UserMediaAccess.external_email.ilike(f"%{search_email}%"))
        if search_notes:
            search_filters.append(UserMediaAccess.notes.ilike(f"%{search_notes}%"))
        if search_term:
            # Legacy search - search both username and email
            search_filters.append(or_(
                UserMediaAccess.external_username.ilike(f"%{search_term}%"), 
                UserMediaAccess.external_email.ilike(f"%{search_term}%")
            ))
    
        # Apply search filters if any exist
        if search_filters:
            all_access_query = all_access_query.filter(or_(*search_filters))

        # Apply server filter
        server_filter_id = request.args.get('server_id', 'all')
        if server_filter_id != 'all':
            try:
                server_filter_id_int = int(server_filter_id)
                all_access_query = all_access_query.filter(UserMediaAccess.server_id == server_filter_id_int)
            except ValueError:
                current_app.logger.warning(f"Invalid server_id received: {server_filter_id}")

        # Get the standalone access records
        all_access_records = all_access_query.all()
        
        # Convert UserMediaAccess records to a user-like format for display
        service_users = []
        current_app.logger.info(f"DEBUG: Found {len(all_access_records)} total access records (standalone + linked)")
        
        for access in all_access_records:
            current_app.logger.info(f"DEBUG: Creating mock user for access ID {access.id}, username: {access.external_username}, server: {access.server.server_nickname}")
            # Create a mock user object with the necessary attributes for display
            class MockUser:
                def __init__(self, access):
                    # Store UUID for identification
                    self.uuid = access.uuid
                    self.id = access.id  # Keep original numeric ID
                    self.username = access.external_username or 'Unknown'
                    self.email = access.external_email
                    self.notes = access.notes
                    self.created_at = access.created_at
                    self.last_login_at = access.last_activity_at
                    self.media_accesses = [access]  # This user has only this one access
                    self.access_expires_at = access.access_expires_at
                    self.discord_user_id = access.discord_user_id
                    self.is_active = access.is_active
                    self._is_standalone = True  # Flag to identify standalone users
                    self._access_record = access  # Store the original access record
                    
                    # Process avatar URL using the same logic as library stats
                    self.avatar_url = self._get_avatar_url(access)
                    
                    # Add last_streamed_at property for template compatibility
                    self.last_streamed_at = None
                    from app.models_media_services import MediaStreamHistory
                    
                    if access.user_app_access_id:
                        # This is a linked service account, get streaming history from UserAppAccess UUID
                        last_stream = MediaStreamHistory.query.filter_by(
                            user_app_access_uuid=access.user_app_access.uuid if access.user_app_access else None
                        ).order_by(MediaStreamHistory.started_at.desc()).first()
                        self.last_streamed_at = last_stream.started_at if last_stream else None
                    else:
                        # This is a standalone service account, get streaming history from UserMediaAccess UUID
                        last_stream = MediaStreamHistory.query.filter_by(
                            user_media_access_uuid=access.uuid
                        ).order_by(MediaStreamHistory.started_at.desc()).first()
                        self.last_streamed_at = last_stream.started_at if last_stream else None
                
                def _get_avatar_url(self, access):
                    """Process avatar URL using the same logic as library stats chart"""
                    avatar_url = None
                    
                    # First check for external avatar URL
                    if access.external_avatar_url:
                        avatar_url = access.external_avatar_url
                    elif access.server.service_type.value.lower() == 'plex':
                        # For Plex, check multiple possible locations for the thumb URL
                        thumb_url = None
                        
                        # First try service_settings
                        if access.service_settings and access.service_settings.get('thumb'):
                            thumb_url = access.service_settings['thumb']
                        # Then try raw_data from the user sync
                        elif access.user_raw_data and access.user_raw_data.get('thumb'):
                            thumb_url = access.user_raw_data['thumb']
                        # Also check nested raw data structure
                        elif (access.user_raw_data and 
                              access.user_raw_data.get('plex_user_obj_attrs') and 
                              access.user_raw_data['plex_user_obj_attrs'].get('thumb')):
                            thumb_url = access.user_raw_data['plex_user_obj_attrs']['thumb']
                        
                        if thumb_url:
                            # Check if it's already a full URL (plex.tv avatars) or needs proxy
                            if thumb_url.startswith('https://plex.tv/') or thumb_url.startswith('http://plex.tv/'):
                                avatar_url = thumb_url
                            else:
                                avatar_url = f"/api/media/plex/images/proxy?path={thumb_url.lstrip('/')}"
                    
                    elif access.server.service_type.value.lower() == 'jellyfin':
                        # For Jellyfin, use the external_user_id to get avatar
                        if access.external_user_id:
                            avatar_url = f"/api/media/jellyfin/users/avatar?user_id={access.external_user_id}"
                    
                    return avatar_url
                
                def get_display_name(self):
                    return self._access_record.external_username or 'Unknown'
                
                def get_avatar(self, default_url=None):
                    """Return avatar URL for MockUser - service users typically don't have avatars"""
                    return default_url
            
            mock_user = MockUser(access)
            mock_user._user_type = 'service'  # Add type for processing
            service_users.append(mock_user)
            current_app.logger.info(f"DEBUG: Added service user {mock_user.username} (UUID: {mock_user.uuid}) to list")
        
        current_app.logger.info(f"Found {len(service_users)} standalone service users")
        for service_user in service_users:
            current_app.logger.info(f"  Standalone service user: {service_user.username} (Access ID: {service_user._access_record.id}, Server: {service_user._access_record.server.server_nickname})")
            
            # Add service type information for standalone users so they get proper colors
            if not hasattr(service_user, '_user_type'):
                service_user._user_type = 'service'
    
    # Combine and paginate results
    all_users = []
    
    # Add local users with a type indicator and process their avatars
    current_app.logger.info(f"DEBUG: Found {len(app_users)} local users")
    for app_user in app_users:
        app_user._user_type = 'local'
        # Process avatar URL for local users using their linked media access accounts
        app_user.avatar_url = _get_local_user_avatar_url(app_user)
        # UUID is already available on the user object
        all_users.append(app_user)
        current_app.logger.info(f"DEBUG: Local user {app_user.username} (UUID: {app_user.uuid}) added to list")
    
    # Add service users with a type indicator  
    for service_user in service_users:
        service_user._user_type = 'service'
        all_users.append(service_user)
    
    # Store sort parameters for later use (after we populate streaming data)
    sort_by_param = request.args.get('sort_by', 'username_asc')
    
    # For last_streamed sorting, we need to populate streaming data first before sorting
    # For other sorts, we can sort immediately
    if 'last_streamed' not in sort_by_param:
        # Sort combined results immediately for non-streaming sorts
        reverse_sort = 'desc' in sort_by_param
        if 'username' in sort_by_param:
            all_users.sort(key=lambda u: getattr(u, 'username', '').lower(), reverse=reverse_sort)
        elif 'created_at' in sort_by_param:
            all_users.sort(key=lambda u: getattr(u, 'created_at', datetime.min.replace(tzinfo=timezone.utc)) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse_sort)
        elif 'email' in sort_by_param:
            all_users.sort(key=lambda u: getattr(u, 'email', '').lower(), reverse=reverse_sort)
    
    # For last_streamed sorting, we need to process ALL users before pagination
    # For other sorts, we can paginate first to improve performance
    total_users = len(all_users)
    
    if 'last_streamed' in sort_by_param:
        users_on_page = all_users  # Process all users for streaming data
    else:
        # Manual pagination for combined results
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        users_on_page = all_users[start_idx:end_idx]
    
    # Debug: Check for AllGas users specifically
    allgas_users = [user for user in all_users if user.username == 'AllGas']
    current_app.logger.info(f"DEBUG: Found {len(allgas_users)} AllGas users in total list")
    for user in allgas_users:
        server_name = getattr(user._access_record, 'server', {}).server_nickname if hasattr(user, '_access_record') else 'N/A'
        current_app.logger.info(f"DEBUG: AllGas user - UUID: {getattr(user, 'uuid', user.id)}, type: {getattr(user, '_user_type', 'unknown')}, server: {server_name}")
    
    allgas_on_page = [user for user in users_on_page if user.username == 'AllGas']
    if 'last_streamed' in sort_by_param:
        current_app.logger.info(f"DEBUG: Found {len(allgas_on_page)} AllGas users on current page (processing all users for last_streamed sort)")
    else:
        current_app.logger.info(f"DEBUG: Found {len(allgas_on_page)} AllGas users on current page (start: {start_idx}, end: {end_idx})")
    for user in allgas_on_page:
        current_app.logger.info(f"DEBUG: AllGas on page - UUID: {getattr(user, 'uuid', user.id)}, type: {getattr(user, '_user_type', 'unknown')}")
    
    # Create a mock pagination object
    class MockPagination:
        def __init__(self, items, page, per_page, total):
            self.items = items
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = (total + per_page - 1) // per_page
            self.has_prev = page > 1
            self.has_next = page < self.pages
            self.prev_num = page - 1 if self.has_prev else None
            self.next_num = page + 1 if self.has_next else None
        
        def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=3):
            """Generate page numbers for pagination, similar to Flask-SQLAlchemy's pagination"""
            last = self.pages
            
            # Generate the page numbers to show
            for num in range(1, last + 1):
                if num <= left_edge or \
                   (self.page - left_current - 1 < num < self.page + right_current) or \
                   num > last - right_edge:
                    yield num
                elif num == left_edge + 1 or num == last - right_edge:
                    # Add None to represent ellipsis
                    yield None
    
    users_pagination = MockPagination(users_on_page, page, items_per_page, total_users)
    
    # Extract sort information for template
    sort_by_param = request.args.get('sort_by', 'username_asc')
    sort_parts = sort_by_param.rsplit('_', 1)
    sort_column = sort_parts[0]
    sort_direction = 'desc' if len(sort_parts) > 1 and sort_parts[1] == 'desc' else 'asc'

    # Get Owner with plex_uuid for filtering (AppUsers don't have plex_uuid)
    owner = Owner.query.filter(Owner.plex_uuid.isnot(None)).first()
    admin_accounts = [owner] if owner else []
    admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts}
    
    # Get user IDs for additional data - extract actual IDs from prefixed format
    user_ids_on_page = []  # Actual UserMediaAccess IDs for service users
    app_user_ids_on_page = []  # Actual UserAppAccess IDs for local users
    
    for user in users_on_page:
        if hasattr(user, '_user_type'):
            # Use actual database IDs directly since we're working with real user objects
            if user._user_type == 'service':
                user_ids_on_page.append(user.id)
            elif user._user_type == 'local':
                app_user_ids_on_page.append(user.id)

    # Fetch additional data for service users only (using actual IDs)
    stream_stats = {}
    last_ips = {}
    if user_ids_on_page:
        stream_stats = user_service.get_bulk_user_stream_stats(user_ids_on_page)
        last_ips = user_service.get_bulk_last_known_ips(user_ids_on_page)

    # Attach the additional data directly to each user object
    for user in users_on_page:
        if hasattr(user, '_user_type') and user._user_type == 'service':
            # Use actual database ID directly
            stats = stream_stats.get(user.id, {})
            user.total_plays = stats.get('play_count', 0)
            user.total_duration = stats.get('total_duration', 0)
            user.last_known_ip = last_ips.get(user.id, 'N/A')
        else:
            # Local users don't have stream stats from the service-specific logic above
            user.total_plays = 0
            user.total_duration = 0
            user.last_known_ip = 'N/A'
            # Initialize last_streamed_at for local users - will be set below if streaming history exists
            user.last_streamed_at = None
    
    # Get library access info for each user, organized by server to prevent ID collisions
    user_library_access_by_server = {}  # user_id -> server_id -> [lib_ids]
    user_sorted_libraries = {}
    user_library_service_mapping = {}  # NEW: user_id -> {lib_name: service_type}
    user_service_types = {}  # Track which services each user belongs to
    user_server_names = {}  # Track which server names each user belongs to
    from app.models_media_services import UserMediaAccess
    
    # Process each user individually based on their type
    current_app.logger.info(f"DEBUG: Processing {len(users_on_page)} users for library access")
    
    for user in users_on_page:
        user_id = user.uuid
        user_library_access_by_server[user_id] = {}
        user_library_service_mapping[user_id] = {}  # NEW: Initialize library-to-service mapping
        user_service_types[user_id] = []
        user_server_names[user_id] = []
        
        current_app.logger.info(f"DEBUG: Processing user ID {user_id}, username: {user.username}, type: {getattr(user, '_user_type', 'unknown')}")
        
        # Use user type and actual database ID directly from user object
        if hasattr(user, '_user_type'):
            if user._user_type == 'local':
                # Local user - get all their UserMediaAccess records via user_app_access_id
                access_records = UserMediaAccess.query.filter(UserMediaAccess.user_app_access_id == user.id).all()
                current_app.logger.info(f"DEBUG: Local user {user.username} (ID: {user.id}) has {len(access_records)} access records")
            elif user._user_type == 'service':
                # Service user - get the specific UserMediaAccess record
                access_records = UserMediaAccess.query.filter(UserMediaAccess.id == user.id).all()
                current_app.logger.info(f"DEBUG: Service user {user.username} (ID: {user.id}) has {len(access_records)} access records")
            else:
                access_records = []
                current_app.logger.warning(f"DEBUG: Unknown user type: {user._user_type}")
        else:
            access_records = []
            current_app.logger.warning(f"DEBUG: User {user.username} has no _user_type attribute")
        
        # Process the access records for this user
        for access in access_records:
            current_app.logger.info(f"DEBUG: User {user.username} access to server {access.server.server_nickname} (ID: {access.server_id}) with libraries: {access.allowed_library_ids}")
            
            # Special handling for AllGas users to debug library issues
            if user.username == 'AllGas':
                current_app.logger.info(f"DEBUG: AllGas library processing - Server: {access.server.server_nickname}, Raw libraries: {access.allowed_library_ids}")
                # Check if libraries need filtering for this specific server
                if access.allowed_library_ids:
                    server_specific_libs = []
                    for lib_id in access.allowed_library_ids:
                        if isinstance(lib_id, str) and lib_id.startswith(f'[{access.server.service_type.value.upper()}]-{access.server.server_nickname}-'):
                            # Extract the actual library ID from the prefixed format
                            actual_lib_id = lib_id.split('-', 2)[-1]
                            server_specific_libs.append(actual_lib_id)
                        elif not isinstance(lib_id, str) or not lib_id.startswith('['):
                            # This is already a clean library ID
                            server_specific_libs.append(lib_id)
                    current_app.logger.info(f"DEBUG: AllGas filtered libraries for {access.server.server_nickname}: {server_specific_libs}")
                    user_library_access_by_server[user_id][access.server_id] = server_specific_libs
                else:
                    user_library_access_by_server[user_id][access.server_id] = access.allowed_library_ids
            else:
                user_library_access_by_server[user_id][access.server_id] = access.allowed_library_ids
            # Track which service types this user has access to
            if access.server.service_type not in user_service_types[user_id]:
                user_service_types[user_id].append(access.server.service_type)
            # Track which server names this user has access to
            if access.server.server_nickname not in user_server_names[user_id]:
                user_server_names[user_id].append(access.server.server_nickname)

    media_service_manager = MediaServiceManager()
    
    # Create a mapping of user_id to User object for easy lookup
    users_by_id = {user.uuid: user for user in users_pagination.items}
    
    # Get all servers for library lookups
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    # Get library data from database instead of making API calls
    libraries_by_server = get_libraries_from_database(all_servers)

    
    for user_id, servers_access in user_library_access_by_server.items():
        user_obj = users_by_id.get(user_id)
        all_lib_names = []
        
        for server_id, lib_ids in servers_access.items():
            # Get the server object to determine service type
            server = next((s for s in all_servers if s.id == server_id), None)
            service_type = server.service_type.value if server else 'unknown'
            
            # Handle special case for Jellyfin users with '*' (all libraries access)
            if lib_ids == ['*']:
                lib_names = ['All Libraries']
                # Map "All Libraries" to the service type
                user_library_service_mapping[user_id]['All Libraries'] = service_type
            else:
                # Check if this user has library_names available (for services like Kavita)
                if user_obj and hasattr(user_obj, 'library_names') and user_obj.library_names:
                    # Use library_names from the user object
                    lib_names = user_obj.library_names
                    # Map each library to the service type
                    for lib_name in lib_names:
                        user_library_service_mapping[user_id][lib_name] = service_type
                else:
                    # Look up library names from the correct server to prevent ID collisions
                    server_libraries = libraries_by_server.get(server_id, {})
                    lib_names = []
                    for lib_id in lib_ids:
                        if '_' in str(lib_id) and str(lib_id).split('_', 1)[0].isdigit():
                            # This looks like a Kavita unique ID (e.g., "0_Comics"), extract the name
                            lib_name = str(lib_id).split('_', 1)[1]
                            lib_names.append(lib_name)
                            # Map library to service type
                            user_library_service_mapping[user_id][lib_name] = service_type
                        else:
                            # Regular library ID lookup from the correct server
                            lib_name = server_libraries.get(str(lib_id), f'Unknown Lib {lib_id}')
                            lib_names.append(lib_name)
                            # Map library to service type
                            user_library_service_mapping[user_id][lib_name] = service_type
            
            all_lib_names.extend(lib_names)
        
        user_sorted_libraries[user_id] = sorted(all_lib_names, key=str.lower)

    mass_edit_form = MassUserEditForm()  

    default_inactive_days = 90
    default_exclude_sharers = True

    purge_settings_context = {
        'inactive_days': request.form.get('inactive_days', default_inactive_days, type=int),
        'exclude_sharers': request.form.get('exclude_sharers', 'true' if default_exclude_sharers else 'false').lower() == 'true'
    }
   
    # Enhanced context dictionary with additional data
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers()
    server_dropdown_options = [{"id": "all", "name": "All Servers"}]
    for server in all_servers:
        server_dropdown_options.append({
            "id": server.id,
            "name": f"{server.server_nickname} ({server.service_type.value.capitalize()})"
        })
    
    # Add user type filter options
    user_type_options = [
        {"id": "all", "name": "All Users"},
        {"id": "local", "name": "Local Users Only"},
        {"id": "service", "name": "Service Users Only"}
    ]

    # Get last played content for each user - extract actual IDs for local users only
    user_last_played = {}
    local_user_ids_on_page = []
    
    # Extract actual UserAppAccess IDs for local users (MediaStreamHistory only tracks local users)
    for user in users_pagination.items:
        if hasattr(user, '_user_type') and user._user_type == 'local':
            # Use actual database ID directly for local users
            local_user_ids_on_page.append(user.id)
    
    if local_user_ids_on_page:
        # Get the most recent stream for each local user from MediaStreamHistory table
        # Need to get UUIDs for the local users first
        local_user_uuids = []
        for user in users_on_page:
            if hasattr(user, '_user_type') and user._user_type == 'local':
                local_user_uuids.append(user.uuid)
        
        if local_user_uuids:
            from sqlalchemy import desc
            last_streams = db.session.query(MediaStreamHistory).filter(
                MediaStreamHistory.user_app_access_uuid.in_(local_user_uuids)
            ).order_by(MediaStreamHistory.user_app_access_uuid, desc(MediaStreamHistory.started_at)).all()
            
            # Group by user_app_access_uuid and take the first (most recent) for each user
            seen_users = set()
            for stream in last_streams:
                if stream.user_app_access_uuid not in seen_users:
                    # Find the user by UUID
                    user_for_stream = next((u for u in users_on_page if hasattr(u, '_user_type') and u._user_type == 'local' and u.uuid == stream.user_app_access_uuid), None)
                    if user_for_stream:
                        user_last_played[user_for_stream.uuid] = {
                        'media_title': stream.media_title,
                        'media_type': stream.media_type,
                    'grandparent_title': stream.grandparent_title,
                    'parent_title': stream.parent_title,
                    'started_at': stream.started_at,
                    'rating_key': stream.rating_key,
                    'server_id': stream.server_id if hasattr(stream, 'server_id') else None
                }
                
                    # Also set last_streamed_at on the user object for table display
                    # When sorting by last_streamed, users_on_page contains all users, not just paginated ones
                    user_for_stream.last_streamed_at = stream.started_at
                
                seen_users.add(stream.user_app_access_uuid)

    # Handle last_streamed sorting after streaming data is populated
    if 'last_streamed' in sort_by_param:
        reverse_sort = 'desc' in sort_by_param
        # Sort ALL users by last_streamed_at field (now that it's populated)
        def get_sort_key(user):
            last_streamed = getattr(user, 'last_streamed_at', None)
            if last_streamed is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            # Handle timezone-naive datetimes by assuming UTC
            if last_streamed.tzinfo is None:
                return last_streamed.replace(tzinfo=timezone.utc)
            return last_streamed
        
        all_users.sort(key=get_sort_key, reverse=reverse_sort)
        
        # Re-paginate after sorting
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        users_on_page = all_users[start_idx:end_idx]
        
        # Update the pagination object with the newly sorted and paginated users
        users_pagination.items = users_on_page

    # Add service types for standalone users to user_service_types
    if user_type_filter in ['all', 'service'] and service_users:
        for service_user in service_users:
            if hasattr(service_user, '_is_standalone') and service_user._is_standalone:
                # Add the service type for this standalone user
                user_service_types[service_user.uuid] = [service_user._access_record.server.service_type]
    
    # Check if user accounts feature is enabled
    allow_user_accounts = Setting.get_bool('ALLOW_USER_ACCOUNTS', False)
    
    template_context = {
        'title': "Managed Users",
        'users': users_pagination,
        'users_count': total_users,
        'stream_stats': stream_stats,
        'last_ips': last_ips,
        'user_library_access_by_server': user_library_access_by_server,
        'user_last_played': user_last_played,
        'user_sorted_libraries': user_sorted_libraries,
        'user_library_service_mapping': user_library_service_mapping,
        'user_service_types': user_service_types,
        'user_server_names': user_server_names,
        'current_view': view_mode,
        'mass_edit_form': mass_edit_form,
        'selected_users_count': 0,
        'current_per_page': items_per_page,
        'purge_settings': purge_settings_context,
        'admin_plex_uuids': {admin.plex_uuid for admin in admin_accounts},
        'admins_by_uuid': admins_by_uuid,
        'sort_column': sort_column,
        'sort_direction': sort_direction,
        'server_dropdown_options': server_dropdown_options,
        'user_type_options': user_type_options,
        'current_user_type': user_type_filter,
        'app_users': app_users,
        'service_users': service_users if user_type_filter in ['all', 'service'] else [],
        'allow_user_accounts': allow_user_accounts
    }
    
    if is_htmx:
        result = render_template('users/partials/user_list_content.html', **template_context)
    else:
        result = render_template('users/list.html', **template_context)
    
    # Log performance for slow requests only
    total_time = time.time() - start_time
    if total_time > 1.0:  # Only log if over 1 second
        current_app.logger.warning(f"Slow users page load: {total_time:.3f}s")
    
    return result

@bp.route('/save_view_preference', methods=['POST'])
@login_required
def save_view_preference():
    view_mode = request.form.get('view_mode')
    current_app.logger.debug(f"--- save_view_preference ---")
    current_app.logger.debug(f"Received view_mode: '{view_mode}'")
    current_app.logger.debug(f"current_user.id: {current_user.id}")
    current_app.logger.debug(f"View preference BEFORE save: '{current_user.preferred_user_list_view}'")
    
    if view_mode in ['cards', 'table']:
        try:
            user_to_update = current_user
            user_to_update.preferred_user_list_view = view_mode
            db.session.commit()
            current_app.logger.debug(f"View preference AFTER save: '{user_to_update.preferred_user_list_view}'")
            return '', 204
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error saving view preference: {e}", exc_info=True)
            return 'Error saving preference', 500
            
    current_app.logger.warning(f"Invalid view_mode '{view_mode}' received.")
    return 'Invalid view mode', 400

@bp.route('/sync', methods=['POST'])
@login_required
@setup_required
def sync_all_users():
    """
    Performs Plex user synchronization and returns an HTML response
    with htmx headers to trigger modals and toasts.
    """
    current_app.logger.info("Starting Plex user synchronization.")

    # --- Part 1: Core Synchronization Logic ---
    try:
        # Use the new unified user service instead of the old plex_service
        sync_result = UnifiedUserService.sync_all_users()
        
        if not sync_result['success']:
            current_app.logger.error(f"User sync failed: {sync_result.get('message', 'Unknown error')}")
            # Show modal with the detailed error messages from the unified service
            modal_html = render_template('users/partials/sync_results_modal.html',
                                       sync_result=sync_result)
            trigger_payload = {
                "showToastEvent": {"message": "Sync encountered errors. See details.", "category": "error"},
                "openSyncResultsModal": True,
                "refreshUserList": True
            }
            headers = {
                'HX-Retarget': '#syncResultModalContainer',
                'HX-Reswap': 'innerHTML',
                'HX-Trigger-After-Swap': json.dumps(trigger_payload)
            }
            return make_response(modal_html, 200, headers)
        else:
            # Check if there are actual changes to determine whether to show modal or just toast
            has_changes = (sync_result.get('added', 0) > 0 or 
                          sync_result.get('updated', 0) > 0 or 
                          sync_result.get('removed', 0) > 0 or 
                          sync_result.get('errors', 0) > 0)
            
            if has_changes:
                # Show modal for changes or errors
                modal_html = render_template('users/partials/sync_results_modal.html',
                                           sync_result=sync_result)
                trigger_payload = {
                    "showToastEvent": {"message": sync_result.get('message', 'Sync completed'), "category": "success"},
                    "openSyncResultsModal": True,
                    "refreshUserList": True
                }
                headers = {
                    'HX-Retarget': '#syncResultModalContainer',
                    'HX-Reswap': 'innerHTML',
                    'HX-Trigger-After-Swap': json.dumps(trigger_payload)
                }
                return make_response(modal_html, 200, headers)
            else:
                # No changes - just show toast
                trigger_payload = {
                    "showToastEvent": {"message": "Sync complete. No changes were made.", "category": "success"},
                    "refreshUserList": True
                }
                headers = {
                    'HX-Trigger': json.dumps(trigger_payload)
                }
                return make_response("", 200, headers)
            
    except Exception as e:
        current_app.logger.error(f"Critical error during user synchronization: {e}", exc_info=True)
        # Create a sync result with the actual exception details
        sync_result = {
            'success': False,
            'added': 0,
            'updated': 0,
            'errors': 1,
            'error_messages': [f"Critical synchronization error: {str(e)}"],
            'servers_synced': 0
        }
        modal_html = render_template('users/partials/sync_results_modal.html',
                                     sync_result=sync_result)
        trigger_payload = {
            "showToastEvent": {"message": "Sync failed due to critical error. See details.", "category": "error"},
            "openSyncResultsModal": True,
            "refreshUserList": True
        }
        headers = {
            'HX-Retarget': '#syncResultModalContainer',
            'HX-Reswap': 'innerHTML',
            'HX-Trigger-After-Swap': json.dumps(trigger_payload)
        }
        return make_response(modal_html, 200, headers)

@bp.route('/delete/<uuid:user_uuid>', methods=['DELETE'])
@login_required
@setup_required
@permission_required('delete_user')
def delete_user(user_uuid):
    # Get user by uuid
    from app.utils.helpers import get_user_by_uuid
    user_obj, user_type = get_user_by_uuid(str(user_uuid))
    
    if not user_obj:
        current_app.logger.error(f"User not found with uuid: {user_uuid}")
        toast = {
            "showToastEvent": {
                "message": f"User not found: {user_uuid}",
                "category": "error"
            }
        }
        response = make_response("", 404)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response
    
    actual_id = user_obj.id
    
    if user_type == "user_app_access":
        # This is a local UserAppAccess user
        user = UserAppAccess.query.get(actual_id)
    
        if not user:
            toast = {
                "showToastEvent": {
                    "message": f"Local user with ID {actual_id} not found.",
                    "category": "error"
                }
            }
            response = make_response("", 404)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        
        # This is a local UserAppAccess user
        username = user.get_display_name()
        
        try:
            UnifiedUserService.delete_user_completely(actual_id, admin_id=current_user.id)
            
            # Create a toast message payload
            toast = {
                "showToastEvent": {
                    "message": f"User '{username}' has been successfully removed.",
                    "category": "success"
                }
            }
            
            # Create an empty response and add the HX-Trigger header
            response = make_response("", 200)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response

        except Exception as e:
            current_app.logger.error(f"Route Error deleting user {username}: {e}", exc_info=True)
            log_event(EventType.ERROR_GENERAL, f"Route: Failed to delete user {username}: {e}", user_id=actual_id, admin_id=current_user.id)
            
            # Create an error toast message payload
            toast = {
                "showToastEvent": {
                    "message": f"Error deleting user '{username}': {str(e)[:100]}",
                    "category": "error"
                }
            }
            
            response = make_response("", 500)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
    
    elif user_type == "user_media_access":
        # This is a standalone service user, get the UserMediaAccess record
        from app.models_media_services import UserMediaAccess
        access = UserMediaAccess.query.filter(
            UserMediaAccess.id == actual_id
        ).first()
        
        if not access:
            toast = {
                "showToastEvent": {
                    "message": f"Service user with ID {actual_id} not found.",
                    "category": "error"
                }
            }
            response = make_response("", 404)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
            
        username = access.external_username or 'Unknown'
        
        try:
            # Delete the standalone UserMediaAccess record
            db.session.delete(access)
            db.session.commit()
            
            # Create a toast message payload
            toast = {
                "showToastEvent": {
                    "message": f"Standalone user '{username}' has been successfully removed.",
                    "category": "success"
                }
            }
            
            response = make_response("", 200)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting standalone user '{username}': {e}", exc_info=True)
            
            toast = {
                "showToastEvent": {
                    "message": f"Error deleting standalone user '{username}': {str(e)[:100]}",
                    "category": "error"
                }
            }
            
            response = make_response("", 500)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response

# Mock user class for mass edit libraries template
class MassEditMockUser:
    def __init__(self, access, server):
        self.access = access
        self.server = server
    
    def get_display_name(self):
        return f"{self.access.external_username} ({self.server.server_nickname})"

@bp.route('/mass_edit_libraries_form')
@login_required
def mass_edit_libraries_form():
    current_app.logger.info("=== USERS PAGE: mass_edit_libraries_form() called - USING DATABASE DATA ===")
    user_ids_str = request.args.get('user_ids', '')
    current_app.logger.info(f"DEBUG: Raw user_ids_str received: '{user_ids_str}'")
    if not user_ids_str:
        return '<div class="alert alert-error">No users selected.</div>'
    
    # Parse user IDs - handle both prefixed and plain numeric IDs
    service_user_ids = []
    current_app.logger.info(f"DEBUG: Processing user IDs: {user_ids_str.split(',')}")
    
    for uid_str in user_ids_str.split(','):
        uid_str = uid_str.strip()
        current_app.logger.info(f"DEBUG: Processing individual ID: '{uid_str}'")
        if not uid_str:
            continue
        
        # Try to parse as UUID first
        try:
            from app.utils.helpers import get_user_by_uuid
            user_obj, user_type = get_user_by_uuid(uid_str)
            
            if user_obj and user_type == "user_media_access":
                service_user_ids.append(user_obj.id)
                current_app.logger.info(f"DEBUG: Added service user ID {user_obj.id} from uuid {uid_str}")
            elif user_obj and user_type == "user_app_access":
                current_app.logger.warning(f"Mass edit libraries attempted on local user {uid_str} - not supported")
            else:
                current_app.logger.warning(f"No user found for uuid {uid_str}")
        except Exception as e:
            current_app.logger.info(f"DEBUG: Failed to parse as uuid: {e}")
            # No fallback - UUID-only identification
            current_app.logger.error(f"Invalid user UUID in mass edit libraries: {uid_str} - {e}")
    
    if not service_user_ids:
        return '<div class="alert alert-warning">Mass edit libraries is only available for service users. No valid service users were selected.</div>'
    
    from app.models_media_services import UserMediaAccess, MediaServer
    current_app.logger.info(f"DEBUG: Querying for service_user_ids: {service_user_ids}")
    access_records = db.session.query(UserMediaAccess, UserAppAccess, MediaServer).join(UserAppAccess, UserMediaAccess.user_app_access_id == UserAppAccess.id, isouter=True).join(MediaServer, UserMediaAccess.server_id == MediaServer.id).filter(UserMediaAccess.id.in_(service_user_ids)).all()
    current_app.logger.info(f"DEBUG: Found {len(access_records)} access records")
    
    # Debug each access record
    for i, (access, user, server) in enumerate(access_records):
        current_app.logger.info(f"DEBUG: Record {i}: access={access.id if access else None}, user={user.id if user else None}, server={server.id if server else None}")
        if access:
            current_app.logger.info(f"DEBUG: Record {i} access details: external_username={access.external_username}, server_id={access.server_id}")
        if server:
            current_app.logger.info(f"DEBUG: Record {i} server details: name={server.server_nickname}, service_type={server.service_type}")
        if user:
            current_app.logger.info(f"DEBUG: Record {i} user details: username={user.username}")
        else:
            current_app.logger.info(f"DEBUG: Record {i} user is None (standalone service user)")

    services_data = {}
    for access, user, server in access_records:
        service_type_key = server.service_type.value
        if service_type_key not in services_data:
            services_data[service_type_key] = {
                'service_name': server.service_type.name.capitalize(),
                'servers': {}
            }
        
        if server.id not in services_data[service_type_key]['servers']:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            libraries = []
            for lib in db_libraries:
                libraries.append({
                    'id': lib.external_id,
                    'external_id': lib.external_id,
                    'name': lib.name
                })
            
            services_data[service_type_key]['servers'][server.id] = {
                'server_name': server.server_nickname,
                'users': [],
                'libraries': libraries,
                'current_library_ids': set(access.allowed_library_ids or [])
            }
        
        services_data[service_type_key]['servers'][server.id]['users'].append(user)
        # Intersect library IDs for users on the same server
        current_ids = services_data[service_type_key]['servers'][server.id]['current_library_ids']
        current_ids.intersection_update(access.allowed_library_ids or [])

    # Build user objects list for the template
    users_list = []
    current_app.logger.info(f"DEBUG: Building users list from {len(access_records)} access records")
    for access, user, server in access_records:
        # Safety check - ensure access and server are not None
        if access is None:
            current_app.logger.error("DEBUG: access is None, skipping")
            continue
        if server is None:
            current_app.logger.error("DEBUG: server is None, skipping")
            continue
            
        # Just use the display name string directly
        display_name = f"{access.external_username} ({server.server_nickname})"
        
        current_app.logger.info(f"DEBUG: Adding user: {display_name}")
        current_app.logger.info(f"DEBUG: User string type: {type(display_name)}")
        
        users_list.append(display_name)
    
    current_app.logger.info(f"DEBUG: Final users_list length: {len(users_list)}")
    current_app.logger.info(f"DEBUG: First user type: {type(users_list[0]) if users_list else 'No users'}")
    
    # Create a data structure that matches what the template expects
    user_data = {
        'users': users_list
    }
    
    current_app.logger.info(f"DEBUG: user_data object: {user_data}")
    current_app.logger.info(f"DEBUG: user_data['users'] length: {len(user_data['users'])}")
    current_app.logger.info(f"DEBUG: Calling render_template with services_data and user_data")
    
    return render_template('users/partials/_mass_edit_libraries.html', 
                           services_data=services_data, 
                           user_data=user_data)

@bp.route('/mass_edit', methods=['POST'])
@login_required
@setup_required
@permission_required('mass_edit_users')
def mass_edit_users():
    current_app.logger.debug("--- MASS EDIT ROUTE START ---")
    
    # DEBUG 1: Print the raw form data received by Flask
    print(f"[SERVER DEBUG 1] Raw request.form: {request.form.to_dict()}")

    # We get user_ids manually from the request now
    user_ids_str = request.form.get('user_ids')
    toast_message = ""
    toast_category = "error"

    # Instantiate form for the other fields that DO need validation
    form = MassUserEditForm(request.form)
    
    # We still must populate the dynamic choices for the libraries field
    media_service_manager = MediaServiceManager()
    
    # Get libraries from all active servers, not just Plex
    available_libraries = {}
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    for server in all_servers:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            for lib in db_libraries:
                lib_id = lib.external_id
                lib_name = lib.name
                if lib_id:
                    # Use just the library name since server name is now shown in a separate badge
                    available_libraries[str(lib_id)] = lib_name
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {server.server_nickname}: {e}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # Manual validation for user_ids, then form validation for the rest
    if not user_ids_str:
        toast_message = "Validation Error: User Ids: This field is required."
        print("[SERVER DEBUG 2] user_ids_str is missing or empty.")
    elif form.validate():
        print(f"[SERVER DEBUG 3] Form validation PASSED. User IDs from request: '{user_ids_str}'")
        
        # Parse prefixed user IDs and extract actual database IDs
        user_ids = []
        # Convert UUIDs to actual user IDs for local users only
        from app.utils.helpers import get_user_by_uuid
        
        for uid_str in user_ids_str.split(','):
            uid_str = uid_str.strip()
            if not uid_str:
                continue
            try:
                user_obj, user_type = get_user_by_uuid(uid_str)
                if user_obj:
                    # For delete operations, support both local and service users
                    # For other mass edit operations, we only support service users (not local users)
                    if user_type == "user_media_access":
                        user_ids.append(uid_str)  # Use UUID for mass operations
                    elif user_type == "user_app_access":
                        # Check if this is a delete operation - if so, allow local users too
                        action = form.action.data if form.action.data else request.form.get('action', '')
                        if action == 'delete_users':
                            user_ids.append(uid_str)  # Allow local users for delete operations
                        else:
                            current_app.logger.warning(f"Mass edit attempted on local user {uid_str} - only service users supported for {action}")
                    else:
                        current_app.logger.warning(f"Unknown user type {user_type} for user {uid_str}")
                else:
                    current_app.logger.warning(f"User not found for UUID {uid_str}")
            except Exception as e:
                current_app.logger.error(f"Invalid user UUID in mass edit: {uid_str} - {e}")
        
        if not user_ids:
            action = form.action.data if form.action.data else request.form.get('action', '')
            if action == 'delete_users':
                toast_message = "No valid users selected for mass delete operation."
            else:
                toast_message = "No valid service users selected for mass edit operation."
            toast_category = "error"
        else:
            action = form.action.data
            try:
                if action == 'update_libraries':
                    # Parse libraries per server for service users
                    updates_by_server = {}
                    for key, value in request.form.items():
                        if key.startswith('libraries_server_'):
                            server_id = int(key.split('_')[-1])
                            if server_id not in updates_by_server:
                                updates_by_server[server_id] = []
                            updates_by_server[server_id] = request.form.getlist(key)

                    processed_count, error_count = user_service.mass_update_user_libraries_by_server(user_ids, updates_by_server, admin_id=current_user.id)
                    toast_message = f"Mass library update: {processed_count} service users updated, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
                elif action == 'extend_access':
                    days_to_extend = form.days_to_extend.data
                    if not days_to_extend or days_to_extend < 1:
                        toast_message = "Invalid number of days to extend."
                        toast_category = "error"
                    else:
                        processed_count, error_count = user_service.mass_extend_access(user_ids, days_to_extend, admin_id=current_user.id)
                        toast_message = f"Extended access for {processed_count} service users by {days_to_extend} days, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action == 'set_expiration':
                    new_expiration_date = form.new_expiration_date.data
                    if not new_expiration_date:
                        toast_message = "Expiration date is required."
                        toast_category = "error"
                    else:
                        processed_count, error_count = user_service.mass_set_expiration(user_ids, new_expiration_date, admin_id=current_user.id)
                        toast_message = f"Set expiration date for {processed_count} service users, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action == 'clear_expiration':
                    processed_count, error_count = user_service.mass_clear_expiration(user_ids, admin_id=current_user.id)
                    toast_message = f"Cleared expiration for {processed_count} service users, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
                elif action == 'delete_users':
                    if not form.confirm_delete.data:
                        toast_message = "Deletion was not confirmed. No action taken."
                        toast_category = "warning"
                    else:
                        # mass_delete_users already supports UUIDs, so pass them directly
                        processed_count, error_count = user_service.mass_delete_users(user_ids, admin_id=current_user.id)
                        toast_message = f"Mass delete: {processed_count} removed, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action.endswith('_whitelist'):
                    should_add = action.startswith('add_to')
                    whitelist_type = "Bot" if "bot" in action else "Purge"
                    if whitelist_type == "Bot":
                        count = user_service.mass_update_bot_whitelist(user_ids, should_add, current_user.id)
                    else: # Purge
                        count = user_service.mass_update_purge_whitelist(user_ids, should_add, current_user.id)
                    action_text = "added to" if should_add else "removed from"
                    toast_message = f"{count} service user(s) {action_text} the {whitelist_type} Whitelist."
                    toast_category = "success"
                else:
                    toast_message = "Invalid action."
            except Exception as e:
                toast_message = f"Server Error: {str(e)[:100]}"
                print(f"[SERVER DEBUG 5] Exception during action '{action}': {e}")
                import traceback
                traceback.print_exc()
    else:
        # Form validation failed for other fields (e.g., action)
        error_list = []
        for field, errors in form.errors.items():
            field_label = getattr(form, field).label.text
            for error in errors:
                error_list.append(f"{field_label}: {error}")
                print(f"[SERVER DEBUG 4] Validation Error for '{field_label}': {error}")
        toast_message = "Validation Error: " + "; ".join(error_list)

    # Re-rendering logic - use the same logic as the main list_users route to preserve all filters
    page = request.args.get('page', 1, type=int)
    view_mode = request.args.get('view', 'cards')
    items_per_page = session.get('users_list_per_page', int(current_app.config.get('DEFAULT_USERS_PER_PAGE', 12)))
    
    query = UserAppAccess.query
    
    # Handle separate search fields (same as main route)
    search_username = request.args.get('search_username', '').strip()
    search_email = request.args.get('search_email', '').strip()
    search_notes = request.args.get('search_notes', '').strip()
    search_term = request.args.get('search', '').strip()
    
    # Build search filters
    search_filters = []
    if search_username:
        search_filters.append(UserAppAccess.username.ilike(f"%{search_username}%"))
    if search_email:
        search_filters.append(UserAppAccess.email.ilike(f"%{search_email}%"))
    if search_notes:
        search_filters.append(UserAppAccess.notes.ilike(f"%{search_notes}%"))
    if search_term:
        search_filters.append(or_(UserAppAccess.username.ilike(f"%{search_term}%"), UserAppAccess.email.ilike(f"%{search_term}%")))
    
    # Apply search filters if any exist
    if search_filters:
        query = query.filter(or_(*search_filters))

    # Server filter (same as main route)
    server_filter_id = request.args.get('server_id', 'all')
    if server_filter_id != 'all':
        try:
            server_filter_id_int = int(server_filter_id)
            from app.models_media_services import UserMediaAccess
            query = query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
        except ValueError:
            current_app.logger.warning(f"Invalid server_id received: {server_filter_id}")
    
    filter_type = request.args.get('filter_type', '')
    # Apply filters for users (updated for new architecture)
    if filter_type == 'has_discord': query = query.filter(UserAppAccess.discord_user_id != None)
    elif filter_type == 'no_discord': query = query.filter(UserAppAccess.discord_user_id == None)
    # Note: home_user and shares_back filters removed as they don't apply to UserAppAccess
    
    # Enhanced sorting logic (same as main route)
    sort_by_param = request.args.get('sort_by', 'username_asc')
    sort_parts = sort_by_param.rsplit('_', 1)
    sort_column = sort_parts[0]
    sort_direction = 'desc' if len(sort_parts) > 1 and sort_parts[1] == 'desc' else 'asc'
    
    # Handle sorting that requires joins and aggregation
    if sort_column in ['total_plays', 'total_duration']:
        # Join with MediaStreamHistory for sorting by stream stats
        query = query.outerjoin(MediaStreamHistory, MediaStreamHistory.user_app_access_id == UserAppAccess.id).group_by(UserAppAccess.id)
        sort_field = func.count(MediaStreamHistory.id) if sort_column == 'total_plays' else func.sum(func.coalesce(MediaStreamHistory.duration_seconds, 0))
        query = query.add_columns(sort_field.label('sort_value'))
        
        if sort_direction == 'desc':
            query = query.order_by(db.desc('sort_value').nullslast(), UserAppAccess.id.asc())
        else:
            query = query.order_by(db.asc('sort_value').nullsfirst(), UserAppAccess.id.asc())
    else:
        sort_map = {
            'username': UserAppAccess.username,
            'email': UserAppAccess.email,
            'last_streamed': UserAppAccess.last_login_at,  # UserAppAccess uses last_login_at
            'created_at': UserAppAccess.created_at
        }
        
        sort_field = sort_map.get(sort_column, UserAppAccess.username)

        if sort_column in ['username', 'email']:
            if sort_direction == 'desc':
                query = query.order_by(func.lower(sort_field).desc().nullslast(), UserAppAccess.id.asc())
            else:
                query = query.order_by(func.lower(sort_field).asc().nullsfirst(), UserAppAccess.id.asc())
        else:
            if sort_direction == 'desc':
                query = query.order_by(sort_field.desc().nullslast(), UserAppAccess.id.asc())
            else:
                query = query.order_by(sort_field.asc().nullsfirst(), UserAppAccess.id.asc())
    
    # Calculate count properly for complex queries
    if sort_column in ['total_plays', 'total_duration']:
        count_query = UserAppAccess.query
        if search_filters:
            count_query = count_query.filter(or_(*search_filters))
        if server_filter_id != 'all':
            try:
                server_filter_id_int = int(server_filter_id)
                from app.models_media_services import UserMediaAccess
                count_query = count_query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
            except ValueError:
                pass
        # Note: home_user and shares_back filters removed as they don't apply to UserAppAccess
        if filter_type == 'has_discord': 
            count_query = count_query.filter(UserAppAccess.discord_user_id != None)
        elif filter_type == 'no_discord': 
            count_query = count_query.filter(UserAppAccess.discord_user_id == None)
        users_count = count_query.count()
    else:
        users_count = query.count()
    
    users_pagination = query.paginate(page=page, per_page=items_per_page, error_out=False)
    
    # Extract users from pagination results (handling complex queries that return tuples)
    users_on_page = [item[0] if isinstance(item, tuple) else item for item in users_pagination.items]
    
    # Extract user IDs from pagination results
    user_ids_on_page = [user.id for user in users_pagination.items]
    
    # Get library access info for each user, organized by server to prevent ID collisions
    user_library_access_by_server = {}  # user_id -> server_id -> [lib_ids]
    user_sorted_libraries = {}
    from app.models_media_services import UserMediaAccess
    access_records = UserMediaAccess.query.filter(UserMediaAccess.user_app_access_id.in_(user_ids_on_page)).all()
    for access in access_records:
        if access.user_app_access_id not in user_library_access_by_server:
            user_library_access_by_server[access.user_app_access_id] = {}
        user_library_access_by_server[access.user_app_access_id][access.server_id] = access.allowed_library_ids

    # Get libraries from all active servers, organized by server to prevent ID collisions
    libraries_by_server = {}  # server_id -> {lib_id: lib_name}
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    for server in all_servers:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            libraries_by_server[server.id] = {}
            for lib in db_libraries:
                lib_id = lib.external_id
                lib_name = lib.name
                if lib_id:
                    libraries_by_server[server.id][str(lib_id)] = lib_name
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {server.server_nickname}: {e}")

    # Create a mapping of user_id to User object for easy lookup
    users_by_id = {user.id: user for user in users_pagination.items}
    
    for user_id, servers_access in user_library_access_by_server.items():
        user_obj = users_by_id.get(user_id)
        all_lib_names = []
        
        for server_id, lib_ids in servers_access.items():
            # Handle special case for Jellyfin users with '*' (all libraries access)
            if lib_ids == ['*']:
                lib_names = ['All Libraries']
            else:
                # Check if this user has library_names available (for services like Kavita)
                if user_obj and hasattr(user_obj, 'library_names') and user_obj.library_names:
                    # Use library_names from the user object
                    lib_names = user_obj.library_names
                else:
                    # Look up library names from the correct server to prevent ID collisions
                    server_libraries = libraries_by_server.get(server_id, {})
                    lib_names = []
                    for lib_id in lib_ids:
                        if '_' in str(lib_id) and str(lib_id).split('_', 1)[0].isdigit():
                            # This looks like a Kavita unique ID (e.g., "0_Comics"), extract the name
                            lib_name = str(lib_id).split('_', 1)[1]
                            lib_names.append(lib_name)
                        else:
                            # Regular library ID lookup from the correct server
                            lib_name = server_libraries.get(str(lib_id), f'Unknown Lib {lib_id}')
                            lib_names.append(lib_name)
            
            all_lib_names.extend(lib_names)
        
        user_sorted_libraries[user_id] = sorted(all_lib_names, key=str.lower)

    # Get additional required context data for the template
    # Get Owner with plex_uuid for filtering (AppUsers don't have plex_uuid)
    owner = Owner.query.filter(Owner.plex_uuid.isnot(None)).first()
    admin_accounts = [owner] if owner else []
    admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts if admin.plex_uuid}
    
    # Get stream stats and other data
    user_ids_on_page = [user.id for user in users_on_page]
    stream_stats = user_service.get_bulk_user_stream_stats(user_ids_on_page)
    last_ips = user_service.get_bulk_last_known_ips(user_ids_on_page)
    
    # Attach the additional data directly to each user object
    for user in users_on_page:
        stats = stream_stats.get(user.id, {})
        user.total_plays = stats.get('play_count', 0)
        user.total_duration = stats.get('total_duration', 0)
        user.last_known_ip = last_ips.get(user.id, 'N/A')
    
    # Build user_service_types for template context
    user_service_types = {}  # Track which services each user belongs to
    user_server_names = {}   # Track server names for each user
    
    # Get all user access records to determine service types
    all_user_access = UserMediaAccess.query.filter(
        UserMediaAccess.user_app_access_id.in_([user.id for user in users_pagination.items])
    ).all()
    
    for access in all_user_access:
        if access.user_app_access_id not in user_service_types:
            user_service_types[access.user_app_access_id] = []
            user_server_names[access.user_app_access_id] = []
        
        if access.server.service_type not in user_service_types[access.user_app_access_id]:
            user_service_types[access.user_app_access_id].append(access.server.service_type)
        
        if access.server.server_nickname not in user_server_names[access.user_app_access_id]:
            user_server_names[access.user_app_access_id].append(access.server.server_nickname)

    # Get server dropdown options for template
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers()
    server_dropdown_options = [{"id": "all", "name": "All Servers"}]
    for server in all_servers:
        server_dropdown_options.append({
            "id": server.id,
            "name": f"{server.server_nickname} ({server.service_type.value.capitalize()})"
        })

    response_html = render_template('users/partials/user_list_content.html',
                                    users=users_pagination,
                                    users_count=users_count,
                                    user_library_access_by_server=user_library_access_by_server,
                                    user_sorted_libraries=user_sorted_libraries,
                                    available_libraries=available_libraries,
                                    current_view=view_mode,
                                    current_per_page=items_per_page,
                                    stream_stats=stream_stats,
                                    last_ips=last_ips,
                                    admins_by_uuid=admins_by_uuid,
                                    user_service_types=user_service_types,
                                    user_server_names=user_server_names,
                                    sort_column=sort_column,
                                    sort_direction=sort_direction,
                                    server_dropdown_options=server_dropdown_options)
    
    response = make_response(response_html)
    toast_payload = {"showToastEvent": {"message": toast_message, "category": toast_category}}
    response.headers['HX-Trigger-After-Swap'] = json.dumps(toast_payload)
    
    # Debug logging to help troubleshoot toast issues
    current_app.logger.debug(f"Mass edit complete. Toast message: '{toast_message}', category: '{toast_category}'")
    current_app.logger.debug(f"HX-Trigger-After-Swap header: {response.headers.get('HX-Trigger-After-Swap')}")
    
    return response

@bp.route('/purge_inactive', methods=['POST'])
@login_required
@setup_required
@permission_required('purge_users')
def purge_inactive_users():
    try:
        user_ids_to_purge = request.form.getlist('user_ids_to_purge')
        if not user_ids_to_purge:
            return render_template('partials/_alert_message.html', message="No users were selected to be purged.", category='info'), 400

        # Pass all criteria to the service layer for a final, safe check
        results = user_service.purge_inactive_users(
            user_ids_to_purge=[int(uid) for uid in user_ids_to_purge],
            admin_id=current_user.id,
            inactive_days_threshold=request.form.get('inactive_days', type=int),
            exclude_sharers=request.form.get('exclude_sharers') == 'true',
            exclude_whitelisted=request.form.get('exclude_whitelisted') == 'true',
            ignore_creation_date_for_never_streamed=request.form.get('ignore_creation_date') == 'true'
        )
        return render_template('partials/_alert_message.html', 
                               message=results['message'], 
                               category='success' if results['errors'] == 0 else 'warning')
    except Exception as e:
        current_app.logger.error(f"Error during purge inactive users route: {e}", exc_info=True)
        return render_template('partials/_alert_message.html', message=f"An unexpected error occurred: {e}", category='error'), 500
    
@bp.route('/purge_inactive/preview', methods=['POST'])
@login_required
@setup_required
def preview_purge_inactive_users():
    inactive_days_str = request.form.get('inactive_days')
    
    # For checkboxes, if they are not in request.form, it means they were unchecked.
    # The value is 'true' only if they are checked and sent.
    exclude_sharers_val = request.form.get('exclude_sharers') # Will be 'true' or None
    exclude_whitelisted_val = request.form.get('exclude_purge_whitelisted') # Will be 'true' or None
    ignore_creation_date_val = request.form.get('ignore_creation_date')

    current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Received form data: inactive_days='{inactive_days_str}', exclude_sharers='{exclude_sharers_val}', exclude_whitelisted='{exclude_whitelisted_val}'")
    
    try:
        inactive_days = int(inactive_days_str) if inactive_days_str and inactive_days_str.isdigit() else 90 # Default if empty or non-digit
        
        # If checkbox is checked, request.form.get() will be 'true' (matching the value="true" in HTML)
        # If unchecked, request.form.get() will be None.
        exclude_sharers = (exclude_sharers_val == 'true')
        exclude_whitelisted = (exclude_whitelisted_val == 'true')
        ignore_creation_date = (ignore_creation_date_val == 'true')

        current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Parsed criteria: inactive_days={inactive_days}, exclude_sharers={exclude_sharers}, exclude_whitelisted={exclude_whitelisted}")

        if inactive_days < 7:
            return render_template('partials/_alert_message.html', message="Inactivity period must be at least 7 days.", category='error'), 400
        
        eligible_users = user_service.get_users_eligible_for_purge(
            inactive_days_threshold=inactive_days,
            exclude_sharers=exclude_sharers,
            exclude_whitelisted=exclude_whitelisted,
            ignore_creation_date_for_never_streamed=ignore_creation_date
        )
        
        current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Found {len(eligible_users)} users eligible for purge based on criteria.")

        purge_criteria = {
            'inactive_days': inactive_days,
            'exclude_sharers': exclude_sharers,
            'exclude_whitelisted': exclude_whitelisted,
            'ignore_creation_date': ignore_creation_date
        }

        return render_template('users/partials/purge_preview_modal.html', 
                               eligible_users=eligible_users, 
                               purge_criteria=purge_criteria)
    except ValueError as ve: # For int conversion error if any
        current_app.logger.error(f"User_Routes.py - preview_purge_inactive_users(): ValueError parsing form: {ve}")
        return render_template('partials/_alert_message.html', message=f"Invalid input: {ve}", category='error'), 400
    except Exception as e:
        current_app.logger.error(f"User_Routes.py - preview_purge_inactive_users(): Error generating purge preview: {e}", exc_info=True)
        return render_template('partials/_alert_message.html', message=f"An unexpected error occurred generating purge preview: {e}", category='error'), 500
    
@bp.route('/local/<int:local_user_id>/edit')
@login_required
@permission_required('edit_user')
def get_local_user_edit_form(local_user_id):
    """Get edit form for local user"""
    local_user = UserAppAccess.query.get_or_404(local_user_id)
    
    # For now, return a simple form - this can be expanded later
    return f"""
    <div class="modal-box">
        <h3 class="font-bold text-lg">Edit Local User: {local_user.username}</h3>
        <p class="py-4">Local user editing functionality coming soon...</p>
        <div class="modal-action">
            <button class="btn" onclick="this.closest('dialog').close()">Close</button>
        </div>
    </div>
    """

@bp.route('/local/<int:local_user_id>/linked-accounts')
@login_required
@permission_required('view_users')
def get_linked_accounts(local_user_id):
    """Get linked accounts view for local user"""
    local_user = UserAppAccess.query.get_or_404(local_user_id)
    
    linked_accounts_html = ""
    # Get linked UserMediaAccess records for this local user
    from app.models_media_services import UserMediaAccess
    linked_accounts = UserMediaAccess.query.filter_by(user_app_access_id=local_user_id).all()
    
    for access in linked_accounts:
        # Get service badge info based on server type
        service_type = access.server.service_type.value if access.server else 'unknown'
        badge_info = {
            'plex': {'name': 'Plex', 'icon': 'fa-solid fa-play', 'color': 'bg-plex'},
            'jellyfin': {'name': 'Jellyfin', 'icon': 'fa-solid fa-cube', 'color': 'bg-jellyfin'},
            'emby': {'name': 'Emby', 'icon': 'fa-solid fa-play-circle', 'color': 'bg-emby'},
            'kavita': {'name': 'Kavita', 'icon': 'fa-solid fa-book', 'color': 'bg-kavita'},
            'audiobookshelf': {'name': 'AudioBookshelf', 'icon': 'fa-solid fa-headphones', 'color': 'bg-audiobookshelf'},
            'komga': {'name': 'Komga', 'icon': 'fa-solid fa-book-open', 'color': 'bg-komga'},
            'romm': {'name': 'RomM', 'icon': 'fa-solid fa-gamepad', 'color': 'bg-romm'}
        }.get(service_type, {'name': 'Unknown', 'icon': 'fa-solid fa-server', 'color': 'bg-gray-500'})
        
        # Get additional account info
        join_date = access.created_at
        join_date_str = join_date.strftime('%b %Y') if join_date else 'Unknown'
        
        # Server info is already available from access.server
        server_name = access.server.server_nickname if access.server else 'Unknown Server'
        
        linked_accounts_html += f"""
        <div class="group relative bg-base-100 rounded-xl border border-base-300/60 hover:border-base-300 transition-all duration-200 hover:shadow-lg hover:shadow-base-300/20">
            <div class="p-5">
                <div class="flex items-start justify-between gap-4">
                    <!-- Left Content -->
                    <div class="flex items-start gap-4 flex-1 min-w-0">
                        <!-- Service Avatar -->
                        <div class="relative flex-shrink-0">
                            <div class="w-12 h-12 rounded-xl {badge_info['color']} flex items-center justify-center shadow-lg">
                                <i class="{badge_info['icon']} text-white text-lg"></i>
                            </div>
                            <!-- Connection Status Indicator -->
                            <div class="absolute -bottom-1 -right-1 w-4 h-4 bg-success rounded-full border-2 border-base-100 flex items-center justify-center">
                                <i class="fa-solid fa-check text-white text-xs"></i>
                            </div>
                        </div>
                        
                        <!-- Account Details -->
                        <div class="flex-1 min-w-0">
                            <!-- Header Row -->
                            <div class="flex items-center gap-3 mb-2">
                                <h4 class="font-semibold text-base-content text-lg truncate">{access.external_username or 'Unknown User'}</h4>
                                <div class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-success/15 text-success border border-success/20">
                                    <div class="w-1.5 h-1.5 bg-success rounded-full"></div>
                                    Connected
                                </div>
                            </div>
                            
                            <!-- Service & Server Info -->
                            <div class="flex items-center gap-2 mb-3">
                                <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-sm font-medium bg-base-200/80 text-base-content/80">
                                    <i class="{badge_info['icon']} text-xs"></i>
                                    {badge_info['name']}
                                </span>
                                <span class="text-base-content/40">•</span>
                                <span class="text-sm text-base-content/70 font-medium">{server_name}</span>
                            </div>
                            
                            <!-- Metadata Row -->
                            <div class="flex items-center gap-4 text-xs text-base-content/50">
                                <div class="flex items-center gap-1.5">
                                    <i class="fa-solid fa-hashtag text-xs"></i>
                                    <span class="font-mono">{access.id}</span>
                                </div>
                                {f'''<div class="flex items-center gap-1.5">
                                    <i class="fa-solid fa-envelope text-xs"></i>
                                    <span class="truncate max-w-[120px]">{access.external_email}</span>
                                </div>''' if access.external_email else ''}
                                <div class="flex items-center gap-1.5">
                                    <i class="fa-solid fa-calendar-plus text-xs"></i>
                                    <span>Joined {join_date_str}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Action Button -->
                    <div class="flex-shrink-0">
                        <button class="btn btn-sm btn-ghost text-error/70 hover:text-error hover:bg-error/10 border border-transparent hover:border-error/20 transition-all duration-200 group/btn" 
                                onclick="unlinkServiceAccount({access.id})"
                                title="Unlink this account">
                            <i class="fa-solid fa-unlink text-sm group-hover/btn:scale-110 transition-transform duration-200"></i>
                        </button>
                    </div>
                </div>
            </div>
        </div>
        """
    
    if not linked_accounts_html:
        linked_accounts_html = f"""
        <div class="bg-base-100 rounded-xl border border-base-300/60 text-center overflow-hidden">
            <!-- Empty State Content -->
            <div class="p-8">
                <div class="w-16 h-16 rounded-2xl bg-base-200/80 flex items-center justify-center mx-auto mb-4">
                    <i class="fa-solid fa-link-slash text-base-content/40 text-2xl"></i>
                </div>
                <h4 class="font-semibold text-base-content text-lg mb-2">No Linked Accounts</h4>
                <p class="text-sm text-base-content/60 mb-6 max-w-sm mx-auto leading-relaxed">This user hasn't linked any service accounts yet.</p>
                
                <!-- Info Card -->
                <div class="bg-info/8 border border-info/15 rounded-xl p-4 max-w-md mx-auto">
                    <div class="flex items-start gap-3 text-left">
                        <div class="w-6 h-6 rounded-lg bg-info/20 flex items-center justify-center flex-shrink-0 mt-0.5">
                            <i class="fa-solid fa-info text-info text-xs"></i>
                        </div>
                        <div>
                            <p class="text-sm text-base-content/70 leading-relaxed">
                                Service accounts are automatically linked when users accept invites to access media servers.
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """
    
    return f"""
    <div class="modal-box max-w-3xl bg-base-100 border border-base-300 shadow-2xl p-0">
        <!-- Professional Header -->
        <div class="flex items-center justify-between p-6 border-b border-base-300">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center">
                    <i class="fa-solid fa-link text-primary text-lg"></i>
                </div>
                <div>
                    <h3 class="text-xl font-semibold text-base-content">Linked Accounts</h3>
                    <p class="text-sm text-base-content/60">{local_user.username} • {len(linked_accounts)} connected service accounts</p>
                </div>
            </div>
            <form method="dialog">
                <button class="btn btn-sm btn-circle btn-ghost hover:bg-base-200" type="button" 
                        onclick="this.closest('dialog').close()">
                    <i class="fa-solid fa-times"></i>
                </button>
            </form>
        </div>

        <!-- Content -->
        <div class="p-6">
            <!-- Description Card -->
            <div class="bg-base-200/50 rounded-lg p-4 mb-6 border border-base-300">
                <div class="flex items-start gap-3">
                    <div class="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 mt-0.5">
                        <i class="fa-solid fa-info text-primary text-sm"></i>
                    </div>
                    <div>
                        <h4 class="font-medium text-base-content mb-1">Account Linking Overview</h4>
                        <p class="text-sm text-base-content/70 leading-relaxed">
                            {f"This local user account is linked to {len(linked_accounts)} service accounts across your media servers." if len(linked_accounts) > 0 else "This local user account has no linked service accounts yet."}
                            Service accounts are automatically created and linked when users accept invites to access media servers.
                        </p>
                    </div>
                </div>
            </div>

            <!-- Linked Accounts List -->
            <div class="space-y-3">
                {linked_accounts_html}
            </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center justify-end gap-3 p-6 border-t border-base-300">
            <button type="button" class="btn btn-ghost" 
                    onclick="this.closest('dialog').close()">
                <i class="fa-solid fa-times mr-2"></i>
                Close
            </button>
        </div>
    </div>
    """

@bp.route('/local/<int:local_user_id>/link/<int:service_user_id>', methods=['POST'])
@login_required
@permission_required('edit_user')
def link_service_to_local(local_user_id, service_user_id):
    """Link a service account to a local user"""
    local_user = UserAppAccess.query.get_or_404(local_user_id)
    service_user = UserAppAccess.query.get_or_404(service_user_id)
    
    try:
        # Check if service user is already linked to another local user
        if service_user.local_user_id and service_user.local_user_id != local_user_id:
            return make_response("Service account is already linked to another local user", 400)
        
        # Link the accounts
        service_user.local_user_id = local_user_id
        db.session.commit()
        
        log_event(EventType.SETTING_CHANGE, 
                  f"Service account '{service_user.get_display_name()}' linked to local user '{local_user.username}'",
                  admin_id=current_user.id)
        
        return make_response("", 200)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error linking accounts: {e}")
        return make_response(f"Error linking accounts: {str(e)}", 500)

@bp.route('/service/<int:service_user_id>/unlink', methods=['POST'])
@login_required
@permission_required('edit_user')
def unlink_service_from_local(service_user_id):
    """Unlink a service account from its local user"""
    service_user = UserAppAccess.query.get_or_404(service_user_id)
    
    try:
        old_local_user = service_user.app_user
        service_user.app_user_id = None
        db.session.commit()
        
        log_event(EventType.SETTING_CHANGE, 
                  f"Service account '{service_user.get_display_name()}' unlinked from local user '{old_local_user.username if old_local_user else 'Unknown'}'",
                  admin_id=current_user.id)
        
        return make_response("", 200)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error unlinking account: {e}")
        return make_response(f"Error unlinking account: {str(e)}", 500)

@bp.route('/app/<username>/delete', methods=['DELETE'])
@login_required
@permission_required('delete_user')
def delete_app_user(username):
    """Delete an app user and all linked service accounts"""
    from app.models import UserAppAccess
    from app.services.unified_user_service import UnifiedUserService
    
    app_user = UserAppAccess.query.filter_by(username=username).first_or_404()
    username = app_user.username
    
    try:
        # Get all linked media access accounts before deletion
        linked_accounts = app_user.media_accesses
        linked_account_names = [access.external_username or 'Unknown' for access in linked_accounts]
        
        current_app.logger.info(f"Deleting app user '{username}' and {len(linked_accounts)} linked service accounts")
        
        # Delete all linked media access accounts first
        for access in linked_accounts:
            try:
                access_name = access.external_username or 'Unknown'
                current_app.logger.debug(f"Deleting linked media access: {access_name} on {access.server.server_nickname}")
                # Delete the UserMediaAccess record - this will handle server deletion if needed
                db.session.delete(access)
            except Exception as e:
                current_app.logger.error(f"Error deleting linked media access {access.external_username or 'Unknown'}: {e}")
                # Continue with other accounts even if one fails
        
        # Delete the local user
        db.session.delete(app_user)
        db.session.commit()
        
        # Log the deletion
        log_event(EventType.MUM_USER_DELETED_FROM_MUM, 
                  f"Local user '{username}' deleted along with {len(linked_account_names)} linked service accounts: {', '.join(linked_account_names) if linked_account_names else 'none'}",
                  admin_id=current_user.id)
        
        # Create a toast message payload
        toast = {
            "showToastEvent": {
                "message": f"Local user '{username}' and all linked accounts have been successfully deleted.",
                "category": "success"
            }
        }
        
        # Create an empty response and add the HX-Trigger header
        response = make_response("", 200)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting local user '{username}': {e}", exc_info=True)
        log_event(EventType.ERROR_GENERAL, f"Failed to delete app user '{username}': {e}", admin_id=current_user.id)
        
        # Create an error toast message payload
        toast = {
            "showToastEvent": {
                "message": f"Error deleting local user '{username}': {str(e)[:100]}",
                "category": "error"
            }
        }
        
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response

@bp.route('/debug_info/<uuid:user_uuid>')
@login_required
def get_user_debug_info(user_uuid):
    """Get raw user data for debugging purposes - ONLY uses stored data, NO API calls"""
    # Get user by uuid
    from app.utils.helpers import get_user_by_uuid
    user_obj, user_type = get_user_by_uuid(str(user_uuid))
    
    if not user_obj:
        current_app.logger.error(f"User not found with uuid: {user_uuid}")
        return f"<p class='text-error'>User not found: {user_uuid}</p>"
    
    actual_id = user_obj.id
    
    if user_type == "user_app_access":
        # This is a local UserAppAccess user
        user = UserAppAccess.query.get(actual_id)
    
        if not user:
            return f"<p class='text-error'>Local user with ID {actual_id} not found</p>"
        
        # This is a local UserAppAccess user - user variable already set above
        pass
    
    elif user_type == "user_media_access":
        # This is a standalone service user, get the UserMediaAccess record
        from app.models_media_services import UserMediaAccess
        access = UserMediaAccess.query.filter(
            UserMediaAccess.id == actual_id
        ).first()
        
        if not access:
            return f"<p class='text-error'>Service user with ID {actual_id} not found</p>"
        
        # Create a mock user object for the template
        class MockUser:
            def __init__(self, access, user_id):
                self.id = user_id  # Keep the prefixed ID for display
                self.username = access.external_username or 'Unknown'
                self.email = access.external_email
                self.notes = access.notes
                self.created_at = access.created_at
                self.last_login_at = access.last_activity_at
                self.media_accesses = [access]
                self.access_expires_at = access.access_expires_at
                self.discord_user_id = access.discord_user_id
                self.is_active = access.is_active
                self._is_standalone = True
                self._access_record = access
            
            def get_display_name(self):
                return self._access_record.external_username or 'Unknown'
            
            def get_avatar(self, default_url=None):
                """Return avatar URL for MockUser - service users typically don't have avatars"""
                return default_url
        
        user = MockUser(access, user_uuid)
    
    try:
        # Enhanced debugging for raw service data
        current_app.logger.info(f"=== DEBUG INFO REQUEST FOR USER {user_uuid} ===")
        current_app.logger.info(f"Username: {user.get_display_name()}")
        
        # Check for service-specific data in UserMediaAccess records
        from app.models_media_services import UserMediaAccess
        if hasattr(user, '_is_standalone') and user._is_standalone:
            # For standalone users, the access record is stored in _access_record
            user_accesses = [user._access_record]
        else:
            # For regular users, query by user_app_access_id using actual_id
            user_accesses = UserMediaAccess.query.filter_by(user_app_access_id=actual_id).all()
        
        has_service_data = False
        for access in user_accesses:
            if access.service_settings:
                has_service_data = True
                current_app.logger.info(f"Service data found for {access.server.service_type.value} server: {access.server.server_nickname}")
                current_app.logger.info(f"Service settings type: {type(access.service_settings)}")
                current_app.logger.info(f"Service settings preview: {str(access.service_settings)[:100]}...")
        
        if not has_service_data:
            current_app.logger.warning(f"No stored service data for user {user.get_display_name()} - user needs to sync")
            
        # Check which services this user belongs to
        if hasattr(user, '_is_standalone') and user._is_standalone:
            # For standalone users, use the access record we already have
            user_access = [user._access_record]
        else:
            # For regular users, query by user_app_access_id
            user_access = UserMediaAccess.query.filter_by(user_app_access_id=user.id).all()
        current_app.logger.info(f"User has access to {len(user_access)} servers:")
        for access in user_access:
            current_app.logger.info(f"  - Server: {access.server.server_nickname} (Type: {access.server.service_type.value})")
        
        # Render the template with the user data
        return render_template('users/partials/user_debug_info_modal.html', user=user)
        
    except Exception as e:
        current_app.logger.error(f"Error getting debug info for user {user_uuid}: {e}", exc_info=True)
        return f"<p class='text-error'>Error fetching user data: {str(e)}</p>"

@bp.route('/quick_edit_form/<uuid:user_uuid>')
@login_required
@permission_required('edit_user')
def get_quick_edit_form(user_uuid):
    # Get user by uuid
    from app.utils.helpers import get_user_by_uuid
    user_obj, user_type = get_user_by_uuid(str(user_uuid))
    
    if not user_obj:
        current_app.logger.error(f"User not found with uuid: {user_uuid}")
        return '<div class="alert alert-error">User not found.</div>'
    
    actual_id = user_obj.id
    
    if user_type == "user_app_access":
        # Local user - get UserAppAccess record
        user = UserAppAccess.query.get_or_404(actual_id)
    elif user_type == "user_media_access":
        # Service user - get UserMediaAccess record and create a compatible object
        from app.models_media_services import UserMediaAccess
        access = UserMediaAccess.query.get_or_404(actual_id)
        
        # Create a mock user object that's compatible with the form
        class MockUser:
            def __init__(self, access):
                self.id = actual_id  # Use actual ID for form processing
                self.username = access.external_username or 'Unknown'
                self.email = access.external_email
                self.notes = access.notes
                self.created_at = access.created_at
                self.last_login_at = access.last_activity_at
                self.access_expires_at = access.access_expires_at
                self.discord_user_id = access.discord_user_id
                self.is_discord_bot_whitelisted = False  # Service users don't have this
                self.is_purge_whitelisted = False  # Service users don't have this
                self._access_record = access
                self._is_service_user = True
            
            def get_display_name(self):
                return self.username or 'Unknown'
        
        user = MockUser(access)
    else:
        return '<div class="alert alert-error">Unknown user type.</div>'
    form = UserEditForm(obj=user) # Pre-populate form with existing data

    # Populate dynamic choices - only show libraries from servers this user has access to
    from app.models_media_services import UserMediaAccess
    
    if user_type == "user_app_access":
        # Local user - get all their UserMediaAccess records
        user_access_records = UserMediaAccess.query.filter_by(user_app_access_id=actual_id).all()
    elif user_type == "user_media_access":
        # Service user - get their specific UserMediaAccess record
        user_access_records = [user._access_record]
    
    available_libraries = {}
    current_library_ids = []
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Building available libraries for user {user.id}")
    
    for access in user_access_records:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=access.server.id).all()
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Processing server {access.server.server_nickname} (type: {access.server.service_type.value})")
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: User access record allowed_library_ids: {access.allowed_library_ids}")
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Server libraries from DB: {[{lib.external_id: lib.name} for lib in db_libraries]}")
            
            for lib in db_libraries:
                lib_id = lib.external_id
                lib_name = lib.name
                if lib_id:
                    # For Kavita, create compound IDs to match the format used in user access records
                    if access.server.service_type.value == 'kavita':
                        compound_lib_id = f"{lib_id}_{lib_name}"
                        available_libraries[compound_lib_id] = lib_name
                        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Added Kavita library: {compound_lib_id} -> {lib_name}")
                    else:
                        available_libraries[str(lib_id)] = lib_name
                        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Added non-Kavita library: {lib_id} -> {lib_name}")
            
            # Collect current library IDs from this server
            current_library_ids.extend(access.allowed_library_ids or [])
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {access.server.server_nickname}: {e}")
    
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Final available_libraries: {available_libraries}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Form choices set to: {form.libraries.choices}")
    
    # Pre-populate the fields with the user's current settings from all their servers
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Current library IDs from access records: {current_library_ids}")
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Available library keys: {list(available_libraries.keys())}")
    
    # Handle special case for Jellyfin users with '*' (all libraries access)
    if current_library_ids == ['*']:
        # If user has "All Libraries" access, check all available library checkboxes
        form.libraries.data = list(available_libraries.keys())
        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Jellyfin wildcard case - setting form data to: {form.libraries.data}")
    else:
        # For Kavita users, ensure we're using the compound IDs that match the available_libraries keys
        validated_library_ids = []
        for lib_id in current_library_ids:
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Processing library ID: {lib_id}")
            if str(lib_id) in available_libraries:
                validated_library_ids.append(str(lib_id))
                current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Direct match found for: {lib_id}")
            else:
                current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: No direct match for {lib_id}, searching for compound ID...")
                # This might be a legacy ID format, try to find a matching compound ID
                found_match = False
                for available_id in available_libraries.keys():
                    if '_' in available_id and available_id.startswith(f"{lib_id}_"):
                        validated_library_ids.append(available_id)
                        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Found compound match: {lib_id} -> {available_id}")
                        found_match = True
                        break
                
                # If no compound match, try matching by library name (for Kavita ID changes)
                if not found_match and '_' in str(lib_id):
                    stored_lib_name = str(lib_id).split('_', 1)[1]  # Extract name from stored ID
                    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Trying name match for: {stored_lib_name}")
                    for available_id, available_name in available_libraries.items():
                        if available_name == stored_lib_name:
                            validated_library_ids.append(available_id)
                            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Found name match: {lib_id} -> {available_id} (name: {stored_lib_name})")
                            found_match = True
                            break
                
                if not found_match:
                    current_app.logger.warning(f"DEBUG KAVITA QUICK EDIT: No match found for library ID: {lib_id}")
        
        form.libraries.data = list(set(validated_library_ids))  # Remove duplicates
        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Final form.libraries.data: {form.libraries.data}")

    # Get allow_downloads and allow_4k_transcode from UserMediaAccess records
    # Use the first access record's values, or default to False if no access records
    if user_access_records:
        first_access = user_access_records[0]
        form.allow_downloads.data = first_access.allow_downloads
        form.allow_4k_transcode.data = first_access.allow_4k_transcode
    else:
        form.allow_downloads.data = False
        form.allow_4k_transcode.data = True  # Default to True for 4K transcode
    form.is_discord_bot_whitelisted.data = user.is_discord_bot_whitelisted
    form.is_purge_whitelisted.data = user.is_purge_whitelisted
    
    # Updated logic for DateField - the form will automatically populate access_expires_at 
    # from the user object via obj=user, but we can explicitly set it if needed
    if user.access_expires_at:
        # Convert datetime to date for the DateField
        form.access_expires_at.data = user.access_expires_at.date()
    
    # Build user_server_names for the template
    user_server_names = {}
    user_server_names[actual_id] = []
    for access in user_access_records:
        if access.server.server_nickname not in user_server_names[actual_id]:
            user_server_names[actual_id].append(access.server.server_nickname)
    
    # We pass the _settings_tab partial, which contains the form we need.
    return render_template(
        'user/partials/settings_tab.html',
        form=form,
        user=user,
        user_server_names=user_server_names
    )