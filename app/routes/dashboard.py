from flask import Blueprint, render_template, current_app, request
from flask_login import login_required
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from app.models import UserAppAccess, Invite, HistoryLog
from app.models_media_services import MediaStreamHistory, UserMediaAccess
from app.extensions import db
from app.utils.helpers import setup_required, permission_required, format_duration
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager

bp = Blueprint('dashboard', __name__)

def _generate_top_users_data(days=7, limit=5):
    """Generate top users data for admin dashboard"""
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict
    from sqlalchemy import func
    
    # Calculate date range
    end_date = datetime.now(timezone.utc)
    if days == -1:  # All time
        earliest_stream = MediaStreamHistory.query.order_by(MediaStreamHistory.started_at.asc()).first()
        if earliest_stream:
            start_date = earliest_stream.started_at
        else:
            start_date = end_date - timedelta(days=7)  # Fallback to 7 days
    else:
        start_date = end_date - timedelta(days=days-1)
    
    # Query to get top users by total watch time
    user_stats = db.session.query(
        MediaStreamHistory.user_app_access_uuid,
        MediaStreamHistory.user_media_access_uuid,
        func.count(MediaStreamHistory.id).label('stream_count'),
        func.sum(
            func.coalesce(
                MediaStreamHistory.duration_seconds,
                MediaStreamHistory.view_offset_at_end_seconds,
                60  # Default 1 minute for streams without duration
            )
        ).label('total_seconds')
    ).filter(
        MediaStreamHistory.started_at >= start_date,
        MediaStreamHistory.started_at <= end_date
    ).group_by(
        MediaStreamHistory.user_app_access_uuid,
        MediaStreamHistory.user_media_access_uuid
    ).order_by(
        func.sum(
            func.coalesce(
                MediaStreamHistory.duration_seconds,
                MediaStreamHistory.view_offset_at_end_seconds,
                60
            )
        ).desc()
    ).limit(limit).all()
    
    top_users = []
    for stat in user_stats:
        user_display_name = "Unknown User"
        user_avatar = None
        service_info = []
        
        # Get user info - could be from UserAppAccess (linked) or UserMediaAccess (standalone)
        if stat.user_app_access_uuid:
            user_app_access = UserAppAccess.query.filter_by(uuid=stat.user_app_access_uuid).first()
            if user_app_access:
                user_display_name = user_app_access.get_display_name()
                # Get all services this user has access to
                for media_access in user_app_access.media_accesses:
                    if media_access.server:
                        service_info.append({
                            'type': media_access.server.service_type.value,
                            'name': media_access.server.server_nickname
                        })
        elif stat.user_media_access_uuid:
            user_media_access = UserMediaAccess.query.filter_by(uuid=stat.user_media_access_uuid).first()
            if user_media_access:
                user_display_name = user_media_access.get_display_name()
                user_avatar = user_media_access.get_avatar_url()
                if user_media_access.server:
                    service_info.append({
                        'type': user_media_access.server.service_type.value,
                        'name': user_media_access.server.server_nickname
                    })
        
        # Remove duplicates from service_info
        unique_services = []
        seen_services = set()
        for service in service_info:
            service_key = f"{service['type']}_{service['name']}"
            if service_key not in seen_services:
                unique_services.append(service)
                seen_services.add(service_key)
        
        # Get category breakdown for this user
        category_query = db.session.query(
            MediaStreamHistory.media_type,
            func.sum(
                func.coalesce(
                    MediaStreamHistory.duration_seconds,
                    MediaStreamHistory.view_offset_at_end_seconds,
                    60
                )
            ).label('category_seconds')
        ).filter(
            MediaStreamHistory.started_at >= start_date,
            MediaStreamHistory.started_at <= end_date
        )
        
        # Add user filter based on which UUID is present
        if stat.user_app_access_uuid:
            category_query = category_query.filter(MediaStreamHistory.user_app_access_uuid == stat.user_app_access_uuid)
        elif stat.user_media_access_uuid:
            category_query = category_query.filter(MediaStreamHistory.user_media_access_uuid == stat.user_media_access_uuid)
        
        category_stats = category_query.group_by(MediaStreamHistory.media_type).all()
        
        # Map media types to categories
        categories = {'tv': 0, 'movies': 0, 'music': 0, 'photos': 0}
        for category_stat in category_stats:
            media_type = (category_stat.media_type or '').lower()
            seconds = int(category_stat.category_seconds or 0)
            
            if media_type in ['show', 'episode', 'tv', 'series']:
                categories['tv'] += seconds
            elif media_type in ['movie', 'film']:
                categories['movies'] += seconds
            elif media_type in ['track', 'music', 'audio', 'song']:
                categories['music'] += seconds
            elif media_type in ['photo', 'image', 'picture']:
                categories['photos'] += seconds
            else:
                # Default unknown types to TV
                categories['tv'] += seconds
        
        # Format category durations
        formatted_categories = {}
        for cat, seconds in categories.items():
            if seconds > 0:
                formatted_categories[cat] = format_duration(seconds)
            else:
                formatted_categories[cat] = '0 min'
        
        total_seconds = int(stat.total_seconds or 0)
        
        # Get primary service type for CSS class
        primary_service_type = 'gray'  # Default fallback
        if unique_services:
            primary_service_type = unique_services[0]['type']
        
        # Get primary server info for linking
        primary_server_nickname = None
        primary_server_username = None
        if stat.user_media_access_uuid:
            user_media_access = UserMediaAccess.query.filter_by(uuid=stat.user_media_access_uuid).first()
            if user_media_access and user_media_access.server:
                primary_server_nickname = user_media_access.server.server_nickname
                primary_server_username = user_media_access.external_username
        
        top_users.append({
            'display_name': user_display_name,
            'avatar_url': user_avatar,
            'stream_count': stat.stream_count,
            'total_duration': format_duration(total_seconds),
            'total_seconds': total_seconds,
            'services': unique_services[:3],  # Show max 3 services to avoid clutter
            'categories': formatted_categories,
            'primary_service_type': primary_service_type,
            'server_nickname': primary_server_nickname,
            'server_username': primary_server_username
        })
    
    return top_users

def _generate_admin_streaming_chart_data(days=7):
    """Generate streaming chart data for admin dashboard - stacked by service within each day"""
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict
    
    # Calculate date range
    end_date = datetime.now(timezone.utc)
    if days == -1:  # All time
        earliest_stream = MediaStreamHistory.query.order_by(MediaStreamHistory.started_at.asc()).first()
        if earliest_stream:
            start_date = earliest_stream.started_at
        else:
            start_date = end_date - timedelta(days=7)  # Fallback to 7 days
    else:
        start_date = end_date - timedelta(days=days-1)
    
    # Get all streaming history for the date range
    streaming_history = MediaStreamHistory.query.filter(
        MediaStreamHistory.started_at >= start_date,
        MediaStreamHistory.started_at <= end_date
    ).all()
    
    if not streaming_history:
        return {
            'chart_data': [],
            'services': [],
            'total_streams': 0,
            'total_duration': '0m',
            'most_active_service': 'None',
            'date_range_days': days
        }
    
    # Service color mapping
    service_colors = {
        'plex': '#e5a00d',
        'jellyfin': '#a855f7', 
        'emby': '#22c55e',
        'kavita': '#06b6d4',
        'audiobookshelf': '#8b5cf6',
        'komga': '#f97316',
        'romm': '#8b5cf6'
    }
    
    # Group data by date and service (stacked within same day)
    grouped_data = defaultdict(lambda: defaultdict(float))  # [date][service] = minutes
    service_totals = defaultdict(float)  # Total watch time per service
    service_counts = defaultdict(int)  # Stream counts per service
    total_duration_seconds = 0
    
    for entry in streaming_history:
        entry_date = entry.started_at.date()
        
        # Get service type from the server
        service_type = 'unknown'
        if entry.user_media_access_uuid:
            service_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
            if service_access and service_access.server:
                service_type = service_access.server.service_type.value
        
        # Get duration in minutes
        duration_minutes = 0
        if entry.duration_seconds and entry.duration_seconds > 0:
            duration_minutes = entry.duration_seconds / 60
            total_duration_seconds += entry.duration_seconds
        elif entry.view_offset_at_end_seconds and entry.view_offset_at_end_seconds > 0:
            duration_minutes = entry.view_offset_at_end_seconds / 60
            total_duration_seconds += entry.view_offset_at_end_seconds
        else:
            duration_minutes = 1  # 1 minute minimum to show activity
        
        # Add to grouped data
        grouped_data[entry_date.isoformat()][service_type] += duration_minutes
        service_totals[service_type] += duration_minutes
        service_counts[service_type] += 1
    
    # Generate chart data for the date range
    chart_data_list = []
    start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
    end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
    
    current_date = start_date_only
    while current_date <= end_date_only:
        day_key = current_date.isoformat()
        day_label = current_date.strftime('%b %d')
        
        period_data = {'date': day_key, 'label': day_label}
        
        # Add service watch times for this day (in minutes)
        for service_type in service_totals.keys():
            period_data[service_type] = round(grouped_data[day_key].get(service_type, 0), 1)
        
        chart_data_list.append(period_data)
        current_date += timedelta(days=1)
    
    # Prepare service information for legend
    services = []
    for service_type, total_minutes in service_totals.items():
        service_color = service_colors.get(service_type, '#64748b')
        
        services.append({
            'type': service_type,
            'name': service_type.title(),
            'watch_time': format_duration(total_minutes * 60),  # Convert back to seconds
            'count': service_counts[service_type],
            'color': service_color
        })
    
    # Sort services by watch time (descending)
    services.sort(key=lambda x: service_totals[x['type']], reverse=True)
    
    # Calculate summary stats
    total_streams = sum(service_counts.values())
    most_active_service = services[0]['name'] if services else 'None'
    total_duration_formatted = format_duration(total_duration_seconds)
    
    return {
        'chart_data': chart_data_list,
        'services': services,
        'total_streams': total_streams,
        'total_duration': total_duration_formatted,
        'most_active_service': most_active_service,
        'date_range_days': days
    }

@bp.route('/')
@bp.route('/dashboard')
@login_required
@setup_required
@permission_required('view_dashboard')
def index():
    current_app.logger.info("=== ADMIN DASHBOARD ROUTE START ===")
    
    current_app.logger.debug("Dashboard: Fetching total users count (local + service users)")
    
    # Count local users (UserAppAccess)
    local_users_count = UserAppAccess.query.count()
    current_app.logger.debug(f"Dashboard: Local users: {local_users_count}")
    
    # Count ALL service users (UserMediaAccess records - both standalone AND linked)
    # This matches the /users page logic which shows each UserMediaAccess as a separate card
    from app.models_media_services import UserMediaAccess
    all_service_users_count = UserMediaAccess.query.count()
    current_app.logger.debug(f"Dashboard: All service users (standalone + linked): {all_service_users_count}")
    
    # Total managed users (matches /users page logic exactly)
    total_users = local_users_count + all_service_users_count
    current_app.logger.debug(f"Dashboard: Total managed users: {total_users}")
    
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

    # Server Status Card Logic - Check for cached status first
    current_app.logger.debug("Dashboard: Getting server list and checking for cached status")
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    current_app.logger.debug(f"Dashboard: Found {server_count} servers in database")
    
    # Check if any servers have never been checked (last_status is None)
    unchecked_servers = [server for server in all_servers if server.last_status is None]
    
    if unchecked_servers:
        current_app.logger.info(f"Dashboard: Found {len(unchecked_servers)} servers that have never been checked - performing automatic first check")
        # Perform automatic first check for all servers
        from app.routes.api import get_fresh_server_status
        server_status_data = get_fresh_server_status()
        current_app.logger.debug("Dashboard: Automatic first server check completed")
    else:
        # Check for stored server status in database
        from app.routes.api import get_stored_server_status
        stored_status = get_stored_server_status()
        
        if stored_status:
            current_app.logger.debug("Dashboard: Using stored server status from database")
            server_status_data = stored_status
        else:
            current_app.logger.debug("Dashboard: No stored status, showing initial state")
            # Just pass basic server info for initial load, actual status will be loaded via HTMX
            server_status_data = {
                'loading': True,
                'server_count': server_count,
                'servers': [{'id': server.id, 'name': server.server_nickname, 'service_type': server.service_type.value} for server in all_servers]
            }
    current_app.logger.debug("Dashboard: Server status data prepared")

    current_app.logger.debug("Dashboard: Fetching recent activities")
    recent_activities = HistoryLog.query.order_by(HistoryLog.timestamp.desc()).limit(10).all()
    recent_activities_count = HistoryLog.query.count()
    current_app.logger.debug(f"Dashboard: Recent activities: {len(recent_activities)}, total count: {recent_activities_count}")

    # Generate admin streaming chart data (last 7 days by default)
    current_app.logger.debug("Dashboard: Generating admin streaming chart data")
    days_param = request.args.get('days', '7')
    try:
        if days_param == 'all':
            days = -1
        else:
            days = int(days_param)
            if days not in [7, 30, 90]:
                days = 7
    except (ValueError, TypeError):
        days = 7
    
    chart_data = _generate_admin_streaming_chart_data(days)
    current_app.logger.debug(f"Dashboard: Chart data generated for {days} days")

    # Generate top users data
    current_app.logger.debug("Dashboard: Generating top users data")
    top_users_data = _generate_top_users_data(days, limit=5)
    current_app.logger.debug(f"Dashboard: Top users data generated with {len(top_users_data)} users")

    current_app.logger.info("Dashboard: Rendering template with data")
    current_app.logger.debug(f"Dashboard: Template data - users: {total_users}, invites: {active_invites_count}, streams: {active_streams_count}, servers: {server_count}, activities: {len(recent_activities)}")
    
    result = render_template('dashboard/index.html',
                           title="Dashboard",
                           total_users=total_users,
                           active_invites_count=active_invites_count,
                           active_streams_count=active_streams_count,
                           server_status=server_status_data,
                           recent_activities=recent_activities,
                           recent_activities_count=recent_activities_count,
                           chart_data=chart_data,
                           top_users_data=top_users_data,
                           selected_days=days)
    
    current_app.logger.info("=== ADMIN DASHBOARD ROUTE COMPLETE ===")
    return result