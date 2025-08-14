# File: app/routes/users.py
from flask import Blueprint, render_template, request, current_app, session, make_response, redirect, url_for 
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.models import User, Setting, EventType, AdminAccount, StreamHistory
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

@bp.route('/')
@login_required
@setup_required
@permission_required('view_users')
def list_users():
    import time
    start_time = time.time()
    current_app.logger.debug(f"Loading users page for user {current_user.id}")
    
    is_htmx = request.headers.get('HX-Request')

    # If it's a direct browser load and 'view' is missing from the URL
    if 'view' not in request.args and not is_htmx:
        # Get the preferred view, default to 'cards' if not set
        preferred_view = current_user.preferred_user_list_view or 'cards'
        
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

    query = User.query
    
    # Handle separate search fields
    search_username = request.args.get('search_username', '').strip()
    search_email = request.args.get('search_email', '').strip()
    search_notes = request.args.get('search_notes', '').strip()
    
    # Legacy search field for backward compatibility with the main search bar
    search_term = request.args.get('search', '').strip()
    
    # Build search filters
    search_filters = []
    if search_username:
        search_filters.append(User.primary_username.ilike(f"%{search_username}%"))
    if search_email:
        search_filters.append(User.plex_email.ilike(f"%{search_email}%"))
    if search_notes:
        search_filters.append(User.notes.ilike(f"%{search_notes}%"))
    if search_term:
        # Legacy search - search both username and email
        search_filters.append(or_(User.primary_username.ilike(f"%{search_term}%"), User.plex_email.ilike(f"%{search_term}%")))
    
    # Apply search filters if any exist
    if search_filters:
        query = query.filter(or_(*search_filters))

    server_filter_id = request.args.get('server_id', 'all')
    if server_filter_id != 'all':
        # Ensure server_filter_id is an integer for the join
        try:
            server_filter_id_int = int(server_filter_id)
            from app.models_media_services import UserMediaAccess
            query = query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
        except ValueError:
            current_app.logger.warning(f"Invalid server_id received: {server_filter_id}")
            # Optionally, handle this error more gracefully, e.g., by showing an alert

    filter_type = request.args.get('filter_type', '')
    if filter_type == 'home_user': query = query.filter(User.is_home_user == True)
    elif filter_type == 'shares_back': query = query.filter(User.shares_back == True)
    elif filter_type == 'has_discord': query = query.filter(User.discord_user_id != None)
    elif filter_type == 'no_discord': query = query.filter(User.discord_user_id == None)
   
    # --- START OF ENHANCED SORTING LOGIC ---
    sort_by_param = request.args.get('sort_by', 'username_asc')
    sort_parts = sort_by_param.rsplit('_', 1)
    sort_column = sort_parts[0]
    sort_direction = 'desc' if len(sort_parts) > 1 and sort_parts[1] == 'desc' else 'asc'
    

    # Handle sorting that requires joins and aggregation
    if sort_column in ['total_plays', 'total_duration']:
        # For stats sorting, we must join and group
        query = query.outerjoin(User.stream_history).group_by(User.id)
        
        # Select the User object and the aggregated columns
        sort_field = func.count(StreamHistory.id) if sort_column == 'total_plays' else func.sum(func.coalesce(StreamHistory.duration_seconds, 0))
        query = query.add_columns(sort_field.label('sort_value'))
        
        if sort_direction == 'desc':
            query = query.order_by(db.desc('sort_value').nullslast(), User.id.asc())
        else:
            query = query.order_by(db.asc('sort_value').nullsfirst(), User.id.asc())
    else:
        # Standard sorting on direct User model fields
        sort_map = {
            'username': User.primary_username,
            'email': User.plex_email,
            'last_streamed': User.last_streamed_at,
            'plex_join_date': User.plex_join_date,
            'created_at': User.created_at
        }
        
        # Default to sorting by username if the column is invalid
        sort_field = sort_map.get(sort_column, User.primary_username)

        # For string fields, use case-insensitive sorting to ensure consistent pagination
        if sort_column in ['username', 'email']:
            if sort_direction == 'desc':
                # Use func.lower() for case-insensitive sorting, with nullslast() and secondary sort by ID
                query = query.order_by(func.lower(sort_field).desc().nullslast(), User.id.asc())
            else:
                # Use func.lower() for case-insensitive sorting, with nullsfirst() and secondary sort by ID
                query = query.order_by(func.lower(sort_field).asc().nullsfirst(), User.id.asc())
        else:
            # For non-string fields (dates, etc.), use regular sorting
            if sort_direction == 'desc':
                # Use .nullslast() to ensure users with no data appear at the end
                # Add secondary sort by ID for consistent ordering
                query = query.order_by(sort_field.desc().nullslast(), User.id.asc())
            else:
                # Use .nullsfirst() to ensure users with no data appear at the beginning  
                # Add secondary sort by ID for consistent ordering
                query = query.order_by(sort_field.asc().nullsfirst(), User.id.asc())
    # --- END OF ENHANCED SORTING LOGIC ---

    admin_accounts = AdminAccount.query.filter(AdminAccount.plex_uuid.isnot(None)).all()
    admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts}
    
    # Calculate count before pagination to ensure consistency
    # For complex queries with joins/aggregations, we need to count differently
    if sort_column in ['total_plays', 'total_duration']:
        # For aggregated queries, count the distinct users
        count_query = User.query
        # Apply the same filters as the main query
        if search_filters:
            count_query = count_query.filter(or_(*search_filters))
        if server_filter_id != 'all':
            try:
                server_filter_id_int = int(server_filter_id)
                from app.models_media_services import UserMediaAccess
                count_query = count_query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
            except ValueError:
                pass
        if filter_type == 'home_user': 
            count_query = count_query.filter(User.is_home_user == True)
        elif filter_type == 'shares_back': 
            count_query = count_query.filter(User.shares_back == True)
        elif filter_type == 'has_discord': 
            count_query = count_query.filter(User.discord_user_id != None)
        elif filter_type == 'no_discord': 
            count_query = count_query.filter(User.discord_user_id == None)
        
        users_count = count_query.count()
    else:
        # For simple queries, use the existing query
        users_count = query.count()
    
    users_pagination = query.paginate(page=page, per_page=items_per_page, error_out=False)

    # Extract users from pagination results (handling complex queries that return tuples)
    users_on_page = [item[0] if isinstance(item, tuple) else item for item in users_pagination.items]
    user_ids_on_page = [user.id for user in users_on_page]

    # Fetch additional data for the current page
    stream_stats = user_service.get_bulk_user_stream_stats(user_ids_on_page)
    last_ips = user_service.get_bulk_last_known_ips(user_ids_on_page)

    # Attach the additional data directly to each user object
    for user in users_on_page:
        stats = stream_stats.get(user.id, {})
        user.total_plays = stats.get('play_count', 0)
        user.total_duration = stats.get('total_duration', 0)
        user.last_known_ip = last_ips.get(user.id, 'N/A')
    
    # Get library access info for each user, organized by server to prevent ID collisions
    user_library_access_by_server = {}  # user_id -> server_id -> [lib_ids]
    user_sorted_libraries = {}
    user_service_types = {}  # Track which services each user belongs to
    user_server_names = {}  # Track which server names each user belongs to
    from app.models_media_services import UserMediaAccess
    access_records = UserMediaAccess.query.filter(UserMediaAccess.user_id.in_(user_ids_on_page)).all()
    for access in access_records:
        if access.user_id not in user_library_access_by_server:
            user_library_access_by_server[access.user_id] = {}
            user_service_types[access.user_id] = []
            user_server_names[access.user_id] = []
        user_library_access_by_server[access.user_id][access.server_id] = access.allowed_library_ids
        # Track which service types this user has access to
        if access.server.service_type not in user_service_types[access.user_id]:
            user_service_types[access.user_id].append(access.server.service_type)
        # Track which server names this user has access to
        if access.server.name not in user_server_names[access.user_id]:
            user_server_names[access.user_id].append(access.server.name)

    media_service_manager = MediaServiceManager()
    
    # Create a mapping of user_id to User object for easy lookup
    users_by_id = {user.id: user for user in users_pagination.items}
    
    # Get all servers for library lookups
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    # Get library data from database instead of making API calls
    libraries_by_server = get_libraries_from_database(all_servers)

    
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
            "name": f"{server.name} ({server.service_type.value.capitalize()})"
        })

    # Get last played content for each user
    user_last_played = {}
    user_ids_on_page = [user.id for user in users_pagination.items]
    if user_ids_on_page:
        # Get the most recent stream for each user from StreamHistory table
        from sqlalchemy import desc
        last_streams = db.session.query(StreamHistory).filter(
            StreamHistory.user_id.in_(user_ids_on_page)
        ).order_by(StreamHistory.user_id, desc(StreamHistory.started_at)).all()
        
        # Group by user_id and take the first (most recent) for each user
        seen_users = set()
        for stream in last_streams:
            if stream.user_id not in seen_users:
                user_last_played[stream.user_id] = {
                    'media_title': stream.media_title,
                    'media_type': stream.media_type,
                    'grandparent_title': stream.grandparent_title,
                    'parent_title': stream.parent_title,
                    'started_at': stream.started_at,
                    'rating_key': stream.rating_key,
                    'server_id': None  # StreamHistory doesn't have server_id
                }
                seen_users.add(stream.user_id)

    template_context = {
        'title': "Managed Users",
        'users': users_pagination,
        'users_count': users_count,
        'stream_stats': stream_stats,
        'last_ips': last_ips,
        'user_library_access_by_server': user_library_access_by_server,
        'user_last_played': user_last_played,
        'user_sorted_libraries': user_sorted_libraries,
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
        'server_dropdown_options': server_dropdown_options
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
            user_to_update = AdminAccount.query.get(current_user.id)
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

@bp.route('/delete/<int:user_id>', methods=['DELETE'])
@login_required
@setup_required
@permission_required('delete_user')
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Use the universal display name method instead of legacy plex_username
    username = user.get_display_name()
    
    try:
        UnifiedUserService.delete_user_completely(user_id, admin_id=current_user.id)
        
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
        log_event(EventType.ERROR_GENERAL, f"Route: Failed to delete user {username}: {e}", user_id=user_id, admin_id=current_user.id)
        
        # Create an error toast message payload
        toast = {
            "showToastEvent": {
                "message": f"Error deleting user '{username}': {str(e)[:100]}",
                "category": "error"
            }
        }
        
        # Respond with an error status and the trigger header
        # Note: HTMX will NOT swap the target on a 500 error unless told to.
        # But it WILL process the trigger header, showing the toast.
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response

@bp.route('/mass_edit_libraries_form')
@login_required
def mass_edit_libraries_form():
    current_app.logger.info("=== USERS PAGE: mass_edit_libraries_form() called - USING DATABASE DATA ===")
    user_ids_str = request.args.get('user_ids', '')
    if not user_ids_str:
        return '<div class="alert alert-error">No users selected.</div>'
    
    user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.isdigit()]
    
    from app.models_media_services import UserMediaAccess, MediaServer
    access_records = db.session.query(UserMediaAccess, User, MediaServer).join(User, UserMediaAccess.user_id == User.id).join(MediaServer, UserMediaAccess.server_id == MediaServer.id).filter(UserMediaAccess.user_id.in_(user_ids)).all()

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
                'server_name': server.name,
                'users': [],
                'libraries': libraries,
                'current_library_ids': set(access.allowed_library_ids or [])
            }
        
        services_data[service_type_key]['servers'][server.id]['users'].append(user)
        # Intersect library IDs for users on the same server
        current_ids = services_data[service_type_key]['servers'][server.id]['current_library_ids']
        current_ids.intersection_update(access.allowed_library_ids or [])

    return render_template('users/partials/_mass_edit_libraries.html', services_data=services_data)

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
            current_app.logger.error(f"Error getting libraries from {server.name}: {e}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # Manual validation for user_ids, then form validation for the rest
    if not user_ids_str:
        toast_message = "Validation Error: User Ids: This field is required."
        print("[SERVER DEBUG 2] user_ids_str is missing or empty.")
    elif form.validate():
        print(f"[SERVER DEBUG 3] Form validation PASSED. User IDs from request: '{user_ids_str}'")
        user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.isdigit()]
        action = form.action.data
        try:
            if action == 'update_libraries':
                # The new logic will parse libraries per server
                updates_by_server = {}
                for key, value in request.form.items():
                    if key.startswith('libraries_server_'):
                        server_id = int(key.split('_')[-1])
                        if server_id not in updates_by_server:
                            updates_by_server[server_id] = []
                        updates_by_server[server_id] = request.form.getlist(key)

                processed_count, error_count = user_service.mass_update_user_libraries_by_server(user_ids, updates_by_server, admin_id=current_user.id)
                toast_message = f"Mass library update: {processed_count} users updated, {error_count} errors."
                toast_category = "success" if error_count == 0 else "warning"
            elif action == 'extend_access':
                days_to_extend = form.days_to_extend.data
                if not days_to_extend or days_to_extend < 1:
                    toast_message = "Invalid number of days to extend."
                    toast_category = "error"
                else:
                    processed_count, error_count = user_service.mass_extend_access(user_ids, days_to_extend, admin_id=current_user.id)
                    toast_message = f"Extended access for {processed_count} users by {days_to_extend} days, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
            elif action == 'set_expiration':
                new_expiration_date = form.new_expiration_date.data
                if not new_expiration_date:
                    toast_message = "Expiration date is required."
                    toast_category = "error"
                else:
                    processed_count, error_count = user_service.mass_set_expiration(user_ids, new_expiration_date, admin_id=current_user.id)
                    toast_message = f"Set expiration date for {processed_count} users, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
            elif action == 'clear_expiration':
                processed_count, error_count = user_service.mass_clear_expiration(user_ids, admin_id=current_user.id)
                toast_message = f"Cleared expiration for {processed_count} users, {error_count} errors."
                toast_category = "success" if error_count == 0 else "warning"
            elif action == 'delete_users':
                if not form.confirm_delete.data:
                    toast_message = "Deletion was not confirmed. No action taken."
                    toast_category = "warning"
                else:
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
                toast_message = f"{count} user(s) {action_text} the {whitelist_type} Whitelist."
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
    
    query = User.query
    
    # Handle separate search fields (same as main route)
    search_username = request.args.get('search_username', '').strip()
    search_email = request.args.get('search_email', '').strip()
    search_notes = request.args.get('search_notes', '').strip()
    search_term = request.args.get('search', '').strip()
    
    # Build search filters
    search_filters = []
    if search_username:
        search_filters.append(User.primary_username.ilike(f"%{search_username}%"))
    if search_email:
        search_filters.append(User.plex_email.ilike(f"%{search_email}%"))
    if search_notes:
        search_filters.append(User.notes.ilike(f"%{search_notes}%"))
    if search_term:
        search_filters.append(or_(User.primary_username.ilike(f"%{search_term}%"), User.plex_email.ilike(f"%{search_term}%")))
    
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
    if filter_type == 'home_user': query = query.filter(User.is_home_user == True)
    elif filter_type == 'shares_back': query = query.filter(User.shares_back == True)
    elif filter_type == 'has_discord': query = query.filter(User.discord_user_id != None)
    elif filter_type == 'no_discord': query = query.filter(User.discord_user_id == None)
    
    # Enhanced sorting logic (same as main route)
    sort_by_param = request.args.get('sort_by', 'username_asc')
    sort_parts = sort_by_param.rsplit('_', 1)
    sort_column = sort_parts[0]
    sort_direction = 'desc' if len(sort_parts) > 1 and sort_parts[1] == 'desc' else 'asc'
    
    # Handle sorting that requires joins and aggregation
    if sort_column in ['total_plays', 'total_duration']:
        query = query.outerjoin(User.stream_history).group_by(User.id)
        sort_field = func.count(StreamHistory.id) if sort_column == 'total_plays' else func.sum(func.coalesce(StreamHistory.duration_seconds, 0))
        query = query.add_columns(sort_field.label('sort_value'))
        
        if sort_direction == 'desc':
            query = query.order_by(db.desc('sort_value').nullslast(), User.id.asc())
        else:
            query = query.order_by(db.asc('sort_value').nullsfirst(), User.id.asc())
    else:
        sort_map = {
            'username': User.primary_username,
            'email': User.plex_email,
            'last_streamed': User.last_streamed_at,
            'plex_join_date': User.plex_join_date,
            'created_at': User.created_at
        }
        
        sort_field = sort_map.get(sort_column, User.primary_username)

        if sort_column in ['username', 'email']:
            if sort_direction == 'desc':
                query = query.order_by(func.lower(sort_field).desc().nullslast(), User.id.asc())
            else:
                query = query.order_by(func.lower(sort_field).asc().nullsfirst(), User.id.asc())
        else:
            if sort_direction == 'desc':
                query = query.order_by(sort_field.desc().nullslast(), User.id.asc())
            else:
                query = query.order_by(sort_field.asc().nullsfirst(), User.id.asc())
    
    # Calculate count properly for complex queries
    if sort_column in ['total_plays', 'total_duration']:
        count_query = User.query
        if search_filters:
            count_query = count_query.filter(or_(*search_filters))
        if server_filter_id != 'all':
            try:
                server_filter_id_int = int(server_filter_id)
                from app.models_media_services import UserMediaAccess
                count_query = count_query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
            except ValueError:
                pass
        if filter_type == 'home_user': 
            count_query = count_query.filter(User.is_home_user == True)
        elif filter_type == 'shares_back': 
            count_query = count_query.filter(User.shares_back == True)
        elif filter_type == 'has_discord': 
            count_query = count_query.filter(User.discord_user_id != None)
        elif filter_type == 'no_discord': 
            count_query = count_query.filter(User.discord_user_id == None)
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
    access_records = UserMediaAccess.query.filter(UserMediaAccess.user_id.in_(user_ids_on_page)).all()
    for access in access_records:
        if access.user_id not in user_library_access_by_server:
            user_library_access_by_server[access.user_id] = {}
        user_library_access_by_server[access.user_id][access.server_id] = access.allowed_library_ids

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
            current_app.logger.error(f"Error getting libraries from {server.name}: {e}")

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
    admin_accounts = AdminAccount.query.filter(AdminAccount.plex_uuid.isnot(None)).all()
    admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts}
    
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
        UserMediaAccess.user_id.in_([user.id for user in users_pagination.items])
    ).all()
    
    for access in all_user_access:
        if access.user_id not in user_service_types:
            user_service_types[access.user_id] = []
            user_server_names[access.user_id] = []
        
        if access.server.service_type not in user_service_types[access.user_id]:
            user_service_types[access.user_id].append(access.server.service_type)
        
        if access.server.name not in user_server_names[access.user_id]:
            user_server_names[access.user_id].append(access.server.name)

    # Get server dropdown options for template
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers()
    server_dropdown_options = [{"id": "all", "name": "All Servers"}]
    for server in all_servers:
        server_dropdown_options.append({
            "id": server.id,
            "name": f"{server.name} ({server.service_type.value.capitalize()})"
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
    
@bp.route('/debug_info/<int:user_id>')
@login_required
def get_user_debug_info(user_id):
    """Get raw user data for debugging purposes - ONLY uses stored data, NO API calls"""
    user = User.query.get_or_404(user_id)
    
    try:
        # Enhanced debugging for raw service data
        current_app.logger.info(f"=== DEBUG INFO REQUEST FOR USER {user_id} ===")
        current_app.logger.info(f"Username: {user.get_display_name()}")
        current_app.logger.info(f"Raw service data exists: {user.raw_service_data is not None}")
        current_app.logger.info(f"Raw service data type: {type(user.raw_service_data)}")
        if user.raw_service_data:
            current_app.logger.info(f"Raw service data length: {len(str(user.raw_service_data))}")
            current_app.logger.info(f"Raw service data preview: {str(user.raw_service_data)[:100]}...")
        else:
            current_app.logger.warning(f"No stored raw data for user {user.get_display_name()} - user needs to sync")
            
        # Check which services this user belongs to
        from app.models_media_services import UserMediaAccess
        user_access = UserMediaAccess.query.filter_by(user_id=user.id).all()
        current_app.logger.info(f"User has access to {len(user_access)} servers:")
        for access in user_access:
            current_app.logger.info(f"  - Server: {access.server.name} (Type: {access.server.service_type.value})")
        
        # Render the template with the user data
        return render_template('users/partials/user_debug_info_modal.html', user=user)
        
    except Exception as e:
        current_app.logger.error(f"Error getting debug info for user {user_id}: {e}", exc_info=True)
        return f"<p class='text-error'>Error fetching user data: {str(e)}</p>"

@bp.route('/quick_edit_form/<int:user_id>')
@login_required
@permission_required('edit_user')
def get_quick_edit_form(user_id):
    user = User.query.get_or_404(user_id)
    form = UserEditForm(obj=user) # Pre-populate form with existing data

    # Populate dynamic choices - only show libraries from servers this user has access to
    from app.models_media_services import UserMediaAccess
    user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
    
    available_libraries = {}
    current_library_ids = []
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Building available libraries for user {user.id}")
    
    for access in user_access_records:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=access.server.id).all()
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Processing server {access.server.name} (type: {access.server.service_type.value})")
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
            current_app.logger.error(f"Error getting libraries from {access.server.name}: {e}")
    
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
    form.allow_downloads.data = user.allow_downloads
    form.allow_4k_transcode.data = user.allow_4k_transcode
    form.is_discord_bot_whitelisted.data = user.is_discord_bot_whitelisted
    form.is_purge_whitelisted.data = user.is_purge_whitelisted
    
    # Updated logic for DateField - the form will automatically populate access_expires_at 
    # from the user object via obj=user, but we can explicitly set it if needed
    if user.access_expires_at:
        # Convert datetime to date for the DateField
        form.access_expires_at.data = user.access_expires_at.date()
    
    # We pass the _settings_tab partial, which contains the form we need.
    return render_template(
        'users/partials/settings_tab.html',
        form=form,
        user=user
    )