from flask import Blueprint, render_template, current_app
from flask_login import login_required
from app.models import User, Invite, HistoryLog
from app.extensions import db
from app.utils.helpers import setup_required, permission_required
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
@setup_required
@permission_required('view_dashboard')
def index():
    current_app.logger.info("=== ADMIN DASHBOARD ROUTE START ===")
    
    current_app.logger.debug("Dashboard: Fetching total users count")
    total_users = User.query.count()
    current_app.logger.debug(f"Dashboard: Total users: {total_users}")
    
    current_app.logger.debug("Dashboard: Fetching active invites count")
    active_invites_count = Invite.query.filter(
        Invite.is_active == True,
        (Invite.expires_at == None) | (Invite.expires_at > db.func.now()), # Use db.func.now() for DB comparison
        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses)
    ).count()
    current_app.logger.debug(f"Dashboard: Active invites: {active_invites_count}")

    # Get active streams count - Load asynchronously to avoid blocking dashboard
    current_app.logger.debug("Dashboard: Setting active streams count to 0 for initial load (will be fetched asynchronously)")
    active_streams_count = 0
    # NOTE: Active streams will be loaded via HTMX after page load to avoid blocking

    # Server Status Card Logic - Load basic info only, status will be fetched asynchronously
    current_app.logger.debug("Dashboard: Getting server list (DB only, no API calls)")
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    current_app.logger.debug(f"Dashboard: Found {server_count} servers in database")
    
    # Just pass basic server info for initial load, actual status will be loaded via HTMX
    server_status_data = {
        'loading': True,
        'server_count': server_count,
        'servers': [{'id': server.id, 'name': server.name, 'service_type': server.service_type.value} for server in all_servers]
    }
    current_app.logger.debug("Dashboard: Server status data prepared (loading state)")

    current_app.logger.debug("Dashboard: Fetching recent activities")
    recent_activities = HistoryLog.query.order_by(HistoryLog.timestamp.desc()).limit(10).all()
    recent_activities_count = HistoryLog.query.count()
    current_app.logger.debug(f"Dashboard: Recent activities: {len(recent_activities)}, total count: {recent_activities_count}")

    current_app.logger.info("Dashboard: Rendering template with data")
    current_app.logger.debug(f"Dashboard: Template data - users: {total_users}, invites: {active_invites_count}, streams: {active_streams_count}, servers: {server_count}, activities: {len(recent_activities)}")
    
    result = render_template('dashboard/index.html',
                           title="Dashboard",
                           total_users=total_users,
                           active_invites_count=active_invites_count,
                           active_streams_count=active_streams_count,
                           server_status=server_status_data,
                           recent_activities=recent_activities,
                           recent_activities_count=recent_activities_count)
    
    current_app.logger.info("=== ADMIN DASHBOARD ROUTE COMPLETE ===")
    return result