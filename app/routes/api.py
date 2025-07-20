# File: app/routes/api.py
from flask import Blueprint, request, current_app, render_template, Response, abort, jsonify
from flask_login import login_required, current_user
import requests
from app.models import EventType, Invite 
from app.utils.helpers import log_event, permission_required
from app.extensions import csrf, db
from app.models_media_services import ServiceType
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager

bp = Blueprint('api', __name__)




@bp.route('/health')
def health_check():
    """
    Health check endpoint for Docker HEALTHCHECK.
    """
    return jsonify(status="ok"), 200

@bp.route('/check_server_status/<int:server_id>', methods=['POST'])
@login_required
@csrf.exempt # Assuming CSRF is handled or exempted appropriately for this API endpoint
def check_server_status(server_id):
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
    # This is still plex-specific, but can be adapted later
    return render_template('dashboard/partials/plex_status_card.html', plex_server_status=server_status_for_htmx)

@bp.route('/plex_image_proxy')
@login_required
def plex_image_proxy():
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
            # This check assumes that paths like "library/metadata/..." should be "/library/metadata/..."
            # If plexapi could also receive paths like "some/other/endpoint" that don't start with /,
            # this logic might need adjustment. For thumbs, "/library/..." is standard.
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
        current_app.logger.error(f"API plex_image_proxy: HTTPError ({e_http.response.status_code}) fetching from Plex: {e_http} for path {image_path_on_plex} (URL: {full_authed_plex_image_url})")
        abort(e_http.response.status_code)
    except requests.exceptions.RequestException as e_req:
        current_app.logger.error(f"API plex_image_proxy: RequestException fetching from Plex: {e_req} for path {image_path_on_plex} (URL: {full_authed_plex_image_url})")
        abort(500) # Or a more specific error if RequestException implies client-side vs server-side issue with the request construction
    except Exception as e:
        current_app.logger.error(f"API plex_image_proxy: Unexpected error for path {image_path_on_plex}: {e}", exc_info=True)
        abort(500)

@bp.route('/terminate_plex_session', methods=['POST'])
@login_required
@csrf.exempt # Or ensure your JS sends CSRF token for POST via HTMX
@permission_required('kill_stream')
def terminate_plex_session_route():
    session_key = request.form.get('session_key')
    message = request.form.get('message', None) # Optional message

    if not session_key:
        current_app.logger.error("API terminate_plex_session: Missing 'session_key'.")
        return jsonify(success=False, error="Session key is required."), 400

    try:
        plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
        if not plex_servers:
            return jsonify(success=False, error="Plex service not found."), 500
        
        # Use the first active Plex server
        plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
        if not plex_service:
            return jsonify(success=False, error="Plex service not found."), 500

        success = plex_service.terminate_session(session_key, message)
        if success:
            # The session might take a moment to disappear from Plex's /status/sessions.
            # The client-side HTMX should trigger a refresh of the sessions list.
            return jsonify(success=True, message=f"Termination command sent for session {session_key}.")
        else:
            # This case might occur if plex_service.get_plex_instance() failed initially
            return jsonify(success=False, error="Failed to send termination command (Plex connection issue?)."), 500
    except Exception as e:
        current_app.logger.error(f"API terminate_plex_session: Exception: {e}", exc_info=True)
        # Provide the error message from the service layer if available
        return jsonify(success=False, error=str(e)), 500
    
@bp.route('/check_guild_invites', methods=['GET'])
@login_required
@csrf.exempt
def check_guild_invites():
    """
    Checks for active, usable invites that don't have a specific override
    to disable guild membership checking. These are the invites that would be
    affected if the global 'Require Guild Membership' setting is turned off.
    """
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

@bp.route('/geoip_lookup/<ip_address>')
@login_required
def geoip_lookup(ip_address):
    """
    Looks up GeoIP information for a given IP address and returns an HTML partial.
    """
    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        abort(503)
    
    # Use the first active Plex server
    plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
    if not plex_service:
        abort(503)
    geoip_data = plex_service.get_geoip_info(ip_address)
    return render_template('components/modals/geoip_modal.html', geoip_data=geoip_data, ip_address=ip_address)