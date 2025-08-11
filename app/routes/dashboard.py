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

    # Get active streams count (keep this fast call)
    active_streams_count = 0
    try:
        active_sessions_list = MediaServiceManager.get_all_active_sessions() # This returns a list
        if active_sessions_list:
            active_streams_count = len(active_sessions_list)
    except Exception as e:
        current_app.logger.error(f"Dashboard: Failed to get active streams count: {e}")

    # Server Status Card Logic - Load basic info only, status will be fetched asynchronously
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    
    # Just pass basic server info for initial load, actual status will be loaded via HTMX
    server_status_data = {
        'loading': True,
        'server_count': server_count,
        'servers': [{'id': server.id, 'name': server.name, 'service_type': server.service_type.value} for server in all_servers]
    }

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