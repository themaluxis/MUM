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
            'error': None
        }
        try:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                # Get libraries
                try:
                    libs = service.get_libraries()
                    server_details['libraries'] = [lib.get('name', 'Unknown Library') for lib in libs] if libs else []
                except Exception as e:
                    current_app.logger.error(f"Error getting libraries for server {server.name}: {e}")
                    server_details['error'] = "Could not fetch libraries."
            else:
                server_details['error'] = "Could not create media service."
        except Exception as e:
            current_app.logger.error(f"Error creating service for server {server.name}: {e}\n{traceback.format_exc()}")
            server_details['error'] = "Failed to connect to server."
            
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
    
    if form.validate_on_submit():
        try:
            # Update server
            server.name = form.name.data
            server.url = form.url.data.rstrip('/')
            server.api_key = form.api_key.data
            server.username = form.username.data
            if form.password.data:  # Only update password if provided
                server.password = form.password.data
            server.is_active = form.is_active.data
            
            db.session.commit()
            
            log_event(
                EventType.SETTING_CHANGE,
                f"Updated media server: {server.name}",
                admin_id=current_user.id
            )
            
            response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
            response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Media server "{server.name}" updated successfully!', "category": "success"}})
            return response
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating media server: {e}")
            # Error will be shown via toast on redirect
    
    # If we get here, there was an error
    response = make_response(render_template(
        'settings/index.html',
        title=f"Edit {server.name}",
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
                name=form.name.data,
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
                f"Added new media server: {new_server.name}",
                admin_id=current_user.id
            )
            
            response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
            response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": f'Media server "{new_server.name}" added successfully!', "category": "success"}})
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
        
        toast_message = f'Server "{server.name}" disabled successfully!'
        toast_category = 'success'
        log_event(EventType.SETTING_CHANGE, f"Server '{server.name}' disabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error disabling server {server_id}: {e}")
        toast_message = f'Failed to disable server "{server.name}": {str(e)}'
        toast_category = 'error'
    
    response = make_response(redirect(url_for('plugin_management.configure', plugin_id=plugin_id)))
    response.headers['HX-Trigger'] = json.dumps({"showToastEvent": {"message": toast_message, "category": toast_category}})
    return response

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
        
        flash(f'Server "{server.name}" enabled successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Server '{server.name}' enabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error enabling server {server_id}: {e}")
        flash(f'Failed to enable server "{server.name}": {str(e)}', 'danger')
    
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
    server_name = server.name  # Store name before deletion
    
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