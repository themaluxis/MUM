# File: app/routes/plugin_management.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, make_response
)
from flask_login import login_required, current_user
from app.models import Setting, EventType
from app.forms import PluginSettingsForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.utils.connection_tester import test_server_connection
from app.services.plugin_manager import plugin_manager
import traceback
import json

bp = Blueprint('plugin_management', __name__)

@bp.route('/')
@login_required
@setup_required
@permission_required('manage_plugins')
def index():
    # This route now just renders the main settings layout.
    # The content will be loaded via the partial included in settings/index.html
    
    # Refresh plugin servers count to ensure accuracy
    try:
        from app.models_media_services import MediaServer, ServiceType
        from app.models_plugins import Plugin
        
        plugins = Plugin.query.all()
        for plugin in plugins:
            try:
                # Find the corresponding ServiceType enum value
                service_type = None
                for st in ServiceType:
                    if st.value == plugin.plugin_id:
                        service_type = st
                        break
                
                if service_type:
                    # Count actual servers
                    actual_count = MediaServer.query.filter_by(service_type=service_type).count()
                    if plugin.servers_count != actual_count:
                        current_app.logger.debug(f"Updating plugin {plugin.plugin_id} servers_count from {plugin.servers_count} to {actual_count}")
                        plugin.servers_count = actual_count
                        db.session.add(plugin)
                else:
                    # For plugins without corresponding ServiceType, set to 0
                    if plugin.servers_count != 0:
                        current_app.logger.debug(f"Setting plugin {plugin.plugin_id} servers_count to 0 (no ServiceType)")
                        plugin.servers_count = 0
                        db.session.add(plugin)
            except Exception as e:
                current_app.logger.error(f"Error updating servers_count for plugin {plugin.plugin_id}: {e}")
        
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error refreshing plugin servers count in settings: {e}")
    
    available_plugins = plugin_manager.get_available_plugins()
    enabled_plugins = [p.plugin_id for p in plugin_manager.get_enabled_plugins()]

    return render_template(
        'settings/index.html',
        title="Plugin Manager",
        available_plugins=available_plugins,
        enabled_plugins=enabled_plugins,
        active_tab='plugins'
    )

@bp.route('/<plugin_id>')
@login_required
@setup_required
@permission_required('manage_plugins')
def configure(plugin_id):
    from app.models_plugins import Plugin
    from app.models_media_services import MediaServer, ServiceType, UserMediaAccess
    from app.services.media_service_factory import MediaServiceFactory

    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))

    servers = MediaServer.query.filter_by(service_type=service_type_enum).all()
    
    servers_with_details = []
    for server in servers:
        member_count = UserMediaAccess.query.filter_by(server_id=server.id).count()
        server_details = {
            'server': server,
            'member_count': member_count,
            'libraries': [],
            'error': None,
            'actual_server_name': None
        }
        # Use database data instead of making API calls on page load
        try:
            # Get libraries from database instead of API
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            server_details['libraries'] = [lib.name for lib in db_libraries]
            
            # Use server nickname as display name (can be updated via sync)
            server_details['actual_server_name'] = server.server_nickname
            
            # No error since we're using database data
            server_details['error'] = None
            
        except Exception as e:
            current_app.logger.error(f"Error getting server details from database for {server.server_nickname}: {e}")
            server_details['error'] = "Could not fetch server details from database."
            server_details['actual_server_name'] = server.server_nickname
            
        servers_with_details.append(server_details)

    return render_template(
        'settings/index.html',
        title=f"Configure {plugin.name}",
        plugin=plugin,
        servers_with_details=servers_with_details, # Pass new list to template
        active_tab='plugin_configure'
    )

@bp.route('/<plugin_id>/<int:server_id>/edit', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def edit_server(plugin_id, server_id):
    from app.models_plugins import Plugin
    from app.models_media_services import MediaServer, ServiceType
    from app.forms import MediaServerForm

    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))

    server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first_or_404()
    
    form = MediaServerForm(server_id=server.id, obj=server)
    form.service_type.data = server.service_type.value
    
    # Remove username/password fields for services that only use API tokens
    if plugin_id in ['plex', 'emby', 'jellyfin', 'kavita']:
        delattr(form, 'username')
        delattr(form, 'password')
    
    if form.validate_on_submit():
        try:
            # Update server
            server.server_nickname = form.name.data
            server.url = form.url.data.rstrip('/')
            server.api_key = form.api_key.data
            server.username = form.username.data
            if form.password.data:  # Only update password if provided
                server.password = form.password.data
            server.is_active = form.is_active.data
            
            db.session.commit()
            
            log_event(
                EventType.SETTING_CHANGE,
                f"Updated media server: {server.server_nickname}",
                admin_id=current_user.id
            )
            
            response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
            response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Media server "{server.server_nickname}" updated successfully!', "category": "success"}})
            return response
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating media server: {e}")
            # Error will be shown via toast on redirect
    
    # If we get here, there was an error
    response = make_response(render_template(
        'settings/index.html',
        title=f"Edit {server.server_nickname}",
        plugin=plugin,
        server=server,
        form=form,
        active_tab='plugin_edit_server'
    ))
    if 'e' in locals():
        response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Error updating server: {str(e)}', "category": "error"}})
    return response

@bp.route('/<plugin_id>/add', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def add_server(plugin_id):
    from app.models_plugins import Plugin
    from app.models_media_services import MediaServer, ServiceType
    from app.forms import MediaServerForm

    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))

    form = MediaServerForm()
    form.service_type.data = service_type_enum.value
    
    if form.validate_on_submit():
        try:
            # Create new server
            new_server = MediaServer(
                server_nickname=form.name.data,
                url=form.url.data.rstrip('/'),
                api_key=form.api_key.data,
                username=form.username.data,
                password=form.password.data,
                service_type=service_type_enum,
                is_active=form.is_active.data
            )
            
            db.session.add(new_server)
            db.session.commit()
            
            # Enable the plugin if it's not already enabled
            from app.services.plugin_manager import plugin_manager
            plugin_enabled = plugin_manager.enable_plugin(plugin_id)
            
            # Sync libraries for the new server
            from app.services.media_service_manager import MediaServiceManager
            MediaServiceManager.sync_server_libraries(new_server.id)
            
            log_event(
                EventType.SETTING_CHANGE,
                f"Added new media server: {new_server.server_nickname}",
                admin_id=current_user.id
            )
            
            response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
            response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Media server "{new_server.server_nickname}" added successfully!', "category": "success"}})
            return response
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding media server: {e}")
            # Error will be shown via toast on redirect
    
    # If we get here, there was an error
    response = make_response(render_template(
        'settings/index.html',
        title=f"Add {plugin.name} Server",
        plugin=plugin,
        form=form,
        active_tab='plugin_add_server'
    ))
    if 'e' in locals():
        response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Error adding server: {str(e)}', "category": "error"}})
    return response

@bp.route('/<plugin_id>/<int:server_id>/disable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def disable_server(plugin_id, server_id):
    """Disable a specific server"""
    from app.models_media_services import MediaServer, ServiceType
    
    # Verify plugin exists
    from app.models_plugins import Plugin
    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))
    
    server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first_or_404()
    
    try:
        server.is_active = False
        db.session.commit()
        
        toast_message = f'Server "{server.server_nickname}" disabled successfully!'
        toast_category = 'success'
        log_event(EventType.SETTING_CHANGE, f"Server '{server.server_nickname}' disabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error disabling server {server_id}: {e}")
        toast_message = f'Failed to disable server "{server.server_nickname}": {str(e)}'
        toast_category = 'error'
    
    response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
    response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": toast_message, "category": toast_category}})
    return response

@bp.route('/<plugin_id>/test-connection', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def test_connection(plugin_id):
    """Test connection for a new server configuration"""
    try:
        from flask import jsonify
        
        # Get data from JSON request
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'})
            
        url = data.get('url', '').strip()
        api_key = data.get('api_key', '').strip()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        # Validate required fields
        if not url:
            return jsonify({'success': False, 'message': 'URL is required'})
        
        # Prepare credentials based on service type
        credentials = {}
        
        if plugin_id in ['jellyfin', 'emby', 'plex', 'audiobookshelf', 'kavita']:
            # Token-based authentication only
            if not api_key:
                return jsonify({'success': False, 'message': f'API token is required for {plugin_id.title()}'})
            credentials['token'] = api_key
                
        elif plugin_id in ['romm']:
            # Username/password authentication
            if not username or not password:
                return jsonify({'success': False, 'message': f'Username and password are required for {plugin_id.title()}'})
            credentials['username'] = username
            credentials['password'] = password
            
        elif plugin_id in ['komga']:
            # API token authentication
            if not api_key:
                return jsonify({'success': False, 'message': f'API token is required for {plugin_id.title()}'})
            credentials['token'] = api_key
        
        else:
            return jsonify({'success': False, 'message': f'Unsupported service type: {plugin_id}'})
        
        # Test the connection using our new utility
        success, message = test_server_connection(plugin_id, url, **credentials)
        
        # Log the test attempt
        log_event(
            EventType.SETTING_CHANGE,
            f"Connection test for {plugin_id.title()} server: {'Success' if success else 'Failed'} - {message}",
            admin_id=current_user.id
        )
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        current_app.logger.error(f"Error testing plugin connection: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Connection test failed: {str(e)}'})

@bp.route('/<plugin_id>/<int:server_id>/test', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def test_existing_server_connection(plugin_id, server_id):
    """Test connection for an existing server"""
    try:
        from app.models_media_services import MediaServer, ServiceType
        from flask import jsonify
        
        # Convert plugin_id string to ServiceType enum
        try:
            service_type_enum = ServiceType[plugin_id.upper()]
        except KeyError:
            return jsonify({'success': False, 'message': f'Invalid service type: {plugin_id}'})
        
        # Get the existing server
        server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first()
        if not server:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        # Prepare credentials based on service type
        credentials = {}
        
        if plugin_id in ['jellyfin', 'emby', 'plex', 'audiobookshelf', 'kavita']:
            # Token-based authentication
            if not server.api_key:
                return jsonify({'success': False, 'message': f'API token not configured for this {plugin_id.title()} server'})
            credentials['token'] = server.api_key
                
        elif plugin_id in ['komga', 'romm']:
            # Username/password authentication
            if not server.username or not server.password:
                return jsonify({'success': False, 'message': f'Username and password not configured for this {plugin_id.title()} server'})
            credentials['username'] = server.username
            credentials['password'] = server.password
        
        else:
            return jsonify({'success': False, 'message': f'Unsupported service type: {plugin_id}'})
        
        # Test the connection using our new utility
        success, message = test_server_connection(plugin_id, server.url, **credentials)
        
        # Log the test attempt
        log_event(
            EventType.SETTING_CHANGE,
            f"Connection test for existing {plugin_id.title()} server '{server.server_nickname}': {'Success' if success else 'Failed'} - {message}",
            admin_id=current_user.id
        )
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        current_app.logger.error(f"Error testing existing server connection: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Connection test failed: {str(e)}'})

@bp.route('/<plugin_id>/<int:server_id>/raw-info', methods=['GET'])
@login_required
@setup_required
@permission_required('manage_plugins')
def get_raw_server_info(plugin_id, server_id):
    """Get raw server information (System/Info) for debugging"""
    try:
        from app.services.media_service_factory import MediaServiceFactory
        from app.models_media_services import MediaServer, ServiceType
        from flask import jsonify
        
        # Convert plugin_id string to ServiceType enum
        try:
            service_type_enum = ServiceType[plugin_id.upper()]
        except KeyError:
            return jsonify({'success': False, 'message': f'Invalid service type: {plugin_id}'})
        
        # Get the existing server
        server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first()
        if not server:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        # Create service instance and get raw info
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return jsonify({'success': False, 'message': f'Failed to create service for {plugin_id}'})
        
        # Get raw system info - this will vary by service type
        current_app.logger.debug(f"Getting raw info for plugin_id: '{plugin_id}' (type: {type(plugin_id)})")
        current_app.logger.debug(f"Service type: {type(service).__name__}")
        
        if plugin_id.lower() == 'plex':
            # For Plex, get comprehensive server information using PlexServer attributes
            current_app.logger.debug("Processing Plex server raw info")
            server_instance = service._get_server_instance()
            if server_instance:
                raw_info = {
                    # Basic Server Info
                    'friendlyName': getattr(server_instance, 'friendlyName', 'Unknown'),
                    'machineIdentifier': getattr(server_instance, 'machineIdentifier', 'Unknown'),
                    'version': getattr(server_instance, 'version', 'Unknown'),
                    'platform': getattr(server_instance, 'platform', 'Unknown'),
                    'platformVersion': getattr(server_instance, 'platformVersion', 'Unknown'),
                    'product': getattr(server_instance, 'product', 'Unknown'),
                    'productVersion': getattr(server_instance, 'productVersion', 'Unknown'),
                    
                    # Network & Connection
                    'baseurl': getattr(server_instance, 'baseurl', 'Unknown'),
                    'token': getattr(server_instance, 'token', 'Unknown')[:20] + '...' if getattr(server_instance, 'token', None) else 'None',
                    'isLocal': getattr(server_instance, 'isLocal', 'Unknown'),
                    'isSecure': getattr(server_instance, 'isSecure', 'Unknown'),
                    
                    # MyPlex Integration
                    'myPlex': getattr(server_instance, 'myPlex', 'Unknown'),
                    'myPlexSigninState': getattr(server_instance, 'myPlexSigninState', 'Unknown'),
                    'myPlexSubscription': getattr(server_instance, 'myPlexSubscription', 'Unknown'),
                    'myPlexUsername': getattr(server_instance, 'myPlexUsername', 'Unknown'),
                    
                    # Server Capabilities
                    'allowCameraUpload': getattr(server_instance, 'allowCameraUpload', 'Unknown'),
                    'allowChannelAccess': getattr(server_instance, 'allowChannelAccess', 'Unknown'),
                    'allowMediaDeletion': getattr(server_instance, 'allowMediaDeletion', 'Unknown'),
                    'allowSharing': getattr(server_instance, 'allowSharing', 'Unknown'),
                    'allowSync': getattr(server_instance, 'allowSync', 'Unknown'),
                    'allowTuners': getattr(server_instance, 'allowTuners', 'Unknown'),
                    
                    # Media & Libraries
                    'backgroundProcessing': getattr(server_instance, 'backgroundProcessing', 'Unknown'),
                    'certificate': getattr(server_instance, 'certificate', 'Unknown'),
                    'companionProxy': getattr(server_instance, 'companionProxy', 'Unknown'),
                    'diagnostics': getattr(server_instance, 'diagnostics', 'Unknown'),
                    'eventStream': getattr(server_instance, 'eventStream', 'Unknown'),
                    
                    # Timestamps
                    'createdAt': str(getattr(server_instance, 'createdAt', 'Unknown')),
                    'updatedAt': str(getattr(server_instance, 'updatedAt', 'Unknown')),
                    
                    # Hardware & Performance
                    'multiuser': getattr(server_instance, 'multiuser', 'Unknown'),
                    'ownerFeatures': getattr(server_instance, 'ownerFeatures', 'Unknown'),
                    'photoAutoTag': getattr(server_instance, 'photoAutoTag', 'Unknown'),
                    'pushNotifications': getattr(server_instance, 'pushNotifications', 'Unknown'),
                    'readOnlyLibraries': getattr(server_instance, 'readOnlyLibraries', 'Unknown'),
                    'requestParametersInCookie': getattr(server_instance, 'requestParametersInCookie', 'Unknown'),
                    'streamingBrainABRVersion': getattr(server_instance, 'streamingBrainABRVersion', 'Unknown'),
                    'streamingBrainVersion': getattr(server_instance, 'streamingBrainVersion', 'Unknown'),
                    'sync': getattr(server_instance, 'sync', 'Unknown'),
                    'transcoderActiveVideoSessions': getattr(server_instance, 'transcoderActiveVideoSessions', 'Unknown'),
                    'transcoderAudio': getattr(server_instance, 'transcoderAudio', 'Unknown'),
                    'transcoderLyrics': getattr(server_instance, 'transcoderLyrics', 'Unknown'),
                    'transcoderPhoto': getattr(server_instance, 'transcoderPhoto', 'Unknown'),
                    'transcoderSubtitles': getattr(server_instance, 'transcoderSubtitles', 'Unknown'),
                    'transcoderVideo': getattr(server_instance, 'transcoderVideo', 'Unknown'),
                    'transcoderVideoBitrates': getattr(server_instance, 'transcoderVideoBitrates', 'Unknown'),
                    'transcoderVideoQualities': getattr(server_instance, 'transcoderVideoQualities', 'Unknown'),
                    'transcoderVideoResolutions': getattr(server_instance, 'transcoderVideoResolutions', 'Unknown'),
                    'voiceSearch': getattr(server_instance, 'voiceSearch', 'Unknown'),
                }
            else:
                raw_info = {'error': 'Could not connect to Plex server'}
        elif plugin_id.lower() == 'jellyfin':
            # For Jellyfin, use the System/Info API
            if hasattr(service, '_make_request'):
                raw_info = service._make_request('System/Info')
            else:
                raw_info = {'error': 'Jellyfin service does not support API requests'}
        elif plugin_id.lower() == 'kavita':
            # For Kavita, use the Server/server-info-slim API
            if hasattr(service, '_make_request'):
                try:
                    raw_info = service._make_request('Server/server-info-slim')
                except Exception as e:
                    raw_info = {'error': f'Could not fetch Kavita server info: {str(e)}'}
            else:
                raw_info = {'error': 'Kavita service does not support API requests'}
        else:
            # For other services, try to get some basic info
            raw_info = {'message': f'Raw info not implemented for {plugin_id}'}
        
        return jsonify({'success': True, 'info': raw_info})
        
    except Exception as e:
        current_app.logger.error(f"Error getting raw server info: {e}")
        return jsonify({'success': False, 'message': f'Failed to get server info: {str(e)}'})

@bp.route('/<plugin_id>/<int:server_id>/enable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def enable_server(plugin_id, server_id):
    """Enable a specific server"""
    from app.models_media_services import MediaServer, ServiceType
    
    # Verify plugin exists
    from app.models_plugins import Plugin
    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))
    
    server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first_or_404()
    
    try:
        server.is_active = True
        db.session.commit()
        
        flash(f'Server "{server.server_nickname}" enabled successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Server '{server.server_nickname}' enabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error enabling server {server_id}: {e}")
        flash(f'Failed to enable server "{server.server_nickname}": {str(e)}', 'danger')
    
    return redirect(url_for('plugin_management.configure', plugin_id=plugin_id))

@bp.route('/<plugin_id>/<int:server_id>/delete', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def delete_server(plugin_id, server_id):
    """Delete a specific server"""
    from app.models_media_services import MediaServer, ServiceType
    
    # Verify plugin exists
    from app.models_plugins import Plugin
    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    
    # Convert plugin_id string to ServiceType enum
    try:
        service_type_enum = ServiceType[plugin_id.upper()]
    except KeyError:
        flash(f"Invalid service type: {plugin_id}", "danger")
        return redirect(url_for('plugin_management.index'))
    
    server = MediaServer.query.filter_by(id=server_id, service_type=service_type_enum).first_or_404()
    server_name = server.server_nickname  # Store name before deletion
    
    try:
        db.session.delete(server)
        db.session.commit()
        
        flash(f'Server "{server_name}" deleted successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Server '{server_name}' deleted", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting server {server_id}: {e}")
        flash(f'Failed to delete server "{server_name}": {str(e)}', 'danger')
    
    return redirect(url_for('plugin_management.configure', plugin_id=plugin_id))

