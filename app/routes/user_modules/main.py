# File: app/routes/user_modules/main.py
"""Main user listing and view preference functionality"""

from flask import render_template, request, current_app, session, make_response, redirect, url_for, flash 
from flask_login import login_required, current_user
from sqlalchemy import or_, func, desc
from app.models import UserAppAccess, Setting, EventType, Owner
from app.models_media_services import ServiceType, MediaStreamHistory, UserMediaAccess
from app.forms import MassUserEditForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.services import user_service
from app.services.unified_user_service import UnifiedUserService
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
from app.routes.user_modules.helpers import get_libraries_from_database, _get_local_user_avatar_url
from . import users_bp
import json
import time
from datetime import datetime, timezone, timedelta


@users_bp.route('/')
@login_required
@setup_required
@permission_required('view_users')
def list_users():
    """Main user listing functionality - handles both local and service users"""
    # Redirect regular users away from admin pages
    if isinstance(current_user, UserAppAccess) and not current_user.has_permission('view_users'):
        flash('You do not have permission to access the users management page.', 'danger')
        return redirect(url_for('user.index'))
    
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
    
    # Get server information early (needed for various operations)
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers()
    
    # Get both local users and service users
    current_app.logger.info(f"=== USERS LIST DEBUG: Loading users page ===")
    current_app.logger.info(f"User type filter: {user_type_filter}")
    current_app.logger.info(f"Search filters - username: '{search_username}', email: '{search_email}', notes: '{search_notes}', term: '{search_term}'")
    
    app_users = []
    service_users = []
    
    # Load actual users from database
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
        current_app.logger.info(f"Found {len(app_users)} local users")
    
    if user_type_filter in ['all', 'service']:
        # Query service users - standalone UserMediaAccess records
        current_app.logger.info("=== QUERYING SERVICE USERS ===")
        all_access_query = UserMediaAccess.query
        
        # Build search filters for service users
        search_filters = []
        if search_username:
            search_filters.append(UserMediaAccess.external_username.ilike(f"%{search_username}%"))
        if search_email:
            search_filters.append(UserMediaAccess.external_email.ilike(f"%{search_email}%"))
        if search_notes:
            search_filters.append(UserMediaAccess.notes.ilike(f"%{search_notes}%"))
        if search_term:
            search_filters.append(or_(
                UserMediaAccess.external_username.ilike(f"%{search_term}%"), 
                UserMediaAccess.external_email.ilike(f"%{search_term}%")
            ))
        
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

        all_access_records = all_access_query.all()
        
        # Convert UserMediaAccess records to user-like format for display
        for access in all_access_records:
            # Create a mock user object with necessary attributes
            class MockUser:
                def __init__(self, access):
                    self.uuid = access.uuid
                    self.id = access.id
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
                    self.is_home_user = access.is_home_user
                    self.shares_back = access.shares_back
                    self.is_purge_whitelisted = access.is_purge_whitelisted
                    self.plex_join_date = access.service_join_date or access.created_at
                    self.avatar_url = None
                    self.last_streamed_at = None
                
                def get_display_name(self):
                    return self._access_record.external_username or 'Unknown'
                
                def get_avatar(self, default_url=None):
                    return default_url
            
            mock_user = MockUser(access)
            mock_user._user_type = 'service'
            service_users.append(mock_user)
        
        current_app.logger.info(f"Found {len(service_users)} service users")
    
    # Combine and process users
    all_users = []
    
    # Add local users with a type indicator and process their avatars
    current_app.logger.info(f"DEBUG: Found {len(app_users)} local users")
    for app_user in app_users:
        app_user._user_type = 'local'
        # Process avatar URL for local users using their linked media access accounts
        app_user.avatar_url = _get_local_user_avatar_url(app_user)
        # UUID is already available on the user object
        
        # Set plex_join_date for local users - use the earliest service join date or created_at
        earliest_join_date = app_user.created_at
        for media_access in app_user.media_accesses:
            if media_access.service_join_date and (not earliest_join_date or media_access.service_join_date < earliest_join_date):
                earliest_join_date = media_access.service_join_date
        app_user.plex_join_date = earliest_join_date
        
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
    
    # Create mock pagination object
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
            last = self.pages
            for num in range(1, last + 1):
                if num <= left_edge or \
                   (self.page - left_current - 1 < num < self.page + right_current) or \
                   num > last - right_edge:
                    yield num
                elif num == left_edge + 1 or num == last - right_edge:
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
    
    # Get user UUIDs for additional data - both local and service users
    all_user_uuids = []
    
    for user in users_on_page:
        if hasattr(user, '_user_type'):
            # Use UUIDs for both local and service users
            all_user_uuids.append(user.uuid)

    # Fetch additional data for all users (using UUIDs)
    stream_stats = {}
    last_ips = {}
    if all_user_uuids:
        stream_stats = user_service.get_bulk_user_stream_stats(all_user_uuids)
        last_ips = user_service.get_bulk_last_known_ips(all_user_uuids)

    # Get last known IPs from streaming history for all users
    all_user_uuids_for_ips = []
    for user in users_on_page:
        if hasattr(user, '_user_type'):
            if user._user_type == 'local':
                all_user_uuids_for_ips.append(user.uuid)
            elif user._user_type == 'service':
                all_user_uuids_for_ips.append(user.uuid)
    
    # Get most recent IP addresses from MediaStreamHistory for all users
    last_known_ips_from_streams = {}
    if all_user_uuids_for_ips:
        from sqlalchemy import desc
        # Query for the most recent stream for each user to get their last known IP
        recent_streams = db.session.query(MediaStreamHistory).filter(
            or_(
                MediaStreamHistory.user_app_access_uuid.in_(all_user_uuids_for_ips),
                MediaStreamHistory.user_media_access_uuid.in_(all_user_uuids_for_ips)
            )
        ).order_by(MediaStreamHistory.started_at.desc()).all()
        
        # Group by user and take the most recent IP for each
        seen_users = set()
        for stream in recent_streams:
            user_uuid = stream.user_app_access_uuid or stream.user_media_access_uuid
            if user_uuid and user_uuid not in seen_users and stream.ip_address:
                last_known_ips_from_streams[user_uuid] = stream.ip_address
                seen_users.add(user_uuid)

    # Attach the additional data directly to each user object
    for user in users_on_page:
        if hasattr(user, '_user_type'):
            # Use UUID to get stats for both local and service users
            stats = stream_stats.get(user.uuid, {})
            user.total_plays = stats.get('play_count', 0)
            user.total_duration = stats.get('total_duration', 0)
            # Use IP from streaming history first, then fall back to bulk IP lookup
            user.last_known_ip = last_known_ips_from_streams.get(user.uuid) or last_ips.get(user.uuid, 'N/A')
            # Initialize last_streamed_at - will be set below if streaming history exists
            if not hasattr(user, 'last_streamed_at'):
                user.last_streamed_at = None
    
    # Get library access info for each user, organized by server
    user_library_access_by_server = {}  # user_id -> server_id -> [lib_ids]
    user_sorted_libraries = {}
    user_library_service_mapping = {}  # user_id -> {lib_name: service_type}
    user_service_types = {}  # Track which services each user belongs to
    user_server_names = {}  # Track which server names each user belongs to
    
    # Process each user for library access and service types
    for user in users_on_page:
        user_id = user.uuid
        user_library_access_by_server[user_id] = {}
        user_library_service_mapping[user_id] = {}
        user_service_types[user_id] = []
        user_server_names[user_id] = []
        
        # Get access records based on user type
        if hasattr(user, '_user_type'):
            if user._user_type == 'local':
                # Local user - get all their UserMediaAccess records
                access_records = UserMediaAccess.query.filter(UserMediaAccess.user_app_access_id == user.id).all()
            elif user._user_type == 'service':
                # Service user - get their specific UserMediaAccess record
                access_records = [user._access_record]
            else:
                access_records = []
        else:
            access_records = []
        
        # Process the access records for this user
        for access in access_records:
            # Track library access by server
            user_library_access_by_server[user_id][access.server_id] = access.allowed_library_ids or []
            
            # Track which service types this user has access to
            if access.server.service_type not in user_service_types[user_id]:
                user_service_types[user_id].append(access.server.service_type)
            
            # Track which server names this user has access to
            if access.server.server_nickname not in user_server_names[user_id]:
                user_server_names[user_id].append(access.server.server_nickname)
    
    # Get library data from database for library name mapping
    libraries_by_server = get_libraries_from_database(all_servers)
    
    # Create user library mappings
    for user_id, servers_access in user_library_access_by_server.items():
        all_lib_names = []
        
        for server_id, lib_ids in servers_access.items():
            # Get the server object to determine service type
            server = next((s for s in all_servers if s.id == server_id), None)
            service_type = server.service_type.value if server else 'unknown'
            
            # Handle special case for Jellyfin users with '*' (all libraries access)
            if lib_ids == ['*']:
                lib_names = ['All Libraries']
                user_library_service_mapping[user_id]['All Libraries'] = service_type
            else:
                # Look up library names from the correct server
                server_libraries = libraries_by_server.get(server_id, {})
                lib_names = []
                for lib_id in lib_ids:
                    if '_' in str(lib_id) and str(lib_id).split('_', 1)[0].isdigit():
                        # This looks like a Kavita unique ID (e.g., "0_Comics"), extract the name
                        lib_name = str(lib_id).split('_', 1)[1]
                        lib_names.append(lib_name)
                        user_library_service_mapping[user_id][lib_name] = service_type
                    else:
                        # Regular library ID lookup from the correct server
                        lib_name = server_libraries.get(str(lib_id), f'Unknown Lib {lib_id}')
                        lib_names.append(lib_name)
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

    # Get last played content for each user - both local and service users
    user_last_played = {}
    all_user_uuids_for_last_played = []
    
    # Extract UUIDs for all users (both local and service)
    for user in users_pagination.items:
        if hasattr(user, '_user_type'):
            all_user_uuids_for_last_played.append(user.uuid)
    
    if all_user_uuids_for_last_played:
        from sqlalchemy import desc
        # Get the most recent stream for each user from MediaStreamHistory table
        last_streams = db.session.query(MediaStreamHistory).filter(
            or_(
                MediaStreamHistory.user_app_access_uuid.in_(all_user_uuids_for_last_played),
                MediaStreamHistory.user_media_access_uuid.in_(all_user_uuids_for_last_played)
            )
        ).order_by(MediaStreamHistory.started_at.desc()).all()
        
        # Group by user UUID and take the first (most recent) for each user
        seen_users = set()
        for stream in last_streams:
            user_uuid = stream.user_app_access_uuid or stream.user_media_access_uuid
            if user_uuid and user_uuid not in seen_users:
                # Find the user by UUID
                user_for_stream = next((u for u in users_on_page if hasattr(u, '_user_type') and u.uuid == user_uuid), None)
                if user_for_stream:
                    # Build display title based on media type
                    display_title = stream.media_title or 'Unknown Title'
                    
                    # For TV shows, combine show name and episode title
                    if stream.media_type == 'episode' and stream.grandparent_title:
                        if stream.parent_title:  # Season info
                            display_title = f"{stream.grandparent_title} - {stream.media_title}"
                        else:
                            display_title = f"{stream.grandparent_title} - {stream.media_title}"
                    elif stream.media_type == 'track' and stream.grandparent_title:  # Music
                        display_title = f"{stream.grandparent_title} - {stream.media_title}"
                    
                    user_last_played[user_for_stream.uuid] = {
                        'media_title': display_title,
                        'original_media_title': stream.media_title,
                        'media_type': stream.media_type,
                        'grandparent_title': stream.grandparent_title,
                        'parent_title': stream.parent_title,
                        'started_at': stream.started_at,
                        'rating_key': stream.rating_key,
                        'server_id': stream.server_id if hasattr(stream, 'server_id') else None
                    }
                
                    # Also set last_streamed_at on the user object for table display
                    user_for_stream.last_streamed_at = stream.started_at
                
                seen_users.add(user_uuid)

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
    
    # Template context with complete user data
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


@users_bp.route('/save_view_preference', methods=['POST'])
@login_required
def save_view_preference():
    """Save user's preferred view mode (cards/table)"""
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