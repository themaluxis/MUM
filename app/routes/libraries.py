from flask import Blueprint, render_template, current_app, request, make_response, json, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from app.utils.helpers import setup_required, permission_required, encode_url_component, decode_url_component, decode_url_component_variations, generate_url_slug
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import MediaLibrary, MediaServer, MediaStreamHistory, UserMediaAccess
from app.extensions import db
from datetime import datetime, timezone, timedelta
import urllib.parse

bp = Blueprint('libraries', __name__)

@bp.route('/libraries')
@login_required
@setup_required
@permission_required('view_libraries')
def index():
    """Display libraries from stored database data instead of live API calls"""
    # Redirect AppUsers without admin permissions away from admin pages
    from app.models import UserAppAccess
    if isinstance(current_user, UserAppAccess) and not current_user.has_permission('view_libraries'):
        flash('You do not have permission to access the libraries management page.', 'danger')
        return redirect(url_for('user.index'))
    
    libraries_by_service = {}
    
    # Get all servers and their stored libraries from database
    servers_with_libraries = db.session.query(MediaServer, MediaLibrary).outerjoin(
        MediaLibrary, MediaServer.id == MediaLibrary.server_id
    ).filter(MediaServer.is_active == True).all()
    
    # Group libraries by server and service type
    servers_dict = {}
    for server, library in servers_with_libraries:
        if server.id not in servers_dict:
            servers_dict[server.id] = {
                'server': server,
                'libraries': [],
                'has_data': False
            }
        
        if library:
            servers_dict[server.id]['libraries'].append({
                'id': library.external_id,
                'name': library.name,
                'type': library.library_type,
                'item_count': library.item_count,
                'last_scanned': library.last_scanned,
                'server_name': server.server_nickname,
                'service_type': server.service_type.value,
                'server_id': server.id,
                'external_id': library.external_id
            })
            servers_dict[server.id]['has_data'] = True
    
    # Build the display structure
    for server_data in servers_dict.values():
        server = server_data['server']
        service_type = server.service_type.value.upper()
        
        if service_type not in libraries_by_service:
            libraries_by_service[service_type] = {
                'servers': {},
                'total_libraries': 0,
                'service_display_name': service_type.title()
            }
        
        libraries_by_service[service_type]['servers'][server.server_nickname] = {
            'libraries': server_data['libraries'],
            'server_id': server.id,
            'online': server_data['has_data'],
            'needs_sync': not server_data['has_data'],
            'last_sync_at': server.last_sync_at
        }
        
        libraries_by_service[service_type]['total_libraries'] += len(server_data['libraries'])

    # Calculate total servers for layout decision
    total_servers = sum(len(service_data['servers']) for service_data in libraries_by_service.values())
    total_services = len(libraries_by_service)
    
    # If only one server/service, flatten the structure for simple display
    simple_libraries = []
    if total_servers == 1 and total_services == 1:
        for service_data in libraries_by_service.values():
            for server_data in service_data['servers'].values():
                simple_libraries.extend(server_data['libraries'])

    # Check if this is an HTMX request for just the content
    if request.headers.get('HX-Request'):
        return render_template(
            'libraries/partials/libraries_content.html',
            libraries_by_service=libraries_by_service,
            simple_libraries=simple_libraries,
            total_servers=total_servers,
            total_services=total_services,
            use_simple_layout=(total_servers == 1 and total_services == 1)
        )
    
    return render_template(
        'libraries/index.html',
        title="Libraries",
        libraries_by_service=libraries_by_service,
        simple_libraries=simple_libraries,
        total_servers=total_servers,
        total_services=total_services,
        use_simple_layout=(total_servers == 1 and total_services == 1)
    )

@bp.route('/sync', methods=['POST'])
@login_required
@setup_required
@permission_required('sync_libraries')
def sync_libraries():
    """Sync libraries from all media servers and store in database"""
    current_app.logger.info("Starting library synchronization.")
    
    try:
        sync_result = {
            'success': True,
            'servers_synced': 0,
            'libraries_added': 0,
            'libraries_updated': 0,
            'libraries_removed': 0,
            'errors': 0,
            'error_messages': [],
            'added_libraries': [],
            'updated_libraries': [],
            'removed_libraries': []
        }
        
        all_servers = MediaServiceManager().get_all_servers(active_only=True)
        current_library_ids_by_server = {}  # Track current libraries to detect removals
        
        for server in all_servers:
            try:
                current_app.logger.info(f"Syncing libraries for {server.server_nickname} ({server.service_type.value})")
                service = MediaServiceFactory.create_service_from_db(server)
                
                if not service:
                    sync_result['errors'] += 1
                    sync_result['error_messages'].append(f"Could not create service for {server.server_nickname}")
                    continue
                
                # Get libraries from API
                api_libraries = service.get_libraries()
                current_library_ids_by_server[server.id] = []
                
                for lib_data in api_libraries:
                    external_id = lib_data.get('external_id') or lib_data.get('id')
                    if not external_id:
                        continue
                    
                    current_library_ids_by_server[server.id].append(external_id)
                    
                    # Check if library already exists
                    existing_library = MediaLibrary.query.filter_by(
                        server_id=server.id,
                        external_id=external_id
                    ).first()
                    
                    if existing_library:
                        # Update existing library
                        updated = False
                        changes = []
                        if existing_library.name != lib_data.get('name', 'Unknown'):
                            existing_library.name = lib_data.get('name', 'Unknown')
                            changes.append('Name updated')
                            updated = True
                        if existing_library.library_type != lib_data.get('type'):
                            existing_library.library_type = lib_data.get('type')
                            changes.append('Type updated')
                            updated = True
                        if existing_library.item_count != lib_data.get('item_count'):
                            existing_library.item_count = lib_data.get('item_count')
                            changes.append('Item count updated')
                            updated = True
                        
                        if updated:
                            existing_library.updated_at = datetime.utcnow()
                            sync_result['libraries_updated'] += 1
                            sync_result['updated_libraries'].append({
                                'name': existing_library.name,
                                'server_name': server.server_nickname,
                                'changes': changes
                            })
                    else:
                        # Create new library
                        new_library = MediaLibrary(
                            server_id=server.id,
                            external_id=external_id,
                            name=lib_data.get('name', 'Unknown'),
                            library_type=lib_data.get('type'),
                            item_count=lib_data.get('item_count'),
                            last_scanned=lib_data.get('last_scanned')
                        )
                        db.session.add(new_library)
                        sync_result['libraries_added'] += 1
                        sync_result['added_libraries'].append({
                            'name': lib_data.get('name', 'Unknown'),
                            'server_name': server.server_nickname,
                            'type': lib_data.get('type'),
                            'item_count': lib_data.get('item_count')
                        })
                
                sync_result['servers_synced'] += 1
                
            except Exception as e:
                current_app.logger.error(f"Error syncing libraries for {server.server_nickname}: {e}")
                sync_result['errors'] += 1
                sync_result['error_messages'].append(f"Error syncing {server.server_nickname}: {str(e)}")
        
        # Remove libraries that no longer exist on servers
        for server_id, current_lib_ids in current_library_ids_by_server.items():
            removed_libraries = MediaLibrary.query.filter(
                MediaLibrary.server_id == server_id,
                ~MediaLibrary.external_id.in_(current_lib_ids)
            ).all()
            
            for removed_lib in removed_libraries:
                current_app.logger.info(f"Removing library {removed_lib.name} (no longer exists on server)")
                sync_result['removed_libraries'].append({
                    'name': removed_lib.name,
                    'server_name': removed_lib.server.server_nickname
                })
                db.session.delete(removed_lib)
                sync_result['libraries_removed'] += 1
        
        # Commit all changes
        db.session.commit()
        
        # Check if there are any changes to determine response type
        has_changes = (sync_result['libraries_added'] > 0 or 
                      sync_result['libraries_updated'] > 0 or 
                      sync_result['libraries_removed'] > 0 or 
                      sync_result['errors'] > 0)
        
        if has_changes:
            # Show modal for changes or errors
            modal_html = render_template('libraries/partials/sync_results_modal.html',
                                       sync_result=sync_result)
            
            if sync_result['errors'] == 0:
                message = f"Library sync complete. {sync_result['libraries_added']} added, {sync_result['libraries_updated']} updated, {sync_result['libraries_removed']} removed."
                category = "success"
            else:
                message = f"Library sync completed with {sync_result['errors']} errors. See details."
                category = "warning"
            
            trigger_payload = {
                "showToastEvent": {"message": message, "category": category},
                "openLibrarySyncResultsModal": True,
                "refreshLibrariesPage": True
            }
            headers = {
                'HX-Retarget': '#librarySyncResultModalContainer',
                'HX-Reswap': 'innerHTML',
                'HX-Trigger-After-Swap': json.dumps(trigger_payload)
            }
            return make_response(modal_html, 200, headers)
        else:
            # No changes - just show toast
            trigger_payload = {
                "showToastEvent": {"message": "Library sync complete. No changes were made.", "category": "success"},
                "refreshLibrariesPage": True
            }
            headers = {
                'HX-Trigger': json.dumps(trigger_payload)
            }
            return make_response("", 200, headers)
        
    except Exception as e:
        current_app.logger.error(f"Critical error during library synchronization: {e}", exc_info=True)
        
        # Return error response
        toast_payload = {
            "showToastEvent": {
                "message": f"Library sync failed: {str(e)}",
                "category": "error"
            }
        }
        
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast_payload)
        return response

@bp.route('/library/<int:server_id>/<library_id>/raw-data')
@login_required
@setup_required
@permission_required('view_libraries')
def get_library_raw_data(server_id, library_id):
    """Get raw API data for a specific library"""
    try:
        # Get the server
        server = MediaServer.query.get_or_404(server_id)
        
        # Create service instance
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {'error': 'Could not create service instance'}, 500
        
        # Get raw libraries data (unmodified API response)
        if hasattr(service, 'get_libraries_raw'):
            # Use raw data method if available (shows true API response)
            libraries = service.get_libraries_raw()
            # For raw data, we need to match against the actual API field names
            target_library = None
            for lib in libraries:
                # Check different possible ID fields from the raw API based on service type
                lib_id = None
                if server.service_type.value.lower() == 'plex':
                    # Plex uses UUID exclusively
                    lib_uuid = lib.get('uuid')
                    if str(lib_uuid) == str(library_id):
                        target_library = lib
                        break
                    continue
                elif server.service_type.value.lower() in ['jellyfin', 'emby']:
                    # Jellyfin/Emby use 'ItemId'
                    lib_id = lib.get('ItemId')
                elif server.service_type.value.lower() in ['kavita', 'komga', 'romm']:
                    # These services use 'id'
                    lib_id = lib.get('id')
                elif server.service_type.value.lower() == 'audiobookshelf':
                    # AudioBookshelf uses 'id'
                    lib_id = lib.get('id')
                else:
                    # Fallback - try common field names
                    lib_id = lib.get('ItemId') or lib.get('id') or lib.get('key') or lib.get('external_id')
                
                if str(lib_id) == str(library_id):
                    target_library = lib
                    break
        else:
            # Fallback to processed data for services that don't have raw method
            libraries = service.get_libraries()
            target_library = None
            for lib in libraries:
                lib_external_id = lib.get('external_id') or lib.get('id')
                if str(lib_external_id) == str(library_id):
                    target_library = lib
                    break
        
        if not target_library:
            return {'error': f'Library with ID {library_id} not found on server'}, 404
        
        return {
            'success': True,
            'library_data': target_library,
            'server_info': {
                'name': server.server_nickname,
                'service_type': server.service_type.value,
                'url': server.url
            }
        }
        
    except Exception as e:
        current_app.logger.error(f"Error fetching raw library data: {e}")
        return {'error': str(e)}, 500

@bp.route('/library/<server_nickname>/<library_name>/<int:media_id>/<tv_show_slug>/<episode_slug>')
@login_required
@setup_required
@permission_required('view_libraries')
def episode_detail(server_nickname, library_name, media_id, tv_show_slug, episode_slug):
    """Display detailed episode information"""
    # URL decode the parameters to handle special characters
    try:
        server_nickname = urllib.parse.unquote(server_nickname)
        library_name = urllib.parse.unquote(library_name)
        tv_show_slug = urllib.parse.unquote(tv_show_slug)
        episode_slug = urllib.parse.unquote(episode_slug)
        
        # Decode URL component back to original name for lookup
        library_name_for_lookup = decode_url_component(library_name)
        tv_show_name = decode_url_component(tv_show_slug)
        episode_name = decode_url_component(episode_slug)
        
    except Exception as e:
        current_app.logger.warning(f"Error decoding URL parameters: {e}")
        abort(400)
    
    # Validate parameters
    if not server_nickname or not library_name or not media_id or not tv_show_slug or not episode_slug:
        abort(400)
    
    # Find the server by nickname
    server = MediaServer.query.filter_by(server_nickname=server_nickname).first_or_404()
    
    # Find the library by name and server - try multiple variations for library name
    library = None
    library_name_variations = decode_url_component_variations(library_name)
    
    for variation in library_name_variations:
        library = MediaLibrary.query.filter_by(
            server_id=server.id,
            name=variation
        ).first()
        if library:
            # Store the actual library name that worked
            library_name_for_lookup = variation
            break
    
    if not library:
        abort(404)
    
    # Get the TV show details by database ID
    from app.models_media_services import MediaItem
    tv_show_item = MediaItem.query.filter_by(
        id=media_id,
        library_id=library.id  # Ensure the media belongs to this library
    ).first_or_404()
    
    # Get episode details from the service
    from app.services.media_service_factory import MediaServiceFactory
    service = MediaServiceFactory.create_service_from_db(server)
    if not service:
        abort(500)
    
    # Get episodes for the show to find the specific episode
    episode_details = None
    if hasattr(service, 'get_show_episodes'):
        episodes_data = service.get_show_episodes(tv_show_item.external_id, page=1, per_page=1000)
        if episodes_data and episodes_data.get('items'):
            for episode in episodes_data['items']:
                if generate_url_slug(episode.get('title', '')) == episode_slug:
                    episode_details = episode
                    break
    
    if not episode_details:
        abort(404)
    
    # Get streaming history for this specific episode
    streaming_history = None
    page = request.args.get('page', 1, type=int)
    days_filter = int(request.args.get('days', 30))
    
    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days_filter)
    
    # Get streaming history for this specific episode
    activity_query = MediaStreamHistory.query.filter(
        MediaStreamHistory.server_id == server.id,
        MediaStreamHistory.library_name == library_name_for_lookup,
        MediaStreamHistory.media_title == episode_details.get('title'),
        MediaStreamHistory.started_at >= start_date,
        MediaStreamHistory.started_at <= end_date
    ).order_by(MediaStreamHistory.started_at.desc())
    
    # Paginate the results
    activity_pagination = activity_query.paginate(
        page=page, per_page=20, error_out=False
    )
    
    # Enhance activity entries with user info
    for entry in activity_pagination.items:
        if entry.user_media_access_uuid:
            user_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
            if user_access:
                entry.user_display_name = user_access.get_display_name()
                entry.user_type = 'service'
            else:
                entry.user_display_name = 'Unknown User'
                entry.user_type = 'unknown'
        elif entry.user_app_access_uuid:
            from app.models import UserAppAccess
            user_app = UserAppAccess.query.filter_by(uuid=entry.user_app_access_uuid).first()
            if user_app:
                entry.user_display_name = user_app.get_display_name()
                entry.user_type = 'local'
            else:
                entry.user_display_name = 'Unknown User'
                entry.user_type = 'unknown'
        else:
            entry.user_display_name = 'Unknown User'
            entry.user_type = 'unknown'
    
    streaming_history = activity_pagination
    
    return render_template('libraries/episode_detail.html',
                         title=f"Episode: {episode_details.get('title')}",
                         episode_details=episode_details,
                         tv_show_item=tv_show_item,
                         library=library,
                         server=server,
                         streaming_history=streaming_history,
                         days_filter=days_filter)

@bp.route('/library/<server_nickname>/<library_name>/<int:media_id>')
@bp.route('/library/<server_nickname>/<library_name>/<int:media_id>/<slug>')
@login_required
@setup_required
@permission_required('view_libraries')
def media_detail(server_nickname, library_name, media_id, slug=None):
    """Display detailed media information using database ID lookup"""
    # URL decode the parameters to handle special characters
    try:
        server_nickname = urllib.parse.unquote(server_nickname)
        library_name = urllib.parse.unquote(library_name)
        
        # Decode URL component back to original name for lookup
        library_name_for_lookup = decode_url_component(library_name)
        
    except Exception as e:
        current_app.logger.warning(f"Error decoding URL parameters: {e}")
        abort(400)
    
    # Validate parameters
    if not server_nickname or not library_name or not media_id:
        abort(400)
    
    # Find the server by nickname
    server = MediaServer.query.filter_by(server_nickname=server_nickname).first_or_404()
    
    # Find the library by name and server - try multiple variations for library name
    library = None
    library_name_variations = decode_url_component_variations(library_name)
    
    for variation in library_name_variations:
        library = MediaLibrary.query.filter_by(
            server_id=server.id,
            name=variation
        ).first()
        if library:
            # Store the actual library name that worked
            library_name_for_lookup = variation
            break
    
    if not library:
        abort(404)
    
    # Get the active tab from the URL query, default to 'overview'
    tab = request.args.get('tab', 'overview')
    
    # Get media details by database ID - this is fast and reliable!
    from app.models_media_services import MediaItem
    media_item = MediaItem.query.filter_by(
        id=media_id,
        library_id=library.id  # Ensure the media belongs to this library
    ).first_or_404()
    
    # Convert database item to the expected format
    media_details = media_item.to_dict()
    content_name_for_lookup = media_item.title  # Use the actual title from database
    
    # Get episodes for TV shows
    episodes_content = None
    if tab == 'episodes' and library.library_type and library.library_type.lower() in ['show', 'tv', 'series', 'tvshows']:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 24, type=int)
        search_query = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'title_asc').strip()
        
        # Validate per_page parameter
        if per_page not in [12, 24, 48, 96]:
            per_page = 24
            
        # Validate sort_by parameter
        valid_sorts = [
            'title_asc', 'title_desc', 'year_asc', 'year_desc', 
            'added_at_asc', 'added_at_desc', 'rating_asc', 'rating_desc',
            'total_streams_asc', 'total_streams_desc'
        ]
        if sort_by not in valid_sorts:
            sort_by = 'title_asc'
            
        episodes_content = get_show_episodes(server, library, content_name_for_lookup, page, per_page, search_query, sort_by)
    
    # Get streaming history for this specific content
    streaming_history = None
    if tab == 'activity':
        page = request.args.get('page', 1, type=int)
        days_filter = int(request.args.get('days', 30))
        
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_filter)
        
        # Get streaming history for this specific content
        # For TV shows, we need to check grandparent_title (show name) instead of media_title (episode name)
        if library.library_type and library.library_type.lower() in ['show', 'tv', 'series', 'tvshows']:
            # For TV shows, filter by grandparent_title to get all episodes of the show
            activity_query = MediaStreamHistory.query.filter(
                MediaStreamHistory.server_id == server.id,
                MediaStreamHistory.library_name == library_name_for_lookup,
                MediaStreamHistory.grandparent_title == content_name_for_lookup,
                MediaStreamHistory.started_at >= start_date,
                MediaStreamHistory.started_at <= end_date
            ).order_by(MediaStreamHistory.started_at.desc())
        else:
            # For movies and other content, filter by media_title
            activity_query = MediaStreamHistory.query.filter(
                MediaStreamHistory.server_id == server.id,
                MediaStreamHistory.library_name == library_name_for_lookup,
                MediaStreamHistory.media_title == content_name_for_lookup,
                MediaStreamHistory.started_at >= start_date,
                MediaStreamHistory.started_at <= end_date
            ).order_by(MediaStreamHistory.started_at.desc())
        
        # Paginate the results
        activity_pagination = activity_query.paginate(
            page=page, per_page=20, error_out=False
        )
        
        # Enhance activity entries with user info
        for entry in activity_pagination.items:
            if entry.user_media_access_uuid:
                user_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
                if user_access:
                    entry.user_display_name = user_access.get_display_name()
                    entry.user_type = 'service'
                    
                    # Get avatar URL for Plex users
                    entry.user_avatar_url = None
                    if server.service_type.value.lower() == 'plex':
                        # For Plex, check multiple possible locations for the thumb URL
                        thumb_url = None
                        
                        # First try service_settings
                        if user_access.service_settings and user_access.service_settings.get('thumb'):
                            thumb_url = user_access.service_settings['thumb']
                        # Then try raw_data from the user sync
                        elif user_access.user_raw_data and user_access.user_raw_data.get('thumb'):
                            thumb_url = user_access.user_raw_data['thumb']
                        # Also check nested raw data structure
                        elif (user_access.user_raw_data and 
                              user_access.user_raw_data.get('plex_user_obj_attrs') and 
                              user_access.user_raw_data['plex_user_obj_attrs'].get('thumb')):
                            thumb_url = user_access.user_raw_data['plex_user_obj_attrs']['thumb']
                        
                        if thumb_url:
                            # Check if it's already a full URL (plex.tv avatars) or needs proxy
                            if thumb_url.startswith('https://plex.tv/') or thumb_url.startswith('http://plex.tv/'):
                                entry.user_avatar_url = thumb_url
                            else:
                                entry.user_avatar_url = f"/api/media/plex/images/proxy?path={thumb_url.lstrip('/')}"
                    
                    elif server.service_type.value.lower() == 'jellyfin':
                        # For Jellyfin, use the external_user_id to get avatar
                        if user_access.external_user_id:
                            entry.user_avatar_url = f"/api/media/jellyfin/users/avatar?user_id={user_access.external_user_id}"
                else:
                    entry.user_display_name = 'Unknown User'
                    entry.user_type = 'unknown'
                    entry.user_avatar_url = None
            elif entry.user_app_access_uuid:
                from app.models import UserAppAccess
                user_app = UserAppAccess.query.filter_by(uuid=entry.user_app_access_uuid).first()
                if user_app:
                    entry.user_display_name = user_app.get_display_name()
                    entry.user_type = 'local'
                    entry.user_avatar_url = None  # Local users don't have service avatars
                else:
                    entry.user_display_name = 'Unknown User'
                    entry.user_type = 'unknown'
                    entry.user_avatar_url = None
            else:
                entry.user_display_name = 'Unknown User'
                entry.user_type = 'unknown'
                entry.user_avatar_url = None
        
        streaming_history = activity_pagination
    
    # Handle HTMX requests for tab content
    if request.headers.get('HX-Request') and tab == 'activity':
        return render_template('libraries/partials/media_activity_tab.html',
                             media_details=media_details,
                             library=library,
                             server=server,
                             streaming_history=streaming_history,
                             days_filter=request.args.get('days', 30))
    
    return render_template('libraries/media_detail.html',
                         title=f"Media: {media_item.title}",
                         media_details=media_details,
                         media_item=media_item,  # Pass the database object for URL generation
                         library=library,
                         server=server,
                         streaming_history=streaming_history,
                         episodes_content=episodes_content,
                         active_tab=tab,
                         days_filter=request.args.get('days', 30) if tab == 'activity' else None,
                         current_sort_by=request.args.get('sort_by', 'title_asc') if tab == 'episodes' else None)

@bp.route('/library/<server_nickname>/<library_name>')
@login_required
@setup_required
@permission_required('view_libraries')
def library_detail(server_nickname, library_name):
    """Display detailed library information and statistics"""
    # URL decode the parameters to handle special characters
    try:
        server_nickname = urllib.parse.unquote(server_nickname)
        library_name = urllib.parse.unquote(library_name)
        
        # Decode URL component back to original name for lookup
        library_name_for_lookup = decode_url_component(library_name)
        
        # If the URL contains spaces or other special characters, redirect to the proper format
        proper_library_name = encode_url_component(library_name)
        if library_name != proper_library_name:
            return redirect(url_for('libraries.library_detail', 
                                  server_nickname=server_nickname,
                                  library_name=proper_library_name,
                                  **request.args))
        
    except Exception as e:
        current_app.logger.warning(f"Error decoding URL parameters: {e}")
        abort(400)
    
    # Validate parameters
    if not server_nickname or not library_name:
        abort(400)
    
    # Find the server by nickname
    server = MediaServer.query.filter_by(server_nickname=server_nickname).first_or_404()
    
    # Find the library by name and server - try multiple variations for library name
    library = None
    library_name_variations = decode_url_component_variations(library_name)
    
    for variation in library_name_variations:
        library = MediaLibrary.query.filter_by(
            server_id=server.id,
            name=variation
        ).first()
        if library:
            # Store the actual library name that worked
            library_name_for_lookup = variation
            break
    
    if not library:
        abort(404)
    
    # Get the active tab from the URL query, default to 'overview'
    tab = request.args.get('tab', 'overview')
    
    # Get library statistics
    library_stats = get_library_statistics(library)
    
    # Get chart data for stats tab
    chart_data = None
    user_stats = None
    if tab == 'stats':
        days_param = request.args.get('days', '30')
        try:
            if days_param == 'all':
                days = -1
            else:
                days = int(days_param)
                # Validate days parameter
                if days not in [7, 30, 90, 365]:
                    days = 30
        except (ValueError, TypeError):
            days = 30
        
        chart_data = generate_library_chart_data(library, days)
        user_stats = get_library_user_stats(library, days)
    
    # Get media content for media tab
    media_content = None
    if tab == 'media':
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 24, type=int)
        search_query = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'title').strip()
        
        # Validate per_page parameter
        if per_page not in [12, 24, 48, 96]:
            per_page = 24
            
        # Validate sort_by parameter
        valid_sorts = [
            'title_asc', 'title_desc', 'year_asc', 'year_desc', 
            'added_at_asc', 'added_at_desc', 'rating_asc', 'rating_desc',
            'total_streams_asc', 'total_streams_desc'
        ]
        if sort_by not in valid_sorts:
            sort_by = 'title_asc'
            
        media_content = get_library_media_content(library, page, per_page, search_query, sort_by)
    
    # Get recent activity for this library
    recent_activity = []
    if tab == 'activity':
        page = request.args.get('page', 1, type=int)
        days_filter = int(request.args.get('days', 30))
        
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_filter)
        
        # Get streaming history for this library
        activity_query = MediaStreamHistory.query.filter(
            MediaStreamHistory.server_id == server.id,
            MediaStreamHistory.library_name == library_name,
            MediaStreamHistory.started_at >= start_date,
            MediaStreamHistory.started_at <= end_date
        ).order_by(MediaStreamHistory.started_at.desc())
        
        # Paginate the results
        activity_pagination = activity_query.paginate(
            page=page, per_page=20, error_out=False
        )
        
        # Enhance activity entries with user info
        for entry in activity_pagination.items:
            if entry.user_media_access_uuid:
                user_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
                if user_access:
                    entry.user_display_name = user_access.get_display_name()
                    entry.user_type = 'service'
                else:
                    entry.user_display_name = 'Unknown User'
                    entry.user_type = 'unknown'
            elif entry.user_app_access_uuid:
                from app.models import UserAppAccess
                user_app = UserAppAccess.query.filter_by(uuid=entry.user_app_access_uuid).first()
                if user_app:
                    entry.user_display_name = user_app.get_display_name()
                    entry.user_type = 'local'
                else:
                    entry.user_display_name = 'Unknown User'
                    entry.user_type = 'unknown'
            else:
                entry.user_display_name = 'Unknown User'
                entry.user_type = 'unknown'
        
        recent_activity = activity_pagination
    
    # Handle HTMX requests for tab content
    if request.headers.get('HX-Request') and tab == 'activity':
        return render_template('libraries/partials/library_activity_tab.html',
                             library=library,
                             server=server,
                             recent_activity=recent_activity,
                             days_filter=request.args.get('days', 30))
    
    return render_template('libraries/library_detail.html',
                         title=f"Library: {library_name}",
                         library=library,
                         server=server,
                         library_stats=library_stats,
                         recent_activity=recent_activity,
                         chart_data=chart_data,
                         user_stats=user_stats,
                         media_content=media_content,
                         active_tab=tab,
                         selected_days=request.args.get('days', 30) if tab == 'stats' else None,
                         days_filter=request.args.get('days', 30) if tab == 'activity' else None,
                         current_sort_by=request.args.get('sort_by', 'title_asc') if tab == 'media' else None)

def get_library_statistics(library):
    """Get statistics for a library"""
    try:
        # Get streaming statistics for this library
        total_streams = MediaStreamHistory.query.filter(
            MediaStreamHistory.server_id == library.server_id,
            MediaStreamHistory.library_name == library.name
        ).count()
        
        # Get unique users who have accessed this library
        unique_users = db.session.query(MediaStreamHistory.user_media_access_uuid)\
            .filter(
                MediaStreamHistory.server_id == library.server_id,
                MediaStreamHistory.library_name == library.name,
                MediaStreamHistory.user_media_access_uuid.isnot(None)
            ).distinct().count()
        
        # Get total watch time (in seconds)
        total_watch_time = db.session.query(db.func.sum(MediaStreamHistory.duration_seconds))\
            .filter(
                MediaStreamHistory.server_id == library.server_id,
                MediaStreamHistory.library_name == library.name,
                MediaStreamHistory.duration_seconds.isnot(None)
            ).scalar() or 0
        
        # Get most popular content
        popular_content = db.session.query(
            MediaStreamHistory.media_title,
            db.func.count(MediaStreamHistory.id).label('play_count')
        ).filter(
            MediaStreamHistory.server_id == library.server_id,
            MediaStreamHistory.library_name == library.name
        ).group_by(MediaStreamHistory.media_title)\
         .order_by(db.func.count(MediaStreamHistory.id).desc())\
         .limit(5).all()
        
        # Format watch time
        def format_duration(seconds):
            if not seconds:
                return "0m"
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        
        return {
            'total_streams': total_streams,
            'unique_users': unique_users,
            'total_watch_time': total_watch_time,
            'total_watch_time_formatted': format_duration(total_watch_time),
            'popular_content': popular_content,
            'item_count': library.item_count or 0,
            'library_type': library.library_type or 'Unknown'
        }
    except Exception as e:
        current_app.logger.error(f"Error getting library statistics: {e}")
        return {
            'total_streams': 0,
            'unique_users': 0,
            'total_watch_time': 0,
            'total_watch_time_formatted': '0m',
            'popular_content': [],
            'item_count': library.item_count or 0,
            'library_type': library.library_type or 'Unknown'
        }

def generate_library_chart_data(library, days=30):
    """Generate chart data for library streaming activity by user"""
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict
    from app.utils.helpers import format_duration
    
    # Calculate date range based on days parameter
    end_date = datetime.now(timezone.utc)
    if days == -1:  # All time
        # Get the earliest stream date for this library
        earliest_stream = MediaStreamHistory.query.filter(
            MediaStreamHistory.server_id == library.server_id,
            MediaStreamHistory.library_name == library.name
        ).order_by(MediaStreamHistory.started_at.asc()).first()
        
        if earliest_stream:
            start_date = earliest_stream.started_at
        else:
            start_date = end_date - timedelta(days=30)  # Fallback to 30 days
    else:
        start_date = end_date - timedelta(days=days-1)
    
    # Get streaming history for this library
    streaming_history = MediaStreamHistory.query.filter(
        MediaStreamHistory.server_id == library.server_id,
        MediaStreamHistory.library_name == library.name,
        MediaStreamHistory.started_at >= start_date,
        MediaStreamHistory.started_at <= end_date
    ).all()
    
    if not streaming_history:
        return {
            'chart_data': [],
            'users': [],
            'user_combinations': [],
            'user_colors': {},
            'total_streams': 0,
            'total_duration': '0m',
            'most_active_user': 'None',
            'date_range_days': days
        }
    
    # Determine grouping strategy based on days parameter
    if days == 7:
        grouping_type = 'daily'
    elif days in [30, 90]:
        grouping_type = 'weekly'
    elif days == 365 or days == -1:
        grouping_type = 'monthly'
    else:
        grouping_type = 'daily'
    
    # Group data by time period (total plays and total time)
    grouped_data = defaultdict(lambda: {'plays': 0, 'time': 0})
    total_duration_seconds = 0
    total_plays = 0
    
    for entry in streaming_history:
        # Get the date (without time)
        entry_date = entry.started_at.date()
        
        # Determine the grouping key based on grouping type
        if grouping_type == 'monthly':
            group_key = entry_date.strftime('%Y-%m')
        elif grouping_type == 'weekly':
            days_since_monday = entry_date.weekday()
            week_start = entry_date - timedelta(days=days_since_monday)
            group_key = week_start.isoformat()
        else:  # daily
            group_key = entry_date.isoformat()
        
        # Get duration in minutes for the chart
        duration_minutes = 0
        if entry.duration_seconds and entry.duration_seconds > 0:
            duration_minutes = entry.duration_seconds / 60
            total_duration_seconds += entry.duration_seconds
        elif entry.view_offset_at_end_seconds and entry.view_offset_at_end_seconds > 0:
            duration_minutes = entry.view_offset_at_end_seconds / 60
            total_duration_seconds += entry.view_offset_at_end_seconds
        else:
            duration_minutes = 1  # 1 minute minimum to show activity
        
        # Add plays and time per group
        grouped_data[group_key]['plays'] += 1
        grouped_data[group_key]['time'] += duration_minutes
        total_plays += 1
    
    # Generate chart data for the date range
    chart_data_list = []
    
    # Generate time periods based on grouping type
    if grouping_type == 'monthly':
        # Generate monthly periods
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        current_date = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        while current_date <= end_date_month:
            month_key = current_date.strftime('%Y-%m')
            month_label = current_date.strftime('%b %Y')
            
            period_data = {
                'date': month_key, 
                'label': month_label,
                'plays': grouped_data[month_key]['plays'],
                'time': round(grouped_data[month_key]['time'], 1)
            }
            
            chart_data_list.append(period_data)
            
            # Move to next month
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1)
                
    elif grouping_type == 'weekly':
        # Generate weekly periods
        start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
        end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
        
        days_since_monday = start_date_only.weekday()
        current_week_start = start_date_only - timedelta(days=days_since_monday)
        
        while current_week_start <= end_date_only:
            week_key = current_week_start.isoformat()
            week_end = current_week_start + timedelta(days=6)
            
            if current_week_start.month == week_end.month:
                week_label = f"{current_week_start.strftime('%b %d')}-{week_end.day}"
            else:
                week_label = f"{current_week_start.strftime('%b %d')}-{week_end.strftime('%b %d')}"
            
            period_data = {
                'date': week_key, 
                'label': week_label,
                'plays': grouped_data[week_key]['plays'],
                'time': round(grouped_data[week_key]['time'], 1)
            }
            
            chart_data_list.append(period_data)
            current_week_start += timedelta(days=7)
            
    else:  # daily
        # Generate daily periods
        start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
        end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
        
        current_date = start_date_only
        while current_date <= end_date_only:
            day_key = current_date.isoformat()
            day_label = current_date.strftime('%b %d')
            
            period_data = {
                'date': day_key, 
                'label': day_label,
                'plays': grouped_data[day_key]['plays'],
                'time': round(grouped_data[day_key]['time'], 1)
            }
            
            chart_data_list.append(period_data)
            current_date += timedelta(days=1)
    
    # Calculate summary stats
    total_duration_formatted = format_duration(total_duration_seconds)
    
    return {
        'chart_data': chart_data_list,
        'total_streams': total_plays,
        'total_duration': total_duration_formatted,
        'date_range_days': days
    }

def get_library_user_stats(library, days=30):
    """Get user statistics for a library"""
    from datetime import datetime, timezone, timedelta
    from app.models_media_services import UserMediaAccess
    
    try:
        # Calculate date range based on days parameter
        end_date = datetime.now(timezone.utc)
        if days == -1:  # All time
            # Get the earliest stream date for this library
            earliest_stream = MediaStreamHistory.query.filter(
                MediaStreamHistory.server_id == library.server_id,
                MediaStreamHistory.library_name == library.name
            ).order_by(MediaStreamHistory.started_at.asc()).first()
            
            if earliest_stream:
                start_date = earliest_stream.started_at
            else:
                start_date = end_date - timedelta(days=30)  # Fallback to 30 days
        else:
            start_date = end_date - timedelta(days=days-1)
        
        # Get user statistics for this library
        user_stats_query = db.session.query(
            MediaStreamHistory.user_media_access_uuid,
            UserMediaAccess.external_username,
            UserMediaAccess.external_email,
            UserMediaAccess.external_avatar_url,
            db.func.count(MediaStreamHistory.id).label('play_count'),
            db.func.sum(MediaStreamHistory.duration_seconds).label('total_duration')
        ).join(
            UserMediaAccess, 
            MediaStreamHistory.user_media_access_uuid == UserMediaAccess.uuid
        ).filter(
            MediaStreamHistory.server_id == library.server_id,
            MediaStreamHistory.library_name == library.name,
            MediaStreamHistory.started_at >= start_date,
            MediaStreamHistory.started_at <= end_date,
            MediaStreamHistory.user_media_access_uuid.isnot(None)
        ).group_by(
            MediaStreamHistory.user_media_access_uuid,
            UserMediaAccess.external_username,
            UserMediaAccess.external_email,
            UserMediaAccess.external_avatar_url
        ).order_by(db.func.count(MediaStreamHistory.id).desc()).all()
        
        # Format user stats
        user_stats = []
        for stat in user_stats_query:
            # Get display name (prefer external_username, fallback to external_email)
            display_name = stat.external_username or stat.external_email or 'Unknown User'
            
            # Format duration
            total_seconds = stat.total_duration or 0
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            
            if hours > 0:
                duration_formatted = f"{hours}h {minutes}m"
            else:
                duration_formatted = f"{minutes}m"
            
            # Determine avatar URL based on server type
            avatar_url = None
            if stat.external_avatar_url:
                # Use the stored external avatar URL directly
                avatar_url = stat.external_avatar_url
            elif stat.user_media_access_uuid:
                # Get the full user record to access raw_data and service_settings
                user_access = UserMediaAccess.query.filter_by(uuid=stat.user_media_access_uuid).first()
                if user_access:
                    if library.server.service_type.value.lower() == 'plex':
                        # For Plex, check multiple possible locations for the thumb URL
                        thumb_url = None
                        
                        # First try service_settings
                        if user_access.service_settings and user_access.service_settings.get('thumb'):
                            thumb_url = user_access.service_settings['thumb']
                        # Then try raw_data from the user sync
                        elif user_access.user_raw_data and user_access.user_raw_data.get('thumb'):
                            thumb_url = user_access.user_raw_data['thumb']
                        # Also check nested raw data structure
                        elif (user_access.user_raw_data and 
                              user_access.user_raw_data.get('plex_user_obj_attrs') and 
                              user_access.user_raw_data['plex_user_obj_attrs'].get('thumb')):
                            thumb_url = user_access.user_raw_data['plex_user_obj_attrs']['thumb']
                        
                        if thumb_url:
                            # Check if it's already a full URL (plex.tv avatars) or needs proxy
                            if thumb_url.startswith('https://plex.tv/') or thumb_url.startswith('http://plex.tv/'):
                                avatar_url = thumb_url
                            else:
                                avatar_url = f"/api/media/plex/images/proxy?path={thumb_url.lstrip('/')}"
                    
                    elif library.server.service_type.value.lower() == 'jellyfin':
                        # For Jellyfin, use the external_user_id to get avatar
                        if user_access.external_user_id:
                            avatar_url = f"/api/media/jellyfin/users/avatar?user_id={user_access.external_user_id}"
            
            user_stats.append({
                'uuid': stat.user_media_access_uuid,
                'display_name': display_name,
                'username': stat.external_username,
                'email': stat.external_email,
                'avatar_url': avatar_url,
                'play_count': stat.play_count,
                'total_duration_seconds': total_seconds,
                'total_duration_formatted': duration_formatted
            })
        
        return user_stats
        
    except Exception as e:
        current_app.logger.error(f"Error getting library user stats: {e}")
        return []

def get_media_details_cached_only(server, library, content_name):
    """Get detailed information about a specific media item from database cache only"""
    try:
        from app.models_media_services import MediaItem
        from flask import current_app
        
        current_app.logger.info(f"DEBUG get_media_details_cached_only: Looking for title='{content_name}' in library_id={library.id}")
        
        # Only check cached database, no API calls
        media_item = MediaItem.query.filter_by(
            library_id=library.id,
            title=content_name
        ).first()
        
        if media_item:
            current_app.logger.info(f"DEBUG: Found media item in database cache: '{media_item.title}'")
            # Convert database item to dict format
            return {
                'id': media_item.external_id,
                'title': media_item.title,
                'sort_title': media_item.sort_title,
                'type': media_item.item_type,
                'summary': media_item.summary,
                'year': media_item.year,
                'rating': media_item.rating,
                'duration': media_item.duration,
                'thumb': media_item.thumb_path,
                'added_at': media_item.added_at,
                'last_synced': media_item.last_synced,
                'raw_data': media_item.extra_metadata or {}
            }
        
        current_app.logger.info(f"DEBUG: No cached media item found for '{content_name}'")
        return None
        
    except Exception as e:
        current_app.logger.error(f"Error getting cached media details for '{content_name}': {e}")
        return None


def get_media_details(server, library, content_name):
    """Get detailed information about a specific media item"""
    try:
        from app.services.media_service_factory import MediaServiceFactory
        from app.models_media_services import MediaItem
        from flask import current_app
        
        current_app.logger.info(f"DEBUG get_media_details: Looking for title='{content_name}' in library_id={library.id}")
        
        # First try to get from cached database
        media_item = MediaItem.query.filter_by(
            library_id=library.id,
            title=content_name
        ).first()
        
        # Debug: Show what titles are actually in the database for this library
        all_titles = MediaItem.query.filter_by(library_id=library.id).with_entities(MediaItem.title).all()
        current_app.logger.info(f"DEBUG: Available titles in library: {[t[0] for t in all_titles[:10]]}...")  # Show first 10
        
        if media_item:
            current_app.logger.info(f"DEBUG: Found media item in database: '{media_item.title}'")
            # Convert database item to dict format
            return {
                'id': media_item.external_id,
                'title': media_item.title,
                'sort_title': media_item.sort_title,
                'type': media_item.item_type,
                'summary': media_item.summary,
                'year': media_item.year,
                'rating': media_item.rating,
                'duration': media_item.duration,
                'thumb': media_item.thumb_path,
                'added_at': media_item.added_at,
                'last_synced': media_item.last_synced,
                'raw_data': media_item.extra_metadata or {}
            }
        
        # If not in cache, try to get from service API
        service = MediaServiceFactory.create_service_from_db(server)
        if not service or not hasattr(service, 'get_library_content'):
            return None
        
        # Search for the content in the library
        try:
            content_data = service.get_library_content(library.external_id, page=1, per_page=100)
            items = content_data.get('items', [])
            
            # Find the specific content by title
            for item in items:
                if item.get('title') == content_name:
                    return item
            
            # If not found in first page, search more pages
            page = 2
            while page <= 10:  # Limit search to 10 pages
                content_data = service.get_library_content(library.external_id, page=page, per_page=100)
                items = content_data.get('items', [])
                
                if not items:
                    break
                    
                for item in items:
                    if item.get('title') == content_name:
                        return item
                        
                page += 1
                
        except Exception as e:
            current_app.logger.error(f"Error searching for media content: {e}")
        
        return None
        
    except Exception as e:
        current_app.logger.error(f"Error getting media details: {e}")
        return None

def get_show_episodes(server, library, show_title, page=1, per_page=24, search_query='', sort_by='title_asc'):
    """Get episodes for a specific TV show"""
    try:
        from app.services.media_service_factory import MediaServiceFactory
        
        # Create service instance
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {
                'items': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'pages': 0,
                'has_prev': False,
                'has_next': False,
                'error': 'Could not create service instance'
            }
        
        # Get show details first to find the show ID
        show_details = get_media_details(server, library, show_title)
        if not show_details:
            return {
                'items': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'pages': 0,
                'has_prev': False,
                'has_next': False,
                'error': 'Show not found'
            }
        
        # Get episodes from the service
        if hasattr(service, 'get_show_episodes'):
            episodes_data = service.get_show_episodes(show_details['id'], page=page, per_page=per_page, search_query=search_query)
        elif hasattr(service, 'get_library_content'):
            # Fallback: try to get episodes by searching for the show in the library
            episodes_data = service.get_library_content(library.external_id, page=page, per_page=per_page, parent_id=show_details['id'])
        else:
            return {
                'items': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'pages': 0,
                'has_prev': False,
                'has_next': False,
                'error': 'Service does not support episode retrieval'
            }
        
        # Add stream counts to episodes
        if episodes_data and episodes_data.get('items'):
            from app.models_media_services import MediaStreamHistory
            for episode in episodes_data['items']:
                stream_count = MediaStreamHistory.query.filter(
                    MediaStreamHistory.server_id == server.id,
                    MediaStreamHistory.media_title == episode.get('title', '')
                ).count()
                episode['stream_count'] = stream_count
        
        # Apply sorting if needed (some services might not support server-side sorting)
        if episodes_data and episodes_data.get('items') and sort_by != 'title_asc':
            items = episodes_data['items']
            reverse = sort_by.endswith('_desc')
            
            if sort_by.startswith('title'):
                items.sort(key=lambda x: x.get('title', '').lower(), reverse=reverse)
            elif sort_by.startswith('year'):
                items.sort(key=lambda x: x.get('year', 0) or 0, reverse=reverse)
            elif sort_by.startswith('added_at'):
                items.sort(key=lambda x: x.get('added_at', ''), reverse=reverse)
            elif sort_by.startswith('rating'):
                items.sort(key=lambda x: x.get('rating', 0) or 0, reverse=reverse)
            elif sort_by.startswith('total_streams'):
                items.sort(key=lambda x: x.get('stream_count', 0), reverse=reverse)
            
            episodes_data['items'] = items
        
        return episodes_data
        
    except Exception as e:
        current_app.logger.error(f"Error getting episodes for show '{show_title}': {e}")
        return {
            'items': [],
            'total': 0,
            'page': page,
            'per_page': per_page,
            'pages': 0,
            'has_prev': False,
            'has_next': False,
            'error': str(e)
        }


def get_library_media_content(library, page=1, per_page=24, search_query='', sort_by='title_asc'):
    """Get media content from the library using cached data or live API"""
    try:
        from app.services.media_sync_service import MediaSyncService
        
        # Check if we have cached data that's recent enough
        if MediaSyncService.is_library_synced(library.id, max_age_hours=24):
            current_app.logger.debug(f"Using cached data for library {library.name}")
            return MediaSyncService.get_cached_library_content(library.id, page, per_page, search_query, sort_by)
        
        # Check if we have any cached data at all (regardless of age)
        cached_content = MediaSyncService.get_cached_library_content(library.id, page, per_page, search_query, sort_by)
        if cached_content and cached_content.get('items'):
            current_app.logger.debug(f"Using older cached data for library {library.name}")
            return cached_content
        
        # Return empty result - no cached data available, user needs to sync first
        current_app.logger.info(f"No cached data for library {library.name}, returning empty result. User needs to sync first.")
        return {
            'items': [],
            'total': 0,
            'page': page,
            'per_page': per_page,
            'pages': 0,
            'has_prev': False,
            'has_next': False,
            'needs_sync': True  # Flag to indicate sync is needed
        }
        
    except Exception as e:
        current_app.logger.error(f"Error fetching media content for library {library.name}: {e}")
        return {
            'items': [],
            'total': 0,
            'page': page,
            'per_page': per_page,
            'pages': 0,
            'has_prev': False,
            'has_next': False,
            'error': str(e)
        }