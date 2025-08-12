# File: app/routes/api.py
from flask import Blueprint, request, current_app, render_template, Response, abort, jsonify
from flask_login import login_required, current_user
import requests
from app.models import EventType, Invite, Setting
from app.utils.helpers import log_event, permission_required
from app.utils.timeout_helper import get_api_timeout
from app.extensions import csrf, db
from app.models_media_services import ServiceType
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager
import time

bp = Blueprint('api', __name__)

def get_fresh_server_status():
    """Fetch fresh server status data from all servers - NO CACHING"""
    current_app.logger.info("API: get_fresh_server_status() called - fetching real-time server status")
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    current_app.logger.debug(f"API: Found {server_count} servers to check status")
    server_status_data = {}

    if server_count == 1:
        server = all_servers[0]
        current_app.logger.warning(f"API: Making API call to single server '{server.name}' ({server.service_type.value})")
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            server_status_data = service.get_server_info()
            # The service returns 'name' field with the server's actual friendly name (e.g., "Plex+")
            # Save this as 'friendly_name' for the template
            actual_server_name = server_status_data.get('name', server.name)
            server_status_data['server_id'] = server.id
            server_status_data['name'] = f"{server.service_type.value.title()} Server Status"
            server_status_data['service_type'] = server.service_type.value
            server_status_data['friendly_name'] = actual_server_name
            # Set last_check_time to current time since we just checked
            from datetime import datetime
            server_status_data['last_check_time'] = datetime.utcnow()
            current_app.logger.debug(f"API: Single server status: {server_status_data.get('online', 'unknown')}")
    elif server_count > 1:
        online_count = 0
        offline_count = 0
        all_server_statuses = []
        servers_by_service = {}
        
        current_app.logger.warning(f"API: Making API calls to {len(all_servers)} servers for status check")
        for server in all_servers:
            current_app.logger.warning(f"API: Making API call to server '{server.name}' ({server.service_type.value}) at {server.url}")
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                status = service.get_server_info()
                current_app.logger.debug(f"API: Server '{server.name}' status: {status.get('online', 'unknown')}")
                # Extract the actual server name BEFORE overriding the 'name' field
                actual_server_name = status.get('name', server.name)
                
                status['server_id'] = server.id
                status['custom_name'] = server.name  # Custom nickname from app
                status['actual_server_name'] = actual_server_name  # Actual server name from service
                status['name'] = server.name  # Override with custom name for backward compatibility
                status['service_type'] = server.service_type.value
                all_server_statuses.append(status)
                
                # Group by service type for categorized display
                service_type = server.service_type.value
                if service_type not in servers_by_service:
                    servers_by_service[service_type] = {
                        'service_name': service_type.title(),
                        'servers': [],
                        'online_count': 0,
                        'offline_count': 0,
                        'total_count': 0
                    }
                
                servers_by_service[service_type]['servers'].append(status)
                servers_by_service[service_type]['total_count'] += 1
                
                if status.get('online'):
                    online_count += 1
                    servers_by_service[service_type]['online_count'] += 1
                else:
                    offline_count += 1
                    servers_by_service[service_type]['offline_count'] += 1
                    
        server_status_data = {
            'multi_server': True,
            'online_count': online_count,
            'offline_count': offline_count,
            'all_statuses': all_server_statuses,
            'servers_by_service': servers_by_service
        }
    
    return server_status_data

# Server status cache functions removed - now using real-time data

# =============================================================================
# SYSTEM HEALTH
# =============================================================================

@bp.route('/health')
def health_check():
    """Health check endpoint for Docker HEALTHCHECK."""
    return jsonify(status="ok"), 200

# =============================================================================
# SETTINGS API
# =============================================================================

@bp.route('/settings/session-monitoring-interval')
@login_required
def get_session_monitoring_interval():
    """Get the current session monitoring interval setting"""
    # Add comprehensive logging
    raw_setting = Setting.get('SESSION_MONITORING_INTERVAL_SECONDS', 30)
    
    try:
        interval = int(raw_setting)
    except (ValueError, TypeError) as e:
        current_app.logger.warning(f"API: Failed to convert '{raw_setting}' to int: {e}, using default 30")
        interval = 30
    
    return jsonify({'interval': interval})

@bp.route('/settings/navbar-stream-badge-status')
@login_required
def get_navbar_stream_badge_status():
    """Get the current navbar stream badge setting"""
    enabled = Setting.get_bool('ENABLE_NAVBAR_STREAM_BADGE', False)
    return jsonify({'enabled': enabled})

# =============================================================================
# SERVERS API
# =============================================================================

@bp.route('/servers/<int:server_id>/status', methods=['POST'])
@login_required
@csrf.exempt
def check_server_status(server_id):
    """Check and return server status"""
    current_app.logger.debug(f"Api.py - check_server_status(): HTMX call received for server_id {server_id}. Forcing connection check.")
    
    server = MediaServiceManager.get_server_by_id(server_id)
    if not server:
        abort(404)

    service = MediaServiceFactory.create_service_from_db(server)
    if not service:
        abort(503)
    
    # Force a reconnect attempt
    service._get_server_instance(force_reconnect=True) 
    
    # Then, retrieve the status that was just updated by the call above.
    server_status_for_htmx = service.get_server_info()
    current_app.logger.debug(f"Api.py - check_server_status(): Status after forced check: {server_status_for_htmx}")
            
    # Render the partial template with the fresh status data.
    # We need to format this as a single server status for the multi_service_status template
    server_status_data = {
        'server_id': server.id,
        'service_type': server.service_type.value,
        'name': f"{server.service_type.value.title()} Server Status",
        'online': server_status_for_htmx.get('online', False),
        'friendly_name': server_status_for_htmx.get('friendly_name', 'Unknown Server'),
        'version': server_status_for_htmx.get('version'),
        'error_message': server_status_for_htmx.get('error_message'),
        'last_check_time': server_status_for_htmx.get('last_check_time'),
        'multi_server': False
    }
    return render_template('dashboard/partials/multi_service_status.html', server_status=server_status_data)

@bp.route('/dashboard/server-status', methods=['GET'])
@login_required
def get_dashboard_server_status():
    """Get server status for dashboard - loads asynchronously with real-time data"""
    current_app.logger.info("=== API ENDPOINT: /dashboard/server-status called ===")
    current_app.logger.debug("Api.py - get_dashboard_server_status(): Loading real-time server status for dashboard")
    
    # Get fresh server status data (no caching)
    server_status_data = get_fresh_server_status()
    current_app.logger.debug(f"Api.py - get_dashboard_server_status(): Fresh server status: {server_status_data}")

    return render_template('dashboard/partials/multi_service_status.html', server_status=server_status_data)

@bp.route('/dashboard/all-servers-modal', methods=['GET'])
@login_required
def get_all_servers_modal():
    """Get all servers status for modal - uses real-time data"""
    current_app.logger.info("=== API ENDPOINT: /dashboard/all-servers-modal called ===")
    current_app.logger.debug("Api.py - get_all_servers_modal(): Loading real-time server status for modal")
    
    # Get fresh server status data (no caching)
    server_status_data = get_fresh_server_status()
    current_app.logger.debug(f"Api.py - get_all_servers_modal(): Fresh server status for modal: {server_status_data}")

    return render_template('components/modals/all_servers_status_modal_content.html', server_status=server_status_data)

@bp.route('/dashboard/active-streams-count', methods=['GET'])
@login_required
def get_active_streams_count():
    """Get active streams count for dashboard - real-time data, no caching"""
    current_app.logger.info("=== API ENDPOINT: /dashboard/active-streams-count called ===")
    current_app.logger.debug("Api.py - get_active_streams_count(): Loading real-time active streams count")
    
    active_streams_count = 0
    try:
        current_app.logger.info("API: Fetching real-time active sessions from all servers")
        active_sessions_list = MediaServiceManager.get_all_active_sessions()
        if active_sessions_list:
            active_streams_count = len(active_sessions_list)
        current_app.logger.debug(f"API: Real-time active streams count: {active_streams_count}")
    except Exception as e:
        current_app.logger.error(f"API: Failed to get active streams count: {e}")
    
    # Return the card content HTML
    return f'''
    <div class="flex flex-row gap-3 items-center">
        <div class="p-3 rounded-md bg-accent/20 text-accent"><i class="fa-solid fa-tower-broadcast fa-2x"></i></div>
        <div class="flex flex-col">
            <p class="text-base-content/70">Active Streams</p>
            <h2 class="card-title text-2xl">{active_streams_count}</h2>
        </div>
    </div>
    <div class="card-actions justify-end w-full mt-2"><span class="text-xs text-accent group-hover:underline">View Streams <i class="fa-solid fa-arrow-right fa-xs ml-1"></i></span></div>
    '''

@bp.route('/servers/<int:server_id>/libraries', methods=['GET'])
@login_required
def get_server_libraries(server_id):
    """Get libraries for a specific server"""
    server = MediaServiceManager.get_server_by_id(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    
    service = MediaServiceFactory.create_service_from_db(server)
    if not service:
        return jsonify({'error': 'Service not available'}), 503
    
    try:
        libraries = service.get_libraries()
        return jsonify({'success': True, 'libraries': libraries})
    except Exception as e:
        current_app.logger.error(f"Error getting libraries for server {server_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/servers/test', methods=['POST'])
@csrf.exempt
def test_new_server():
    """Test connection to a new server"""
    # Allow during setup, but require auth after setup is complete
    if not current_user.is_authenticated:
        from app.models import AdminAccount
        try:
            admin_exists = AdminAccount.query.first() is not None
            if admin_exists:
                return jsonify({'success': False, 'message': 'Authentication required'}), 401
        except:
            pass  # Database might not be ready yet
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    try:
        service = MediaServiceFactory.create_service(data)
        if not service:
            return jsonify({'success': False, 'message': 'Unsupported service type'}), 400
        
        success, message = service.test_connection()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        current_app.logger.error(f"Error testing new server: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/servers/<int:server_id>/test', methods=['POST'])
@login_required
@csrf.exempt
def test_existing_server(server_id):
    """Test connection to an existing server"""
    result = MediaServiceManager.test_server_connection(server_id)
    return jsonify(result)

@bp.route('/servers/<int:server_id>/sync/libraries', methods=['POST'])
@login_required
@csrf.exempt
def sync_server_libraries(server_id):
    """Sync libraries for a server"""
    result = MediaServiceManager.sync_server_libraries(server_id)
    return jsonify(result)

@bp.route('/servers/<int:server_id>/sync/users', methods=['POST'])
@login_required
@csrf.exempt
def sync_server_users(server_id):
    """Sync users for a server"""
    result = MediaServiceManager.sync_server_users(server_id)
    return jsonify(result)

# =============================================================================
# MEDIA SERVICES API
# =============================================================================

@bp.route('/media/plex/images/proxy')
@login_required
def plex_image_proxy():
    """Proxy Plex images through the application"""
    image_path_on_plex = request.args.get('path')
    if not image_path_on_plex:
        current_app.logger.warning("API plex_image_proxy: 'path' parameter is missing.")
        abort(400)

    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        current_app.logger.error("API plex_image_proxy: No Plex servers found.")
        abort(503)
    
    # Use the first active Plex server
    plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
    if not plex_service:
        current_app.logger.error("API plex_image_proxy: Could not get Plex instance to proxy image.")
        abort(503)

    try:
        # Ensure the path for plex.url starts with a '/' if it's meant to be from the server root
        path_for_plexapi = image_path_on_plex
        if not path_for_plexapi.startswith('/'):
            path_for_plexapi = '/' + path_for_plexapi
        
        plex = plex_service._get_server_instance()
        full_authed_plex_image_url = plex.url(path_for_plexapi, includeToken=True)
        
        current_app.logger.debug(f"API plex_image_proxy: Corrected path for plex.url(): {path_for_plexapi}")
        current_app.logger.debug(f"API plex_image_proxy: Fetching image from Plex URL: {full_authed_plex_image_url}")

        plex_timeout = current_app.config.get('PLEX_TIMEOUT', 10)
        
        img_response = plex._session.get(full_authed_plex_image_url, stream=True, timeout=plex_timeout)
        img_response.raise_for_status()

        content_type = img_response.headers.get('Content-Type', 'image/jpeg')
        return Response(img_response.iter_content(chunk_size=1024*8), content_type=content_type)

    except requests.exceptions.HTTPError as e_http:
        current_app.logger.error(f"API plex_image_proxy: HTTPError ({e_http.response.status_code}) fetching from Plex: {e_http} for path {image_path_on_plex}")
        abort(e_http.response.status_code)
    except requests.exceptions.RequestException as e_req:
        current_app.logger.error(f"API plex_image_proxy: RequestException fetching from Plex: {e_req} for path {image_path_on_plex}")
        abort(500)
    except Exception as e:
        current_app.logger.error(f"API plex_image_proxy: Unexpected error for path {image_path_on_plex}: {e}", exc_info=True)
        abort(500)

@bp.route('/media/jellyfin/images/proxy')
@login_required
def jellyfin_image_proxy():
    """Proxy Jellyfin images through the application"""
    item_id = request.args.get('item_id')
    image_type = request.args.get('image_type', 'Primary')
    
    current_app.logger.info(f"API jellyfin_image_proxy: Received request for item_id='{item_id}', image_type='{image_type}'")
    
    if not item_id:
        current_app.logger.warning("API jellyfin_image_proxy: 'item_id' parameter is missing.")
        return "Missing item_id parameter", 400

    try:
        jellyfin_servers = MediaServiceManager.get_servers_by_type(ServiceType.JELLYFIN, active_only=True)
        
        if not jellyfin_servers:
            current_app.logger.error("API jellyfin_image_proxy: No Jellyfin servers found.")
            return "No Jellyfin servers available", 404

        jellyfin_server = jellyfin_servers[0]  # Use first available server
        current_app.logger.info(f"API jellyfin_image_proxy: Using Jellyfin server: {jellyfin_server.name} at {jellyfin_server.url}")
        
        jellyfin_service = MediaServiceFactory.create_service_from_db(jellyfin_server)
        
        if not jellyfin_service:
            current_app.logger.error("API jellyfin_image_proxy: Could not get Jellyfin instance to proxy image.")
            return "Could not connect to Jellyfin", 500

        # Construct Jellyfin image URL
        jellyfin_image_url = f"{jellyfin_server.url.rstrip('/')}/Items/{item_id}/Images/{image_type}"
        
        current_app.logger.info(f"API jellyfin_image_proxy: Fetching image from Jellyfin URL: {jellyfin_image_url}")

        # Make request with authentication headers
        headers = {
            'X-Emby-Token': jellyfin_server.api_key,
        }
        current_app.logger.info(f"API jellyfin_image_proxy: Using API key: {jellyfin_server.api_key[:8]}...")
        
        timeout = get_api_timeout()
        img_response = requests.get(jellyfin_image_url, headers=headers, stream=True, timeout=timeout)
        img_response.raise_for_status()

        content_type = img_response.headers.get('Content-Type', 'image/jpeg')
        
        return Response(img_response.content, content_type=content_type)

    except requests.exceptions.HTTPError as e_http:
        current_app.logger.error(f"API jellyfin_image_proxy: HTTPError ({e_http.response.status_code}) fetching from Jellyfin: {e_http} for item {item_id}")
        return f"HTTP error fetching image: {e_http.response.status_code}", e_http.response.status_code
    except requests.exceptions.RequestException as e_req:
        current_app.logger.error(f"API jellyfin_image_proxy: RequestException fetching from Jellyfin: {e_req} for item {item_id}")
        return "Network error fetching image", 500
    except Exception as e:
        current_app.logger.error(f"API jellyfin_image_proxy: Unexpected error for item {item_id}: {e}", exc_info=True)
        return "Error fetching image", 500

@bp.route('/media/jellyfin/users/avatar')
@login_required
def jellyfin_user_avatar_proxy():
    """Proxy Jellyfin user avatars through the application"""
    user_id = request.args.get('user_id')
    
    current_app.logger.debug(f"API jellyfin_user_avatar_proxy: Received request for user_id='{user_id}'")
    
    if not user_id:
        current_app.logger.warning("API jellyfin_user_avatar_proxy: 'user_id' parameter is missing.")
        abort(400)

    try:
        jellyfin_servers = MediaServiceManager.get_servers_by_type(ServiceType.JELLYFIN, active_only=True)
        
        if not jellyfin_servers:
            current_app.logger.error("API jellyfin_user_avatar_proxy: No Jellyfin servers found.")
            abort(404)

        jellyfin_server = jellyfin_servers[0]  # Use first available server
        current_app.logger.debug(f"API jellyfin_user_avatar_proxy: Using Jellyfin server: {jellyfin_server.name}")
        
        # First, get user data to check if PrimaryImageTag exists
        headers = {
            'X-Emby-Token': jellyfin_server.api_key,
        }
        
        # Get user info to check for PrimaryImageTag
        user_info_url = f"{jellyfin_server.url.rstrip('/')}/Users/{user_id}"
        timeout = get_api_timeout()
        user_response = requests.get(user_info_url, headers=headers, timeout=timeout)
        user_response.raise_for_status()
        user_data = user_response.json()
        
        # Check if user has a PrimaryImageTag (avatar)
        primary_image_tag = user_data.get('PrimaryImageTag')
        if not primary_image_tag:
            current_app.logger.debug(f"API jellyfin_user_avatar_proxy: User {user_id} has no PrimaryImageTag, no avatar available")
            abort(404)
        
        # Construct Jellyfin user avatar URL with tag parameter (required for Jellyfin avatars)
        avatar_url = f"{jellyfin_server.url.rstrip('/')}/Users/{user_id}/Images/Primary?tag={primary_image_tag}&width=64&quality=90"
        
        current_app.logger.debug(f"API jellyfin_user_avatar_proxy: Fetching avatar from: {avatar_url}")

        timeout = get_api_timeout()
        img_response = requests.get(avatar_url, headers=headers, stream=True, timeout=timeout)
        img_response.raise_for_status()

        content_type = img_response.headers.get('Content-Type', 'image/jpeg')
        
        return Response(img_response.content, content_type=content_type)

    except requests.exceptions.HTTPError as e_http:
        if e_http.response.status_code == 404:
            current_app.logger.debug(f"API jellyfin_user_avatar_proxy: User {user_id} avatar not found (404)")
        else:
            current_app.logger.error(f"API jellyfin_user_avatar_proxy: HTTPError ({e_http.response.status_code}) fetching avatar for user {user_id}: {e_http}")
        abort(e_http.response.status_code)
    except requests.exceptions.RequestException as e_req:
        current_app.logger.error(f"API jellyfin_user_avatar_proxy: RequestException fetching avatar for user {user_id}: {e_req}")
        abort(500)
    except Exception as e:
        current_app.logger.error(f"API jellyfin_user_avatar_proxy: Unexpected error for user {user_id}: {e}", exc_info=True)
        abort(500)

@bp.route('/media/sessions/terminate', methods=['POST'])
@login_required
@csrf.exempt
@permission_required('kill_stream')
def terminate_session():
    """Terminate a media session (Plex, Jellyfin, etc.)"""
    session_key = request.form.get('session_key')
    service_type = request.form.get('service_type')
    server_name = request.form.get('server_name')
    message = request.form.get('message', None)

    if not session_key:
        current_app.logger.error("API terminate_session: Missing 'session_key'.")
        return jsonify(success=False, error="Session key is required."), 400

    if not service_type:
        current_app.logger.error("API terminate_session: Missing 'service_type'.")
        return jsonify(success=False, error="Service type is required."), 400

    try:
        # Get the appropriate service based on service type
        if service_type.lower() == 'plex':
            servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
        elif service_type.lower() == 'jellyfin':
            servers = MediaServiceManager.get_servers_by_type(ServiceType.JELLYFIN)
        else:
            return jsonify(success=False, error=f"Unsupported service type: {service_type}"), 400
        
        if not servers:
            return jsonify(success=False, error=f"{service_type} service not found."), 500
        
        # Find the specific server by name if provided, otherwise use the first one
        target_server = None
        if server_name:
            target_server = next((s for s in servers if s.name == server_name), None)
        if not target_server:
            target_server = servers[0]  # Use first available server
        
        service = MediaServiceFactory.create_service_from_db(target_server)
        if not service:
            return jsonify(success=False, error=f"{service_type} service not available."), 500

        current_app.logger.info(f"Terminating {service_type} session {session_key} on server {target_server.name}")
        success = service.terminate_session(session_key, message)
        
        if success:
            log_event(EventType.SETTING_CHANGE, 
                     f"Terminated {service_type} session {session_key} on {target_server.name}",
                     admin_id=current_user.id if hasattr(current_user, 'id') else None)
            return jsonify(success=True, message=f"Termination command sent for {service_type} session {session_key}.")
        else:
            return jsonify(success=False, error=f"Failed to send termination command ({service_type} connection issue?)."), 500
            
    except Exception as e:
        current_app.logger.error(f"API terminate_session: Exception: {e}", exc_info=True)
        return jsonify(success=False, error=str(e)), 500

@bp.route('/media/plex/sessions/terminate', methods=['POST'])
@login_required
@csrf.exempt
@permission_required('kill_stream')
def terminate_plex_session():
    """Legacy endpoint for Plex session termination - redirects to universal endpoint"""
    # Redirect to the new universal endpoint for backward compatibility
    session_key = request.form.get('session_key')
    message = request.form.get('message', None)
    
    # Create new form data for the universal endpoint
    from werkzeug.datastructures import ImmutableMultiDict
    new_form_data = ImmutableMultiDict([
        ('session_key', session_key),
        ('service_type', 'plex'),
        ('message', message)
    ])
    
    # Replace the form data and call the universal endpoint
    request.form = new_form_data
    return terminate_session()

# =============================================================================
# STREAMING API
# =============================================================================

@bp.route('/streaming/sessions/count')
@login_required
def get_session_count():
    """Get the current count of active streaming sessions - real-time data, no caching"""
    try:
        current_app.logger.debug("API: Fetching real-time session count")
        
        # Get active sessions from all services (no caching)
        active_sessions_data = MediaServiceManager.get_all_active_sessions()
        
        # Count total sessions
        total_sessions = len(active_sessions_data)
        current_app.logger.debug(f"API: Real-time session count: {total_sessions}")
        
        return jsonify({
            'success': True,
            'count': total_sessions,
            'cached': False,
            'real_time': True
        })
    except Exception as e:
        current_app.logger.error(f"Error getting session count: {e}")
        return jsonify({
            'success': False,
            'count': 0,
            'error': str(e)
        }), 500

# =============================================================================
# INVITES API
# =============================================================================

@bp.route('/invites/guild-check', methods=['GET'])
@login_required
@csrf.exempt
def check_guild_invites():
    """Check for active, usable invites that would be affected by guild membership settings"""
    now = db.func.now()
    affected_invites = Invite.query.filter(
        Invite.is_active == True,
        (Invite.expires_at == None) | (Invite.expires_at > now),
        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses),
        Invite.force_guild_membership.is_(None)
    ).all()

    if not affected_invites:
        return jsonify(affected=False, invites=[])

    invites_data = [
        {
            "id": invite.id,
            "path": invite.custom_path or invite.token,
            "created_at": invite.created_at.isoformat()
        } for invite in affected_invites
    ]
    
    return jsonify(affected=True, invites=invites_data)

# =============================================================================
# PLUGINS API
# =============================================================================

@bp.route('/plugins/reload', methods=['POST'])
@login_required
@csrf.exempt
def reload_plugins():
    """Reload all plugins"""
    try:
        from app.services.plugin_manager import plugin_manager
        plugin_manager.reload_all_plugins()
        return jsonify({'success': True, 'message': 'Plugins reloaded successfully'})
    except Exception as e:
        current_app.logger.error(f"Error reloading plugins: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/plugins/refresh-servers-count', methods=['POST'])
@login_required
@csrf.exempt
def refresh_plugins_servers_count():
    """Refresh the servers count for all plugins"""
    try:
        from app.services.plugin_manager import plugin_manager
        plugin_manager.refresh_servers_count()
        return jsonify({'success': True, 'message': 'Plugin servers count refreshed'})
    except Exception as e:
        current_app.logger.error(f"Error refreshing plugin servers count: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# =============================================================================
# NETWORK API
# =============================================================================

@bp.route('/network/geoip/<ip_address>')
@login_required
def geoip_lookup(ip_address):
    """Look up GeoIP information for a given IP address and return HTML partial"""
    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        abort(503)
    
    # Use the first active Plex server
    plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
    if not plex_service:
        abort(503)
    geoip_data = plex_service.get_geoip_info(ip_address)
    return render_template('components/modals/geoip_modal.html', geoip_data=geoip_data, ip_address=ip_address)