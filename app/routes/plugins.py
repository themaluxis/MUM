# File: app/routes/plugins.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, make_response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
from app.models_plugins import Plugin, PluginStatus, PluginType
from app.services.plugin_manager import plugin_manager
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.models import EventType
import json
from app.routes.setup import get_completed_steps

bp = Blueprint('plugins', __name__)

# Removed list_plugins route - functionality moved to dashboard.settings_plugins

@bp.route('/plugins/<plugin_id>/enable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def enable_plugin(plugin_id):
    """Enable a plugin"""
    success = plugin_manager.enable_plugin(plugin_id)
    
    if success:
        # Ensure database changes are committed and refresh session for immediate visibility
        db.session.commit()
        db.session.close()  # Force fresh data on next query
        
        # Check if plugin has servers that were auto-enabled
        plugin = Plugin.query.filter_by(plugin_id=plugin_id).first()
        if plugin and plugin.servers_count > 0:
            flash(f'Plugin "{plugin_id}" enabled successfully! {plugin.servers_count} associated server(s) have been activated.', 'success')
            log_event(EventType.SETTING_CHANGE, f"Plugin '{plugin_id}' enabled and {plugin.servers_count} servers activated", admin_id=current_user.id)
        else:
            flash(f'Plugin "{plugin_id}" enabled successfully!', 'success')
            log_event(EventType.SETTING_CHANGE, f"Plugin '{plugin_id}' enabled", admin_id=current_user.id)
    else:
        plugin = Plugin.query.filter_by(plugin_id=plugin_id).first()
        error_msg = plugin.last_error if plugin else "Plugin not found"
        flash(f'Failed to enable plugin "{plugin_id}": {error_msg}', 'danger')
    
    # Check if this is an HTMX request
    if request.headers.get('HX-Request'):
        # For HTMX requests, trigger a page refresh to update the navbar
        response = make_response("", 200)
        response.headers['HX-Refresh'] = 'true'
        return response
    else:
        # Check if we're coming from settings page
        if request.referrer and 'settings/plugins' in request.referrer:
            return redirect(url_for('dashboard.settings_plugins'))
        else:
            return redirect(url_for('plugins.list_plugins'))

@bp.route('/plugins/<plugin_id>/disable', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def disable_plugin(plugin_id):
    """Disable a plugin"""
    success = plugin_manager.disable_plugin(plugin_id)
    
    if success:
        # Check if this was the last enabled plugin
        remaining_enabled = Plugin.query.filter_by(status=PluginStatus.ENABLED).count()
        if remaining_enabled == 0:
            flash(f'Plugin "{plugin_id}" disabled successfully! Warning: No plugins are now enabled. You must enable at least one plugin before leaving this page.', 'warning')
        else:
            flash(f'Plugin "{plugin_id}" disabled successfully! Associated servers have been deactivated.', 'success')
        log_event(EventType.SETTING_CHANGE, f"Plugin '{plugin_id}' disabled and servers deactivated", admin_id=current_user.id)
    else:
        plugin = Plugin.query.filter_by(plugin_id=plugin_id).first()
        error_msg = plugin.last_error if plugin else "Plugin not found"
        flash(f'Failed to disable plugin "{plugin_id}": {error_msg}', 'danger')
    
    # Check if this is an HTMX request
    if request.headers.get('HX-Request'):
        # For HTMX requests, trigger a page refresh to update the navbar
        response = make_response("", 200)
        response.headers['HX-Refresh'] = 'true'
        return response
    else:
        # Check if we're coming from settings page
        if request.referrer and 'settings/plugins' in request.referrer:
            return redirect(url_for('dashboard.settings_plugins'))
        else:
            return redirect(url_for('plugins.list_plugins'))

@bp.route('/plugins/<plugin_id>/info')
@login_required
@setup_required
@permission_required('manage_plugins')
def plugin_info(plugin_id):
    """Get detailed plugin information"""
    plugin_info = plugin_manager.get_plugin_info(plugin_id)
    
    if not plugin_info:
        return jsonify({'error': 'Plugin not found'}), 404
    
    return jsonify(plugin_info)

@bp.route('/plugins/install', methods=['GET', 'POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def install_plugin():
    """Install a plugin from file upload"""
    if request.method == 'POST':
        if 'plugin_file' not in request.files:
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        file = request.files['plugin_file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        if file and file.filename.endswith(('.zip', '.tar.gz')):
            filename = secure_filename(file.filename)
            upload_path = os.path.join(current_app.instance_path, 'temp', filename)
            
            # Ensure temp directory exists
            os.makedirs(os.path.dirname(upload_path), exist_ok=True)
            
            try:
                file.save(upload_path)
                
                success = plugin_manager.install_plugin_from_file(upload_path)
                
                if success:
                    flash('Plugin installed successfully!', 'success')
                    log_event(EventType.SETTING_CHANGE, f"Plugin installed from {filename}", admin_id=current_user.id)
                else:
                    flash('Failed to install plugin. Check logs for details.', 'danger')
                
                # Clean up temp file
                if os.path.exists(upload_path):
                    os.remove(upload_path)
                
            except Exception as e:
                flash(f'Error installing plugin: {str(e)}', 'danger')
                current_app.logger.error(f"Plugin installation error: {e}")
        else:
            flash('Invalid file format. Please upload a .zip or .tar.gz file.', 'danger')
        
        return redirect(url_for('dashboard.settings_plugins'))
    
    return render_template('settings/index.html', 
                         title="Install Plugin",
                         active_tab='plugin_install')

@bp.route('/plugins/<plugin_id>/uninstall', methods=['POST'])
@login_required
@setup_required
@permission_required('manage_plugins')
def uninstall_plugin(plugin_id):
    """Uninstall a plugin"""
    plugin = Plugin.query.filter_by(plugin_id=plugin_id).first()
    
    if not plugin:
        flash('Plugin not found', 'danger')
        return redirect(url_for('dashboard.settings_plugins'))
    
    if plugin.plugin_type == PluginType.CORE:
        flash('Cannot uninstall core plugins', 'danger')
        return redirect(url_for('dashboard.settings_plugins'))
    
    if not plugin.can_be_disabled():
        flash('Cannot uninstall plugin while it has active servers', 'danger')
        return redirect(url_for('dashboard.settings_plugins'))
    
    success = plugin_manager.uninstall_plugin(plugin_id)
    
    if success:
        flash(f'Plugin "{plugin_id}" uninstalled successfully!', 'success')
        log_event(EventType.SETTING_CHANGE, f"Plugin '{plugin_id}' uninstalled", admin_id=current_user.id)
    else:
        flash(f'Failed to uninstall plugin "{plugin_id}"', 'danger')
    
    # Check if we're coming from settings page
    if request.referrer and 'settings/plugins' in request.referrer:
        return redirect(url_for('dashboard.settings_plugins'))
    else:
        return redirect(url_for('dashboard.settings_plugins'))



@bp.route('/api/plugins/reload')
@login_required
@setup_required
@permission_required('manage_plugins')
def reload_plugins():
    """Reload all enabled plugins"""
    try:
        plugin_manager.load_all_enabled_plugins()
        return jsonify({'success': True, 'message': 'Plugins reloaded successfully'})
    except Exception as e:
        current_app.logger.error(f"Error reloading plugins: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500