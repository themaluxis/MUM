# File: app/routes/media_servers.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.models_media_services import MediaServer, ServiceType
from app.models_plugins import Plugin
from app.forms import MediaServerForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.extensions import csrf
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
from app.models import EventType

bp = Blueprint('media_servers', __name__)


@bp.route('/setup/plugins/<string:plugin_id>/servers', methods=['GET'])
@login_required
def setup_list_servers(plugin_id):
    """List servers for a specific plugin during setup."""
    from app.routes.setup import get_completed_steps
    
    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first_or_404()
    servers = [s for s in MediaServiceManager.get_all_servers(active_only=False) if s.service_type.value == plugin_id]
    
    return render_template(
        'setup/servers.html',
        servers=servers,
        plugin=plugin,
        completed_steps=get_completed_steps(),
        current_step_id='plugins'
    )


@bp.route('/setup/plugins/<string:plugin_id>/servers/add', methods=['GET', 'POST'])
@login_required
def add_server_setup(plugin_id):
    """Add a new media server during the setup flow."""
    from app.routes.setup import get_completed_steps

    form = MediaServerForm()
    if request.method == 'GET':
        form.service_type.data = plugin_id

    if form.validate_on_submit():
        try:
            # Test connection before saving
            server_config = { 'name': form.name.data, 'service_type': form.service_type.data, 'url': form.url.data.rstrip('/'), 'api_key': form.api_key.data, 'username': form.username.data, 'password': form.password.data }
            service = MediaServiceFactory.create_service(server_config)
            if not service:
                flash('Unsupported service type', 'danger')
                return render_template('setup/add_server.html', form=form, completed_steps=get_completed_steps(), current_step_id='plugins')

            success, message = service.test_connection()
            if not success:
                flash(f'Connection test failed: {message}', 'danger')
                return render_template('setup/add_server.html', form=form, completed_steps=get_completed_steps(), current_step_id='plugins')

            # Save to database
            server = MediaServer(name=form.name.data, service_type=ServiceType(form.service_type.data), url=form.url.data.rstrip('/'), api_key=form.api_key.data, username=form.username.data, password=form.password.data, is_active=form.is_active.data)
            db.session.add(server)
            db.session.commit()
            
            # Auto-enable the plugin when a server is configured during setup
            try:
                from app.services.plugin_manager import plugin_manager
                plugin_enabled = plugin_manager.enable_plugin(plugin_id)
                if plugin_enabled:
                    current_app.logger.info(f"Auto-enabled plugin '{plugin_id}' after adding server during setup")
                    flash(f'Plugin "{plugin_id}" has been automatically enabled!', 'info')
                else:
                    current_app.logger.warning(f"Failed to auto-enable plugin '{plugin_id}' after adding server during setup")
            except Exception as e:
                current_app.logger.error(f"Error auto-enabling plugin '{plugin_id}' during setup: {e}")
            
            log_event(EventType.SETTING_CHANGE, f"Added new media server: {server.name}", admin_id=current_user.id)
            flash(f'Media server "{server.name}" added successfully!', 'success')
            
            MediaServiceManager.sync_server_libraries(server.id)
            
            return redirect(url_for('media_servers.setup_list_servers', plugin_id=plugin_id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding media server during setup: {e}")
            flash(f'Error adding server: {str(e)}', 'danger')
    else:
        if request.method == 'POST':
            current_app.logger.warning(f"Form validation failed. Errors: {form.errors}")

    return render_template('setup/add_server.html', form=form, completed_steps=get_completed_steps(), current_step_id='plugins')

@bp.route('/servers/add', methods=['GET', 'POST'])
@login_required
def add_server():
    """Add a new media server from the main settings page."""
    form = MediaServerForm()
    service_type_from_url = request.args.get('service_type')
    if service_type_from_url and request.method == 'GET':
        form.service_type.data = service_type_from_url

    if form.validate_on_submit():
        try:
            # Test connection before saving
            server_config = { 'name': form.name.data, 'service_type': form.service_type.data, 'url': form.url.data.rstrip('/'), 'api_key': form.api_key.data, 'username': form.username.data, 'password': form.password.data }
            service = MediaServiceFactory.create_service(server_config)
            if not service:
                flash('Unsupported service type', 'danger')
                return render_template('media_servers/add.html', form=form)

            success, message = service.test_connection()
            if not success:
                flash(f'Connection test failed: {message}', 'danger')
                return render_template('media_servers/add.html', form=form)

            # Save to database
            server = MediaServer(name=form.name.data, service_type=ServiceType(form.service_type.data), url=form.url.data.rstrip('/'), api_key=form.api_key.data, username=form.username.data, password=form.password.data, is_active=form.is_active.data)
            db.session.add(server)
            db.session.commit()
            
            # Auto-enable the plugin when a server is configured (for non-setup flow too)
            try:
                from app.services.plugin_manager import plugin_manager
                plugin_enabled = plugin_manager.enable_plugin(server.service_type.value)
                if plugin_enabled:
                    current_app.logger.info(f"Auto-enabled plugin '{server.service_type.value}' after adding server")
                else:
                    current_app.logger.warning(f"Failed to auto-enable plugin '{server.service_type.value}' after adding server")
            except Exception as e:
                current_app.logger.error(f"Error auto-enabling plugin '{server.service_type.value}': {e}")
            
            log_event(EventType.SETTING_CHANGE, f"Added new media server: {server.name}", admin_id=current_user.id)
            flash(f'Media server "{server.name}" added successfully!', 'success')
            
            MediaServiceManager.sync_server_libraries(server.id)
            
            return redirect(url_for('plugin_management.configure', plugin_id=server.service_type.value))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding media server: {e}")
            flash(f'Error adding server: {str(e)}', 'danger')

    return render_template('media_servers/add.html', form=form)

@bp.route('/servers/<int:server_id>/edit', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_media_servers')
def edit_server(server_id):
    """Edit an existing media server"""
    server = MediaServiceManager.get_server_by_id(server_id)
    if not server:
        flash('Server not found', 'danger')
        return redirect(url_for('plugin_management.index'))
    
    form = MediaServerForm(server_id=server.id, obj=server)
    form.service_type.data = server.service_type.value
    
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
            
            flash(f'Media server "{server.name}" updated successfully!', 'success')
            # Redirect to the appropriate plugin settings page based on service type
            if server.service_type.value == 'plex':
                return redirect(url_for('plugin_management.configure', plugin_id='plex'))
            else:
                return redirect(url_for('plugin_management.configure', plugin_id=server.service_type.value))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating media server: {e}")
            flash(f'Error updating server: {str(e)}', 'danger')
    
    return render_template('media_servers/edit.html', form=form, server=server)

@bp.route('/setup/plugins/<string:plugin_id>/servers/<int:server_id>/edit', methods=['GET', 'POST'])
@login_required
def setup_edit_server(plugin_id, server_id):
    """Edit a media server during the setup flow."""
    from app.routes.setup import get_completed_steps
    current_app.logger.debug(f"Entering setup_edit_server for server_id: {server_id} with method: {request.method}")
    
    server = MediaServiceManager.get_server_by_id(server_id)
    if not server:
        flash('Server not found', 'danger')
        return redirect(url_for('media_servers.setup_list_servers', plugin_id=plugin_id))

    form = MediaServerForm(server_id=server.id, obj=server)
    
    if request.method == 'POST':
        # Manually set service_type as it's disabled in the form
        form.service_type.data = server.service_type.value
        current_app.logger.debug("Attempting to validate form on POST.")
        if form.validate_on_submit():
            current_app.logger.debug("Form validation successful.")
            try:
                server.server_nickname = form.name.data
                server.url = form.url.data.rstrip('/')
                server.api_key = form.api_key.data
                server.username = form.username.data
                if form.password.data:
                    server.password = form.password.data
                server.is_active = form.is_active.data
                db.session.commit()
                flash(f'Media server "{server.name}" updated successfully!', 'success')
                current_app.logger.debug("Server updated and committed. Redirecting.")
                return redirect(url_for('media_servers.setup_list_servers', plugin_id=plugin_id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating media server during setup: {e}")
                flash(f'Error updating server: {str(e)}', 'danger')
        else:
            current_app.logger.warning(f"Form validation failed. Errors: {form.errors}")

    return render_template('setup/edit_server.html', form=form, server=server, completed_steps=get_completed_steps(), current_step_id='plugins')

@bp.route('/servers/<int:server_id>/delete', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_media_servers')
def delete_server(server_id):
    """Delete a media server"""
    server = MediaServiceManager.get_server_by_id(server_id)
    if not server:
        flash('Server not found', 'danger')
        return redirect(url_for('plugin_management.index'))
    
    try:
        server_name = server.server_nickname
        service_type = server.service_type
        db.session.delete(server)
        db.session.commit()
        
        # Manually update plugin servers count to ensure it's current
        try:
            from app.models_plugins import Plugin
            plugin = Plugin.query.filter_by(plugin_id=service_type.value).first()
            if plugin:
                count = MediaServer.query.filter_by(service_type=service_type).count()
                plugin.servers_count = count
                db.session.add(plugin)
                db.session.commit()
                current_app.logger.debug(f"Updated plugin {plugin.plugin_id} servers_count to {count} after deletion")
        except Exception as e:
            current_app.logger.error(f"Error updating plugin servers count after deletion: {e}")
        
        # Plugin servers count is automatically updated by SQLAlchemy event listeners
        
        log_event(
            EventType.SETTING_CHANGE,
            f"Deleted media server: {server_name}",
            admin_id=current_user.id
        )
        
        flash(f'Media server "{server_name}" deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting media server: {e}")
        flash(f'Error deleting server: {str(e)}', 'danger')
    
    # Check if we came from plugin configuration and redirect back there
    return_to = request.form.get('return_to') or request.args.get('return_to')
    if return_to == 'setup_plugin_config':
        plugin_id = request.form.get('plugin_id') or request.args.get('plugin_id')
        if plugin_id:
            return redirect(url_for('media_servers.setup_list_servers', plugin_id=plugin_id))
    if return_to == 'plugin_config':
        plugin_id = request.form.get('plugin_id') or request.args.get('plugin_id')
        if plugin_id:
            return redirect(url_for('plugin_management.configure', plugin_id=plugin_id))
    
    # Fallback: check referer
    referer = request.headers.get('Referer', '')
    if 'settings/plugins/' in referer:
        # Extract plugin_id from the referer URL
        import re
        plugin_match = re.search(r'/settings/plugins/([^/]+)', referer)
        if plugin_match:
            plugin_id = plugin_match.group(1)
            return redirect(url_for('plugin_management.configure', plugin_id=plugin_id))
    
    return redirect(url_for('plugin_management.index'))






@bp.route('/servers/<int:server_id>/enable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def enable_server(server_id):
    """Enable a specific server"""
    server = MediaServer.query.get_or_404(server_id)
    
    try:
        server.is_active = True
        db.session.commit()
        
        flash(f'Server "{server.server_nickname}" enabled successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Server '{server.server_nickname}' enabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error enabling server {server_id}: {e}")
        flash(f'Failed to enable server "{server.server_nickname}": {str(e)}', 'danger')
    
    # Redirect back to plugin configuration page
    return redirect(url_for('plugin_management.configure', plugin_id=server.service_type.value))

@bp.route('/servers/<int:server_id>/disable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def disable_server(server_id):
    """Disable a specific server"""
    server = MediaServer.query.get_or_404(server_id)
    
    try:
        server.is_active = False
        db.session.commit()
        
        flash(f'Server "{server.server_nickname}" disabled successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Server '{server.server_nickname}' disabled", admin_id=current_user.id)
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error disabling server {server_id}: {e}")
        flash(f'Failed to disable server "{server.server_nickname}": {str(e)}', 'danger')
    
    # Redirect back to plugin configuration page
    return redirect(url_for('plugin_management.configure', plugin_id=server.service_type.value))