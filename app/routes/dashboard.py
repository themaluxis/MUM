from flask import Blueprint, render_template, current_app
from flask_login import login_required
from app.models import User, Invite, HistoryLog
from app.extensions import db
from app.utils.helpers import setup_required
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
@setup_required 
def index():
    total_users = User.query.count()
    active_invites_count = Invite.query.filter(
        Invite.is_active == True,
        (Invite.expires_at == None) | (Invite.expires_at > db.func.now()), # Use db.func.now() for DB comparison
        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses)
    ).count()

    # Get active streams count
    active_streams_count = 0
    try:
        active_sessions_list = MediaServiceManager.get_all_active_sessions() # This returns a list
        if active_sessions_list:
            active_streams_count = len(active_sessions_list)
    except Exception as e:
        current_app.logger.error(f"Dashboard: Failed to get active streams count: {e}")

    # Server Status Card Logic
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    server_status_data = {}

    if server_count == 1:
        server = all_servers[0]
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            server_status_data = service.get_server_info()
            server_status_data['server_id'] = server.id
            server_status_data['name'] = server.name
            server_status_data['service_type'] = server.service_type.value
    elif server_count > 1:
        online_count = 0
        offline_count = 0
        all_server_statuses = []
        servers_by_service = {}
        
        for server in all_servers:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                status = service.get_server_info()
                # Extract the actual server name BEFORE overriding the 'name' field
                actual_server_name = status.get('name', server.name)
                
                # DEBUG: Log what we're getting from each service
                current_app.logger.info(f"DEBUG SERVER INFO - Server: {server.name} ({server.service_type.value})")
                current_app.logger.info(f"DEBUG SERVER INFO - Raw status keys: {list(status.keys()) if status else 'None'}")
                current_app.logger.info(f"DEBUG SERVER INFO - Status name: '{status.get('name')}', version: '{status.get('version')}'")
                current_app.logger.info(f"DEBUG SERVER INFO - Extracted actual_server_name: '{actual_server_name}'")
                
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
    # If server_count is 0, server_status_data will be an empty dict, which the template handles.
    current_app.logger.debug(f"Dashboard.py - index(): Server status from service: {server_status_data}")

    recent_activities = HistoryLog.query.order_by(HistoryLog.timestamp.desc()).limit(10).all()
    recent_activities_count = HistoryLog.query.count()

    return render_template('dashboard/index.html',
                           title="Dashboard",
                           total_users=total_users,
                           active_invites_count=active_invites_count,
                           active_streams_count=active_streams_count,
                           server_status=server_status_data,
                           recent_activities=recent_activities,
                           recent_activities_count=recent_activities_count)