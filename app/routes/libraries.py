from flask import Blueprint, render_template, current_app, request, make_response, json, flash, redirect, url_for
from flask_login import login_required, current_user
from app.utils.helpers import setup_required, permission_required
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import MediaLibrary, MediaServer
from app.extensions import db
from datetime import datetime

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
            'needs_sync': not server_data['has_data']
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
                    # Plex uses 'key' field
                    lib_id = lib.get('key')
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