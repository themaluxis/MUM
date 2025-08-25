# File: app/routes/user.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, make_response, abort
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
from app.models import UserAppAccess, Owner, EventType
from app.forms import UserEditForm, UserResetPasswordForm
from app.extensions import db
from app.utils.helpers import permission_required, log_event
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import ServiceType, UserMediaAccess, MediaStreamHistory
from app.services.media_service_manager import MediaServiceManager
from app.services import user_service
import json
import urllib.parse

# Note the new blueprint name and singular URL prefix
bp = Blueprint('user', __name__, url_prefix='/user')

def _generate_streaming_chart_data(user, days=30):
    """Generate streaming history chart data for the specified number of days"""
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict
    from app.utils.helpers import format_duration
    import calendar
    
    # Calculate date range based on days parameter
    end_date = datetime.now(timezone.utc)
    if days == -1:  # All time
        # Get the earliest stream date for this user
        earliest_stream = MediaStreamHistory.query.filter(
            MediaStreamHistory.user_app_access_uuid == user.uuid
        ).order_by(MediaStreamHistory.started_at.asc()).first()
        
        if earliest_stream:
            start_date = earliest_stream.started_at
        else:
            start_date = end_date - timedelta(days=30)  # Fallback to 30 days
    else:
        # For daily periods, we want exactly 'days' number of days including today
        # So if days=7, we want 7 days total: today + 6 previous days
        start_date = end_date - timedelta(days=days-1)
    
    # Get streaming history for this user
    history_query = MediaStreamHistory.query.filter(
        MediaStreamHistory.user_app_access_uuid == user.uuid,
        MediaStreamHistory.started_at >= start_date,
        MediaStreamHistory.started_at <= end_date
    )
    
    streaming_history = history_query.all()
    
    if not streaming_history:
        # Return empty chart data structure instead of None
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
        'jellyfin': '#00a4dc', 
        'emby': '#52b54b',
        'kavita': '#f39c12',
        'audiobookshelf': '#8b5cf6',
        'komga': '#ef4444',
        'romm': '#10b981'
    }
    
    # Determine grouping strategy based on days parameter
    if days == 7:
        # Last 7 days: Show daily
        grouping_type = 'daily'
    elif days in [30, 90]:
        # Last 30/90 days: Group into 7-day periods
        grouping_type = 'weekly'
    elif days == 365 or days == -1:
        # Last year or all time: Group by months
        grouping_type = 'monthly'
    else:
        # Default to daily for any other values
        grouping_type = 'daily'
    
    # Group data by appropriate time period, service, and content type
    if grouping_type == 'monthly':
        grouped_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # [year_month][service][content_type]
    elif grouping_type == 'weekly':
        grouped_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # [week_start_date][service][content_type]
    else:  # daily
        grouped_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # [date][service][content_type]
    
    service_content_totals = defaultdict(lambda: defaultdict(int))  # [service][content_type]
    service_totals = defaultdict(int)  # Total watch time per service
    service_counts = defaultdict(int)  # Stream counts per service
    total_duration_seconds = 0
    
    for entry in streaming_history:
        # Get the date (without time)
        entry_date = entry.started_at.date()
        
        # Determine the grouping key based on grouping type
        if grouping_type == 'monthly':
            # Group by year-month (e.g., "2024-01")
            group_key = entry_date.strftime('%Y-%m')
        elif grouping_type == 'weekly':
            # Group by week start date (Monday of the week)
            days_since_monday = entry_date.weekday()
            week_start = entry_date - timedelta(days=days_since_monday)
            group_key = week_start.isoformat()
        else:  # daily
            group_key = entry_date.isoformat()
        
        # Get service type from the server
        service_type = 'unknown'
        if entry.user_media_access_uuid:
            service_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
            if service_access and service_access.server:
                service_type = service_access.server.service_type.value
        
        # Determine content type based on media_type or service type
        content_type = 'mixed'
        if entry.media_type:
            media_type = entry.media_type.lower()
            if media_type in ['movie', 'film']:
                content_type = 'movies'
            elif media_type in ['episode', 'show', 'series']:
                content_type = 'tv_shows'
            elif media_type in ['track', 'song', 'album']:
                content_type = 'music'
            elif media_type in ['book', 'audiobook']:
                content_type = 'books'
            elif media_type in ['comic', 'manga']:
                content_type = 'comics'
            else:
                content_type = media_type
        else:
            # Fallback to service-based categorization
            if service_type == 'kavita':
                content_type = 'comics'
            elif service_type == 'audiobookshelf':
                content_type = 'books'
            elif service_type == 'komga':
                content_type = 'comics'
            elif service_type == 'romm':
                content_type = 'games'
            else:
                content_type = 'mixed'
        
        # Get duration in minutes for the chart
        duration_minutes = 0
        if entry.duration_seconds and entry.duration_seconds > 0:
            duration_minutes = entry.duration_seconds / 60  # Convert to minutes
            total_duration_seconds += entry.duration_seconds
        else:
            # If no duration, use a small default value so streams show up on the chart
            duration_minutes = 1  # 1 minute minimum to show activity
        
        # Add watch time per group per service per content type (in minutes)
        grouped_data[group_key][service_type][content_type] += duration_minutes
        service_content_totals[service_type][content_type] += duration_minutes
        service_totals[service_type] += duration_minutes
        service_counts[service_type] += 1
    
    # Generate chart data for the date range (including periods with no activity)
    chart_data_list = []
    
    # Create datasets for each service-content combination
    service_content_combinations = []
    for service_type in service_totals.keys():
        for content_type in service_content_totals[service_type].keys():
            service_content_combinations.append(f"{service_type}_{content_type}")
    
    # Generate time periods based on grouping type
    if grouping_type == 'monthly':
        # Generate monthly periods
        # Ensure both dates are timezone-aware and normalized to month boundaries
        if start_date.tzinfo is None:
            # If start_date is naive, make it timezone-aware
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            # If end_date is naive, make it timezone-aware  
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        # Normalize to first day of month with same timezone
        current_date = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        while current_date <= end_date_month:
            month_key = current_date.strftime('%Y-%m')
            month_label = current_date.strftime('%b %Y')
            
            period_data = {'date': month_key, 'label': month_label}
            
            # Add service-content watch times for this month (in minutes)
            for service_type in service_totals.keys():
                for content_type in service_content_totals[service_type].keys():
                    combination_key = f"{service_type}_{content_type}"
                    period_data[combination_key] = round(grouped_data[month_key][service_type].get(content_type, 0), 1)
            
            chart_data_list.append(period_data)
            
            # Move to next month
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1)
                
    elif grouping_type == 'weekly':
        # Generate weekly periods
        # Start from the Monday of the week containing start_date
        # Ensure we're working with date objects consistently
        start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
        end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
        
        days_since_monday = start_date_only.weekday()
        current_week_start = start_date_only - timedelta(days=days_since_monday)
        
        while current_week_start <= end_date_only:
            week_key = current_week_start.isoformat()
            week_end = current_week_start + timedelta(days=6)
            
            # Create label for the week
            if current_week_start.month == week_end.month:
                week_label = f"{current_week_start.strftime('%b %d')}-{week_end.day}"
            else:
                week_label = f"{current_week_start.strftime('%b %d')}-{week_end.strftime('%b %d')}"
            
            period_data = {'date': week_key, 'label': week_label}
            
            # Add service-content watch times for this week (in minutes)
            for service_type in service_totals.keys():
                for content_type in service_content_totals[service_type].keys():
                    combination_key = f"{service_type}_{content_type}"
                    period_data[combination_key] = round(grouped_data[week_key][service_type].get(content_type, 0), 1)
            
            chart_data_list.append(period_data)
            current_week_start += timedelta(days=7)
            
    else:  # daily
        # Generate daily periods (existing logic)
        # Ensure we're working with date objects consistently
        start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
        end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
        
        current_date = start_date_only
        while current_date <= end_date_only:
            day_key = current_date.isoformat()
            day_label = current_date.strftime('%b %d')
            
            period_data = {'date': day_key, 'label': day_label}
            
            # Add service-content watch times for this day (in minutes)
            for service_type in service_totals.keys():
                for content_type in service_content_totals[service_type].keys():
                    combination_key = f"{service_type}_{content_type}"
                    period_data[combination_key] = round(grouped_data[day_key][service_type].get(content_type, 0), 1)
            
            chart_data_list.append(period_data)
            current_date += timedelta(days=1)
    
    # Content type color mapping
    content_colors = {
        'movies': '#ef4444',      # Red
        'tv_shows': '#3b82f6',    # Blue  
        'music': '#10b981',       # Green
        'books': '#f59e0b',       # Amber
        'comics': '#8b5cf6',      # Purple
        'games': '#06b6d4',       # Cyan
        'mixed': '#6b7280',       # Gray
        'unknown': '#64748b'      # Slate
    }
    
    # Prepare service information for legend (with content breakdown)
    services = []
    for service_type, total_minutes in service_totals.items():
        # Get service colors from the original mapping
        service_color = service_colors.get(service_type, '#64748b')
        
        services.append({
            'type': service_type,
            'name': service_type.title(),
            'watch_time': format_duration(total_minutes * 60),  # Convert back to seconds for formatting
            'count': service_counts[service_type],
            'color': service_color,
            'content_breakdown': service_content_totals[service_type]
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
        'service_content_combinations': service_content_combinations,
        'content_colors': content_colors,
        'total_streams': total_streams,
        'total_duration': total_duration_formatted,
        'most_active_service': most_active_service,
        'date_range_days': days
    }

def check_if_user_is_admin(user):
    """Check if a UserAppAccess user is an admin by looking up their access in UserMediaAccess"""
    if not isinstance(user, UserAppAccess):
        return False
    
    # Get the user's UserMediaAccess records for Plex servers
    from app.models_media_services import MediaServer
    plex_servers = MediaServer.query.filter_by(service_type=ServiceType.PLEX).all()
    
    for plex_server in plex_servers:
        access = UserMediaAccess.query.filter_by(
            user_app_access_uuid=user.uuid,
            server_id=plex_server.id
        ).first()
        
        if access and access.external_user_alt_id:  # external_user_alt_id is the plex_uuid
            # Check if this plex_uuid belongs to an Owner
            owner = Owner.query.filter_by(plex_uuid=access.external_user_alt_id).first()
            if owner:
                return True
    
    return False

@bp.route('/')
@bp.route('/index')
@bp.route('/dashboard')
@login_required
def index():
    """User dashboard/index page for regular user accounts - accessible at /user/dashboard"""
    # Ensure this is a regular user, not an owner
    if isinstance(current_user, Owner):
        return redirect(url_for('dashboard.index'))
    
    # Ensure this is an AppUser (local user account)
    if not isinstance(current_user, UserAppAccess):
        flash('Access denied. Please log in with a valid user account.', 'danger')
        return redirect(url_for('auth.app_login'))
    
    # Get application name for welcome message
    from app.models import Setting
    app_name = Setting.get('APP_NAME', 'MUM')
    
    # Get date range from query parameter
    days_param = request.args.get('days', '30')
    try:
        if days_param == 'all':
            days = -1
        else:
            days = int(days_param)
            # Validate days parameter
            if days not in [7, 30, 90, 365]:
                days = 30
    except (ValueError, TypeError):
        days = 30
    
    # Generate streaming history chart data
    chart_data = _generate_streaming_chart_data(current_user, days)
    
    # Debug logging (simplified)
    if chart_data and chart_data.get('services'):
        current_app.logger.info(f"Chart generated: {len(chart_data['services'])} services, {chart_data['total_streams']} streams")
    else:
        current_app.logger.info("Chart generated: No data available")
    
    return render_template('user/index.html', 
                         title="Dashboard", 
                         app_name=app_name,
                         user=current_user,
                         chart_data=chart_data,
                         selected_days=days)

@bp.route('/<server_nickname>/<server_username>', methods=['GET', 'POST'])
@login_required
@permission_required('view_user')
def view_service_account(server_nickname, server_username):
    """View service account profile by server nickname and username"""
    from app.models_media_services import MediaServer
    
    # URL decode the parameters to handle special characters
    try:
        server_nickname = urllib.parse.unquote(server_nickname)
        server_username = urllib.parse.unquote(server_username)
    except Exception as e:
        current_app.logger.warning(f"Error decoding URL parameters: {e}")
        abort(400)
    
    # Validate parameters
    if not server_nickname or not server_username:
        abort(400)
    
    # Check for potential username conflicts with app users
    # If server_nickname matches an app user username, this could be ambiguous
    user_conflict = UserAppAccess.query.filter_by(username=server_nickname).first()
    if user_conflict:
        current_app.logger.warning(f"Potential conflict: server nickname '{server_nickname}' matches app user username")
    
    # Find the server by nickname (name)
    server = MediaServer.query.filter_by(server_nickname=server_nickname).first_or_404()
    
    # Find the service account by server and username
    # Look for UserMediaAccess record directly (handles both standalone and linked users)
    access = UserMediaAccess.query.filter_by(
        server_id=server.id,
        external_username=server_username
    ).first()
    
    if not access:
        current_app.logger.warning(f"Service account not found: {server_username} on {server_nickname}")
        abort(404)
    
    # Create a mock user object for the template
    class MockServiceUser:
        def __init__(self, access):
            self.id = access.id
            self.uuid = access.uuid
            self.username = access.external_username
            self.email = access.external_email
            self.notes = access.notes
            self.created_at = access.created_at
            self.last_login_at = access.last_activity_at
            self.media_accesses = [access]
            self.access_expires_at = access.access_expires_at
            self.discord_user_id = access.discord_user_id
            self.is_active = access.is_active
            self._is_service_user = True
            self._access_record = access
        
        def get_display_name(self):
            return self._access_record.external_username or 'Unknown'
    
    user = MockServiceUser(access)
    
    # Check if this UserMediaAccess is linked to a UserAppAccess account
    linked_user_app_access = None
    if access.user_app_access_id:
        linked_user_app_access = UserAppAccess.query.get(access.user_app_access_id)
    
    # Get the active tab from the URL query. Default to 'profile' for GET, 'settings' for POST context.
    tab = request.args.get('tab', 'settings' if request.method == 'POST' else 'profile')
    
    # Correctly instantiate the form:
    # On POST, it's populated from request.form.
    # On GET, it's populated from the user object.
    form = UserEditForm(request.form if request.method == 'POST' else None, obj=user)
    
    # Populate dynamic choices for the form - only show libraries from servers this user has access to
    # For standalone service users, we only have the single access record
    user_access_records = [access]
    
    available_libraries = {}
    current_app.logger.info(f"DEBUG KAVITA FORM: Building available libraries for user {user.id}")
    
    for access in user_access_records:
        try:
            service = MediaServiceFactory.create_service_from_db(access.server)
            current_app.logger.info(f"DEBUG KAVITA FORM: Processing server {access.server.server_nickname} (type: {access.server.service_type.value})")
            current_app.logger.info(f"DEBUG KAVITA FORM: User access record allowed_library_ids: {access.allowed_library_ids}")
            
            if service:
                server_libraries = service.get_libraries()
                current_app.logger.info(f"DEBUG KAVITA FORM: Server libraries from API: {[{lib.get('id'): lib.get('name')} for lib in server_libraries]}")
                
                for lib in server_libraries:
                    lib_id = lib.get('external_id') or lib.get('id')
                    lib_name = lib.get('name', 'Unknown')
                    if lib_id:
                        # For Kavita, create compound IDs to match the format used in user access records
                        if access.server.service_type.value == 'kavita':
                            compound_lib_id = f"{lib_id}_{lib_name}"
                            available_libraries[compound_lib_id] = lib_name
                            current_app.logger.info(f"DEBUG KAVITA FORM: Added Kavita library: {compound_lib_id} -> {lib_name}")
                        else:
                            available_libraries[str(lib_id)] = lib_name
                            current_app.logger.info(f"DEBUG KAVITA FORM: Added non-Kavita library: {lib_id} -> {lib_name}")
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {access.server.server_nickname}: {e}")
    
    current_app.logger.info(f"DEBUG KAVITA FORM: Final available_libraries: {available_libraries}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    current_app.logger.info(f"DEBUG KAVITA FORM: Form choices set to: {form.libraries.choices}")

    # Handle form submission for the settings tab
    if form.validate_on_submit(): # This handles (if request.method == 'POST' and form.validate())
        try:
            # Updated expiration logic to handle DateField calendar picker
            access_expiration_changed = False
            
            if form.clear_access_expiration.data:
                if user.access_expires_at is not None:
                    user.access_expires_at = None
                    access_expiration_changed = True
            elif form.access_expiration.data:
                # WTForms gives a date object. Combine with max time to set expiry to end of day.
                new_expiry_datetime = datetime.combine(form.access_expiration.data, datetime.max.time())
                # Only update if the date is actually different
                if user.access_expires_at is None or user.access_expires_at.date() != new_expiry_datetime.date():
                    user.access_expires_at = new_expiry_datetime
                    access_expiration_changed = True
            
            # Get current library IDs from UserMediaAccess records
            current_library_ids = []
            for access in user_access_records:
                current_library_ids.extend(access.allowed_library_ids or [])
            
            original_library_ids = set(current_library_ids)
            new_library_ids_from_form = set(form.libraries.data or [])
            libraries_changed = (original_library_ids != new_library_ids_from_form)

            # Update user fields directly (not library-related)
            user.notes = form.notes.data
            user.is_discord_bot_whitelisted = form.is_discord_bot_whitelisted.data
            user.is_purge_whitelisted = form.is_purge_whitelisted.data
            user.allow_4k_transcode = form.allow_4k_transcode.data
            
            # Update library access in UserMediaAccess records if changed
            if libraries_changed:
                for access in user_access_records:
                    try:
                        # Get the service for this server
                        service = MediaServiceFactory.create_service_from_db(access.server)
                        if service:
                            # Get libraries available on this server
                            server_libraries = service.get_libraries()
                            server_lib_ids = [lib.get('external_id') or lib.get('id') for lib in server_libraries]
                            
                            # Filter the new library IDs to only include ones available on this server
                            new_libs_for_this_server = []
                            for lib_id in new_library_ids_from_form:
                                if access.server.service_type.value == 'kavita':
                                    # For Kavita, extract the numeric ID from compound format (e.g., "1_Comics" -> "1")
                                    if '_' in str(lib_id):
                                        numeric_id = str(lib_id).split('_')[0]
                                        if numeric_id in [str(sid) for sid in server_lib_ids]:
                                            new_libs_for_this_server.append(numeric_id)
                                    elif str(lib_id) in [str(sid) for sid in server_lib_ids]:
                                        new_libs_for_this_server.append(str(lib_id))
                                else:
                                    # For other services, use direct matching
                                    if lib_id in server_lib_ids:
                                        new_libs_for_this_server.append(lib_id)
                            
                            # Special handling for Jellyfin: if all libraries are selected, use '*' wildcard
                            if (access.server.service_type == ServiceType.JELLYFIN and 
                                set(new_libs_for_this_server) == set(server_lib_ids) and 
                                len(server_lib_ids) > 0):
                                new_libs_for_this_server = ['*']
                            
                            # Update the access record
                            access.allowed_library_ids = new_libs_for_this_server
                            access.updated_at = datetime.utcnow()
                            
                            # Update the media service if it supports user access updates
                            if hasattr(service, 'update_user_access'):
                                # Use the external_user_id from UserMediaAccess for all services
                                user_identifier = access.external_user_id
                                if user_identifier:
                                    service.update_user_access(user_identifier, new_libs_for_this_server)
                    except Exception as e:
                        current_app.logger.error(f"Error updating library access for server {access.server.server_nickname}: {e}")
                
                log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' library access updated", user_id=user.id, admin_id=current_user.id)
            
            user.updated_at = datetime.utcnow()
            
            if access_expiration_changed:
                if user.access_expires_at is None:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' access expiration cleared.", user_id=user.id, admin_id=current_user.id)
                else:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' access expiration set to {user.access_expires_at.strftime('%Y-%m-%d')}.", user_id=user.id, admin_id=current_user.id)
            
            # This commit saves all changes from user_service and the expiration date
            db.session.commit()
            
            if request.headers.get('HX-Request'):
                # Re-fetch user data to ensure the form is populated with the freshest data after save
                user = UserAppAccess.query.get_or_404(user.id)
                form_after_save = UserEditForm(obj=user)
                
                # Re-populate the dynamic choices and data for the re-rendered form
                form_after_save.libraries.choices = list(available_libraries.items())
                
                # Get current library IDs from UserMediaAccess records for the re-rendered form
                current_library_ids_after_save = []
                updated_user_access_records = UserMediaAccess.query.filter_by(service_account_id=user.id).all()
                for access in updated_user_access_records:
                    current_library_ids_after_save.extend(access.allowed_library_ids or [])
                
                # Handle special case for Jellyfin users with '*' (all libraries access)
                if current_library_ids_after_save == ['*']:
                    # If user has "All Libraries" access, check all available library checkboxes
                    form_after_save.libraries.data = list(available_libraries.keys())
                else:
                    form_after_save.libraries.data = list(set(current_library_ids_after_save))

                # OOB-SWAP LOGIC
                # 1. Render the updated form for the modal (the primary target)
                # Build user_server_names for the template
                user_server_names_for_modal = {}
                user_server_names_for_modal[user.uuid] = []
                for access in user_access_records:
                    if access.server.server_nickname not in user_server_names_for_modal[user.uuid]:
                        user_server_names_for_modal[user.uuid].append(access.server.server_nickname)
                
                modal_html = render_template('user/partials/settings_tab.html', form=form_after_save, user=user, user_server_names=user_server_names_for_modal)

                # 2. Render the updated user card for the OOB swap
                # We need the same context that the main user list uses for a card
                
                # Get all user access records for proper library display
                all_user_access_records = UserMediaAccess.query.filter_by(service_account_id=user.id).all()
                user_sorted_libraries = {}
                user_service_types = {}
                user_server_names = {}
                
                # Collect library IDs from all access records
                all_library_ids = []
                user_service_types[user.uuid] = []
                user_server_names[user.uuid] = []
                
                for access in all_user_access_records:
                    all_library_ids.extend(access.allowed_library_ids or [])
                    # Track service types
                    if access.server.service_type not in user_service_types[user.uuid]:
                        user_service_types[user.uuid].append(access.server.service_type)
                    # Track server names
                    if access.server.server_nickname not in user_server_names[user.uuid]:
                        user_server_names[user.uuid].append(access.server.server_nickname)
                
                # Handle special case for Jellyfin users with '*' (all libraries access)
                if all_library_ids == ['*']:
                    lib_names = ['All Libraries']
                else:
                    # Check if this user has library_names available (for services like Kavita)
                    if hasattr(user, 'library_names') and user.library_names:
                        # Use library_names from the user object
                        lib_names = user.library_names
                    else:
                        # Fallback to looking up in available_libraries
                        # For Kavita unique IDs (format: "0_Comics"), extract the name part
                        lib_names = []
                        for lib_id in all_library_ids:
                            if '_' in str(lib_id) and str(lib_id).split('_', 1)[0].isdigit():
                                # This looks like a Kavita unique ID (e.g., "0_Comics"), extract the name
                                lib_name = str(lib_id).split('_', 1)[1]
                                lib_names.append(lib_name)
                            else:
                                # Regular library ID lookup
                                lib_names.append(available_libraries.get(str(lib_id), f'Unknown Lib {lib_id}'))
                user_sorted_libraries[user.uuid] = sorted(lib_names, key=str.lower)
                
                # Get Owner with plex_uuid for filtering (AppUsers don't have plex_uuid)
                owner = Owner.query.filter(Owner.plex_uuid.isnot(None)).first()
                admin_accounts = [owner] if owner else []
                admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts if admin.plex_uuid}

                card_html = render_template(
                    'users/partials/_single_user_card.html',
                    user=user,
                    user_sorted_libraries=user_sorted_libraries,
                    user_service_types=user_service_types,
                    user_server_names=user_server_names,
                    admins_by_uuid=admins_by_uuid,
                    current_user=current_user 
                )
                
                # 3. Add the oob-swap attribute to the card's root div
                card_html_oob = card_html.replace(f'id="user-card-{user.uuid}"', f'id="user-card-{user.uuid}" hx-swap-oob="true"')

                # 4. Combine the modal and card HTML for the response
                final_html = modal_html + card_html_oob

                # Create the toast message payload
                toast_payload = {
                    "showToastEvent": {
                        "message": f"User '{user.get_display_name()}' updated successfully.",
                        "category": "success"
                    }
                }
                
                # Create the response and add the HX-Trigger header
                response = make_response(final_html)
                response.headers['HX-Trigger'] = json.dumps(toast_payload)
                return response
            else:
                # Fallback for standard form submissions - redirect to service account route
                flash(f"User '{user.get_display_name()}' updated successfully.", "success")
                back_param = request.args.get('back')
                back_view_param = request.args.get('back_view')
                redirect_params = {'server_nickname': server_nickname, 'server_username': server_username, 'tab': 'settings'}
                if back_param:
                    redirect_params['back'] = back_param
                if back_view_param:
                    redirect_params['back_view'] = back_view_param
                return redirect(url_for('user.view_service_account', **redirect_params))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating user {user.get_display_name()}: {e}", exc_info=True)
            flash(f"Error updating user: {e}", "danger")

    if request.method == 'POST' and form.errors:
        if request.headers.get('HX-Request'):
            return render_template('user/partials/settings_tab.html', form=form, user=user), 422

    if request.method == 'GET':
        # Get current library IDs from UserMediaAccess records (same as quick edit form)
        current_library_ids = []
        for access in user_access_records:
            current_library_ids.extend(access.allowed_library_ids or [])
        
        current_app.logger.info(f"DEBUG KAVITA FORM: Current library IDs from access records: {current_library_ids}")
        current_app.logger.info(f"DEBUG KAVITA FORM: Available library keys: {list(available_libraries.keys())}")
        
        # Handle special case for Jellyfin users with '*' (all libraries access)
        if current_library_ids == ['*']:
            # If user has "All Libraries" access, check all available library checkboxes
            form.libraries.data = list(available_libraries.keys())
            current_app.logger.info(f"DEBUG KAVITA FORM: Jellyfin wildcard case - setting form data to: {form.libraries.data}")
        else:
            # For Kavita users, ensure we're using the compound IDs that match the available_libraries keys
            validated_library_ids = []
            for lib_id in current_library_ids:
                current_app.logger.info(f"DEBUG KAVITA FORM: Processing library ID: {lib_id}")
                if str(lib_id) in available_libraries:
                    validated_library_ids.append(str(lib_id))
                    current_app.logger.info(f"DEBUG KAVITA FORM: Direct match found for: {lib_id}")
                else:
                    current_app.logger.info(f"DEBUG KAVITA FORM: No direct match for {lib_id}, searching for compound ID...")
                    # This might be a legacy ID format, try to find a matching compound ID
                    found_match = False
                    for available_id in available_libraries.keys():
                        if '_' in available_id and available_id.startswith(f"{lib_id}_"):
                            validated_library_ids.append(available_id)
                            current_app.logger.info(f"DEBUG KAVITA FORM: Found compound match: {lib_id} -> {available_id}")
                            found_match = True
                            break
                    
                    # If no compound match, try matching by library name (for Kavita ID changes)
                    if not found_match and '_' in str(lib_id):
                        stored_lib_name = str(lib_id).split('_', 1)[1]  # Extract name from stored ID
                        current_app.logger.info(f"DEBUG KAVITA FORM: Trying name match for: {stored_lib_name}")
                        for available_id, available_name in available_libraries.items():
                            if available_name == stored_lib_name:
                                validated_library_ids.append(available_id)
                                current_app.logger.info(f"DEBUG KAVITA FORM: Found name match: {lib_id} -> {available_id} (name: {stored_lib_name})")
                                found_match = True
                                break
                    
                    if not found_match:
                        current_app.logger.warning(f"DEBUG KAVITA FORM: No match found for library ID: {lib_id}")
            
            form.libraries.data = list(set(validated_library_ids))  # Remove duplicates
            current_app.logger.info(f"DEBUG KAVITA FORM: Final form.libraries.data: {form.libraries.data}")
        # Remove the old access_expires_in_days logic since we're now using DateField
        # The form will automatically populate access_expires_at from the user object via obj=user

    # Use UUID for user_service calls
    current_app.logger.info(f"DEBUG STATS: Getting stats for {user.uuid}")
    current_app.logger.info(f"DEBUG STATS: User type check - _is_service_user: {getattr(user, '_is_service_user', 'N/A')}")
    
    stream_stats = user_service.get_user_stream_stats(user.uuid)
    current_app.logger.info(f"DEBUG STATS: Raw stream_stats returned: {stream_stats}")
    
    last_ip_map = user_service.get_bulk_last_known_ips([user.uuid])
    current_app.logger.info(f"DEBUG STATS: Last IP map: {last_ip_map}")
    
    last_ip = last_ip_map.get(str(user.uuid))
    user.stream_stats = stream_stats
    user.total_plays = stream_stats.get('global', {}).get('all_time_plays', 0)
    user.total_duration = stream_stats.get('global', {}).get('all_time_duration_seconds', 0)
    user.last_known_ip = last_ip if last_ip else 'N/A'
    
    current_app.logger.info(f"DEBUG STATS: Final stats - plays: {user.total_plays}, duration: {user.total_duration}, IP: {user.last_known_ip}")
    
    # Additional debugging - check what's actually in the database for this user
    from app.models_media_services import MediaStreamHistory
    db_records = MediaStreamHistory.query.filter_by(user_media_access_uuid=user.uuid).count()
    current_app.logger.info(f"DEBUG STATS: Direct DB check - MediaStreamHistory records for user_media_access_uuid={user.uuid}: {db_records}")
    
    # Check if user_service is looking in the right place
    current_app.logger.info(f"DEBUG STATS: Checking user_service logic for user UUID: {user.uuid}")
    
    stream_history_pagination = None
    kavita_reading_stats = None
    kavita_reading_history = None
    
    if tab == 'history':
        page = request.args.get('page', 1, type=int)
        
        # Check if this is a Kavita user and get reading data
        is_kavita_user = False
        kavita_user_id = None
        
        current_app.logger.info(f"DEBUG KAVITA HISTORY: Checking user {user.id} for Kavita access")
        current_app.logger.info(f"DEBUG KAVITA HISTORY: User access records: {[(access.server.server_nickname, access.server.service_type.value, access.external_user_id) for access in user_access_records]}")
        
        for access in user_access_records:
            if access.server.service_type.value == 'kavita':
                is_kavita_user = True
                kavita_user_id = access.external_user_id
                current_app.logger.info(f"DEBUG KAVITA HISTORY: Found Kavita user! Server: {access.server.server_nickname}, External User ID: {kavita_user_id}")
                break
        
        current_app.logger.info(f"DEBUG KAVITA HISTORY: Is Kavita user: {is_kavita_user}, User ID: {kavita_user_id}")
        
        if is_kavita_user and kavita_user_id:
            # Get Kavita reading data
            try:
                kavita_server = None
                for access in user_access_records:
                    if access.server.service_type.value == 'kavita':
                        kavita_server = access.server
                        break
                
                if kavita_server:
                    service = MediaServiceFactory.create_service_from_db(kavita_server)
                    if service:
                        kavita_reading_stats = service.get_user_reading_stats(kavita_user_id)
                        kavita_reading_history = service.get_user_reading_history(kavita_user_id)
                        current_app.logger.info(f"DEBUG KAVITA HISTORY: Stats: {kavita_reading_stats}")
                        current_app.logger.info(f"DEBUG KAVITA HISTORY: History: {kavita_reading_history}")
            except Exception as e:
                current_app.logger.error(f"Error fetching Kavita reading data: {e}")
        
        if not is_kavita_user:
            # For non-Kavita users, use regular stream history
            # Check if this is a service user (MockServiceUser) or a regular UserAppAccess
            current_app.logger.info(f"DEBUG HISTORY: Processing history for user ID {user.id}, username: {getattr(user, 'username', 'N/A')}")
            current_app.logger.info(f"DEBUG HISTORY: User type: {type(user).__name__}")
            current_app.logger.info(f"DEBUG HISTORY: Has _is_service_user: {hasattr(user, '_is_service_user')}")
            current_app.logger.info(f"DEBUG HISTORY: _is_service_user value: {getattr(user, '_is_service_user', 'N/A')}")
            
            if hasattr(user, '_is_service_user') and user._is_service_user:
                # This is a service user - we need to filter by user_media_access_uuid to get only this service's history
                # For linked service accounts, history is stored with both user_app_access_uuid AND user_media_access_uuid
                current_app.logger.info(f"DEBUG HISTORY: Service user - querying MediaStreamHistory with user_media_access_uuid={user.uuid}")
                current_app.logger.info(f"DEBUG HISTORY: Access record details - server: {access.server.server_nickname}, service_type: {access.server.service_type.value}, external_username: {access.external_username}")
                stream_history_pagination = MediaStreamHistory.query.filter_by(user_media_access_uuid=user.uuid)\
                    .order_by(MediaStreamHistory.started_at.desc())\
                    .paginate(page=page, per_page=15, error_out=False)
                current_app.logger.info(f"DEBUG HISTORY: Found {stream_history_pagination.total} history records for service user")
                
                # Additional debugging - show sample records
                if stream_history_pagination.items:
                    current_app.logger.info(f"DEBUG HISTORY: Sample records:")
                    for i, record in enumerate(stream_history_pagination.items[:3]):
                        current_app.logger.info(f"DEBUG HISTORY: Record {i+1}: {record.media_title} at {record.started_at} (user_media_access_uuid: {record.user_media_access_uuid}, user_app_access_uuid: {record.user_app_access_uuid})")
            else:
                # This is a regular UserAppAccess - query by user_app_access_id
                current_app.logger.info(f"DEBUG HISTORY: Regular UserAppAccess - querying MediaStreamHistory with user_app_access_uuid={user.uuid}")
                stream_history_pagination = MediaStreamHistory.query.filter_by(user_app_access_uuid=user.uuid)\
                    .order_by(MediaStreamHistory.started_at.desc())\
                    .paginate(page=page, per_page=15, error_out=False)
                current_app.logger.info(f"DEBUG HISTORY: Found {stream_history_pagination.total} history records for regular user")
            
            # Additional debugging - check what's actually in the database
            total_records = MediaStreamHistory.query.count()
            records_with_user_media_access = MediaStreamHistory.query.filter(MediaStreamHistory.user_media_access_uuid.isnot(None)).count()
            records_with_user_app_access = MediaStreamHistory.query.filter(MediaStreamHistory.user_app_access_uuid.isnot(None)).count()
            current_app.logger.info(f"DEBUG HISTORY: Total MediaStreamHistory records: {total_records}")
            current_app.logger.info(f"DEBUG HISTORY: Records with user_media_access_uuid: {records_with_user_media_access}")
            current_app.logger.info(f"DEBUG HISTORY: Records with user_app_access_uuid: {records_with_user_app_access}")
            
            # Check specifically for this user's records
            if hasattr(user, '_is_service_user') and user._is_service_user:
                specific_records = MediaStreamHistory.query.filter_by(user_media_access_uuid=user.uuid).all()
                current_app.logger.info(f"DEBUG HISTORY: Specific records for user_media_access_uuid {user.uuid}: {len(specific_records)}")
                for record in specific_records:
                    current_app.logger.info(f"DEBUG HISTORY: Record ID {record.id}: {record.media_title} at {record.started_at}")
                
                # Check if this user is linked to a local account and if so, what other user_media_access_uuids exist for that local account
                if access.user_app_access_id:
                    current_app.logger.info(f"DEBUG HISTORY: This service account is linked to local user_app_access_id: {access.user_app_access_id}")
                    # Find all UserMediaAccess records for this local user
                    all_user_accesses = UserMediaAccess.query.filter_by(user_app_access_id=access.user_app_access_id).all()
                    current_app.logger.info(f"DEBUG HISTORY: All UserMediaAccess records for local user:")
                    for ua in all_user_accesses:
                        current_app.logger.info(f"DEBUG HISTORY: - UserMediaAccess ID {ua.id}: {ua.server.server_nickname} ({ua.server.service_type.value}) - {ua.external_username}")
                        # Check how many history records exist for each
                        count = MediaStreamHistory.query.filter_by(user_media_access_uuid=ua.uuid).count()
                        current_app.logger.info(f"DEBUG HISTORY:   -> Has {count} streaming history records")
            else:
                specific_records = MediaStreamHistory.query.filter_by(user_app_access_uuid=user.uuid).all()
                current_app.logger.info(f"DEBUG HISTORY: Specific records for user_app_access_uuid {user.uuid}: {len(specific_records)}")
                for record in specific_records:
                    current_app.logger.info(f"DEBUG HISTORY: Record ID {record.id}: {record.media_title} at {record.started_at}")
            
    # Get user service types and server names for service-aware display
    user_service_types = {}
    user_server_names = {}
    # For standalone service users, we already have the access records from above
    # Use UUID as key to match template expectations
    user_service_types[user.uuid] = []
    user_server_names[user.uuid] = []
    for access_record in user_access_records:
        if access_record.server.service_type not in user_service_types[user.uuid]:
            user_service_types[user.uuid].append(access_record.server.service_type)
        if access_record.server.server_nickname not in user_server_names[user.uuid]:
            user_server_names[user.uuid].append(access_record.server.server_nickname)

    if request.headers.get('HX-Request') and tab == 'history':
        # UUID is already available on user objects for the delete function
            
        return render_template('user/partials/history_tab_content.html', 
                             user=user, 
                             history_logs=stream_history_pagination,
                             kavita_reading_stats=kavita_reading_stats,
                             kavita_reading_history=kavita_reading_history,
                             user_service_types=user_service_types,
                             user_server_names=user_server_names)
        
    return render_template(
        'user/profile.html',
        title=f"User Profile: {user.get_display_name()}",
        user=user,
        form=form,
        history_logs=stream_history_pagination,
        kavita_reading_stats=kavita_reading_stats,
        kavita_reading_history=kavita_reading_history,
        active_tab=tab,
        is_admin=check_if_user_is_admin(user),
        is_service_user=True,
        server=server,
        stream_stats=stream_stats,
        user_service_types=user_service_types,
        user_server_names=user_server_names,  # Add this context variable
        linked_user_app_access=linked_user_app_access,  # Add linked UserAppAccess info
        now_utc=datetime.now(timezone.utc)
    )


@bp.route('/<username>/delete_history', methods=['POST'])
@bp.route('/<server_nickname>/<server_username>/delete_history', methods=['POST'])
@login_required
@permission_required('edit_user') # Or a more specific permission if you add one
def delete_stream_history(username=None, server_nickname=None, server_username=None):
    # Determine if this is a local user or service user based on parameters
    if username and not server_nickname and not server_username:
        # Local user route
        user = UserAppAccess.query.filter_by(username=urllib.parse.unquote(username)).first()
        if not user:
            current_app.logger.error(f"Local user not found: {username}")
            return make_response("<!-- error -->", 400)
        actual_id = user.id
        user_type = "user_app_access"
    elif server_nickname and server_username:
        # Service user route
        from app.models_media_services import MediaServer
        server = MediaServer.query.filter_by(server_nickname=urllib.parse.unquote(server_nickname)).first()
        if not server:
            current_app.logger.error(f"Server not found: {server_nickname}")
            return make_response("<!-- error -->", 400)
        
        access = UserMediaAccess.query.filter_by(
            server_id=server.id,
            external_username=urllib.parse.unquote(server_username)
        ).first()
        if not access:
            current_app.logger.error(f"Service user not found: {server_username} on {server_nickname}")
            return make_response("<!-- error -->", 400)
        actual_id = access.id
        user_type = "user_media_access"
    else:
        current_app.logger.error(f"Invalid parameters for delete_stream_history")
        return make_response("<!-- error -->", 400)
    
    history_ids_to_delete = request.form.getlist('history_ids[]')
    if not history_ids_to_delete:
        # This can happen if the form is submitted with no boxes checked
        return make_response("<!-- no-op -->", 200)

    try:
        # Convert IDs to integers for safe querying
        ids_as_int = [int(id_str) for id_str in history_ids_to_delete]
        
        # Perform the bulk delete based on user type
        if user_type == "user_app_access":
            # For linked users, delete by user_app_access_uuid
            # Need to get the UUID from the ID
            user_app_access = UserAppAccess.query.get(actual_id)
            if user_app_access:
                num_deleted = db.session.query(MediaStreamHistory).filter(
                    MediaStreamHistory.user_app_access_uuid == user_app_access.uuid,
                    MediaStreamHistory.id.in_(ids_as_int)
                ).delete(synchronize_session=False)
            else:
                num_deleted = 0
        else:  # user_media_access
            # For standalone service users, delete by user_media_access_uuid
            num_deleted = db.session.query(MediaStreamHistory).filter(
                MediaStreamHistory.user_media_access_uuid == access.uuid,
                MediaStreamHistory.id.in_(ids_as_int)
            ).delete(synchronize_session=False)
        
        db.session.commit()
        
        current_app.logger.info(f"Admin {current_user.id} deleted {num_deleted} history entries for {user_type} user {actual_id}.")
        
        # This payload will show a success toast.
        toast_payload = {
            "showToastEvent": {
                "message": f"Successfully deleted {num_deleted} history entries.",
                "category": "success"
            }
        }
        
        # This will trigger both the toast and a custom event to refresh the table.
        # Note: We now use htmx.trigger() in the template itself for a cleaner flow.
        response = make_response("", 200)
        response.headers['HX-Trigger'] = json.dumps(toast_payload)
        
        return response

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting stream history for user {user_id}: {e}", exc_info=True)
        # Send an error toast on failure
        toast_payload = {
            "showToastEvent": {
                "message": "Error deleting history records.",
                "category": "error"
            }
        }
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast_payload)
        return response

@bp.route('/<username>/reset_password', methods=['GET', 'POST'])
@bp.route('/<server_nickname>/<server_username>/reset_password', methods=['GET', 'POST'])
@login_required
@permission_required('edit_user')
def reset_password(username=None, server_nickname=None, server_username=None):
    # Determine if this is a local user or service user based on parameters
    if username and not server_nickname and not server_username:
        # Local user route
        user = UserAppAccess.query.filter_by(username=urllib.parse.unquote(username)).first_or_404()
    elif server_nickname and server_username:
        # Service user route - get the associated UserAppAccess if it exists
        from app.models_media_services import MediaServer
        server = MediaServer.query.filter_by(server_nickname=urllib.parse.unquote(server_nickname)).first_or_404()
        
        media_access = UserMediaAccess.query.filter_by(
            server_id=server.id,
            external_username=urllib.parse.unquote(server_username)
        ).first_or_404()
        
        if media_access.user_app_access_id:
            user = UserAppAccess.query.get_or_404(media_access.user_app_access_id)
        else:
            flash('Password reset is only available for local user accounts.', 'danger')
            return redirect(url_for('users.list'))
    else:
        abort(400)
    
    # Only allow reset for local accounts created through invites (have password_hash and used_invite_id)
    if not user.password_hash or not user.used_invite_id:
        flash('Password reset is only available for local user accounts created through invites.', 'danger')
        # Need to determine the correct route based on user type and server info
        # For now, redirect to users list since we can't easily determine the username route
        return redirect(url_for('users.list'))
    
    form = UserResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            user.set_password(form.new_password.data)
            db.session.commit()
            
            log_event(EventType.SETTING_CHANGE, f"Password was reset for user '{user.get_display_name()}'.", user_id=user.id, admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset successfully.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('users/partials/reset_password_modal.html', form=form, user=user), 422
    
    # For GET request, just render the form
    return render_template('users/partials/reset_password_modal.html', form=form, user=user)

@bp.route('/profile')
@login_required
def profile():
    """User profile page - handles both self-profile and admin viewing other users"""
    
    # Check if this is an admin viewing another user's profile
    username = request.args.get('username')
    
    if username:
        # Only Owners can view other users' profiles
        if not isinstance(current_user, Owner):
            flash('Access denied. You do not have permission to view user profiles.', 'danger')
            return redirect(url_for('dashboard.index'))
        
        # This is Owner viewing another user - redirect to proper route
        # You mentioned it should go to /user/plex/username, so let's redirect there
        return redirect(url_for('user.view_plex_user', username=username, 
                               back=request.args.get('back', 'users'),
                               back_view=request.args.get('back_view', 'cards')))
    
    else:
        # User viewing their own profile
        # Ensure this is an AppUser (local user account)
        if not isinstance(current_user, UserAppAccess):
            flash('Access denied. Please log in with a valid user account.', 'danger')
            return redirect(url_for('auth.app_login'))
    
    # Get the active tab from the URL query, default to 'profile'
    tab = request.args.get('tab', 'profile')
    
    # Get linked service accounts if any
    linked_accounts = []
    if isinstance(current_user, UserAppAccess):
        # This is a UserAppAccess, get linked media access accounts directly
        linked_accounts = current_user.media_accesses
    else:
        # No linked accounts for other user types
        linked_accounts = []
    
    # Re-query the user to avoid detached instance issues
    user = db.session.get(UserAppAccess, current_user.id)
    if not user:
        abort(404)
    
    return render_template(
        'admin/account_settings.html',
        title="Account Settings",
        user=user
    )

@bp.route('/<username>')
@login_required
@permission_required('view_user')
def view_app_user(username):
    """Admin view of a specific app user profile by username"""
    # URL decode the username to handle special characters
    try:
        username = urllib.parse.unquote(username)
    except Exception as e:
        current_app.logger.warning(f"Error decoding username parameter: {e}")
        abort(400)
    
    # Validate username
    if not username:
        abort(400)
    
    # Check for potential conflicts with server nicknames
    from app.models_media_services import MediaServer
    server_conflict = MediaServer.query.filter_by(server_nickname=username).first()
    if server_conflict:
        current_app.logger.warning(f"Potential conflict: app username '{username}' matches server nickname")
    
    user_app_access = UserAppAccess.query.filter_by(username=username).first_or_404()
    
    # Get the active tab from the URL query, default to 'profile'
    tab = request.args.get('tab', 'profile')
    
    # Get linked media access accounts
    linked_accounts = user_app_access.media_accesses
    
    # Create context variables that the template expects (for local users)
    user_service_types = {}
    user_server_names = {}
    
    # For local users, collect service types from their linked accounts
    if linked_accounts:
        service_types = []
        server_names = []
        for access in linked_accounts:
            if access.server:
                if access.server.service_type not in service_types:
                    service_types.append(access.server.service_type)
                if access.server.server_nickname not in server_names:
                    server_names.append(access.server.server_nickname)
        
        user_service_types[user_app_access.id] = service_types
        user_server_names[user_app_access.id] = server_names
    
    # Add stream stats for local users
    from app.services import user_service
    try:
        # Create prefixed user ID for service calls
        # Use UUID for user identification
        user_uuid = user_app_access.uuid
        stream_stats = user_service.get_user_stream_stats(user_uuid)
        last_ip_map = user_service.get_bulk_last_known_ips([user_uuid])
        
        # Attach stats to the user object
        user_app_access.stream_stats = stream_stats
        user_app_access.total_plays = stream_stats.get('global', {}).get('all_time_plays', 0)
        
        # Ensure total_duration is a number for the format_duration filter
        duration_value = stream_stats.get('global', {}).get('all_time_duration_seconds', 0)
        if isinstance(duration_value, str):
            try:
                user_app_access.total_duration = int(duration_value)
            except (ValueError, TypeError):
                user_app_access.total_duration = 0
        else:
            user_app_access.total_duration = duration_value or 0
            
        user_app_access.last_known_ip = last_ip_map.get(prefixed_user_id, 'N/A')
    except Exception as e:
        current_app.logger.error(f"Error getting stream stats for local user {user_app_access.id}: {e}")
        # Provide empty stats as fallback
        user_app_access.stream_stats = {'global': {}, 'players': []}
        user_app_access.total_plays = 0
        user_app_access.total_duration = 0
        user_app_access.last_known_ip = 'N/A'
    
    # Create a form object for the settings tab
    from app.forms import UserEditForm
    form = UserEditForm()
    
    # Get aggregated streaming history for history tab
    streaming_history = []
    if tab == 'history':
        # Get filter parameters
        service_filter = request.args.get('service', 'all')
        days_filter = int(request.args.get('days', 30))
        
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_filter)
        
        # Get streaming history for this local user
        history_query = MediaStreamHistory.query.filter(
            MediaStreamHistory.user_app_access_uuid == user_app_access.uuid,
            MediaStreamHistory.started_at >= start_date,
            MediaStreamHistory.started_at <= end_date
        )
        
        # Apply service filter if specified
        if service_filter != 'all':
            # Join with UserMediaAccess and MediaServer to filter by service type
            history_query = history_query.join(
                UserMediaAccess, 
                MediaStreamHistory.user_media_access_uuid == UserMediaAccess.uuid
            ).join(
                UserMediaAccess.server
            ).filter(
                UserMediaAccess.server.has(service_type=ServiceType(service_filter))
            )
        
        # Order by most recent first and limit to 100 entries
        streaming_history = history_query.order_by(
            MediaStreamHistory.started_at.desc()
        ).limit(100).all()
        
        # Enhance history entries with service account info
        for entry in streaming_history:
            current_app.logger.info(f"DEBUG LOCAL HISTORY: Processing entry - user_media_access_uuid: {entry.user_media_access_uuid}, server_id: {getattr(entry, 'server_id', 'N/A')}")
            
            if entry.user_media_access_uuid:
                # Get the service account that was used for this stream
                service_access = UserMediaAccess.query.filter_by(uuid=entry.user_media_access_uuid).first()
                if service_access:
                    entry.service_account = service_access
                    entry.service_type = service_access.server.service_type.value if service_access.server else 'unknown'
                    entry.server_name = service_access.server.server_nickname if service_access.server else 'Unknown Server'
                    entry.service_username = service_access.external_username or 'Unknown'
                    current_app.logger.info(f"DEBUG LOCAL HISTORY: Found service account - type: {entry.service_type}, server: {entry.server_name}, username: {entry.service_username}")
                else:
                    current_app.logger.warning(f"DEBUG LOCAL HISTORY: No service account found for user_media_access_uuid: {entry.user_media_access_uuid}")
                    entry.service_account = None
                    entry.service_type = 'unknown'
                    entry.server_name = 'Unknown Server'
                    entry.service_username = 'Unknown'
            elif hasattr(entry, 'server_id') and entry.server_id:
                # Try to find service account by server_id for this local user
                current_app.logger.info(f"DEBUG LOCAL HISTORY: No user_media_access_uuid, trying server_id: {entry.server_id}")
                service_access = UserMediaAccess.query.filter_by(
                    user_app_access_id=user_app_access.id,
                    server_id=entry.server_id
                ).first()
                if service_access:
                    entry.service_account = service_access
                    entry.service_type = service_access.server.service_type.value if service_access.server else 'unknown'
                    entry.server_name = service_access.server.server_nickname if service_access.server else 'Unknown Server'
                    entry.service_username = service_access.external_username or 'Unknown'
                    current_app.logger.info(f"DEBUG LOCAL HISTORY: Found service account by server_id - type: {entry.service_type}, server: {entry.server_name}, username: {entry.service_username}")
                else:
                    current_app.logger.warning(f"DEBUG LOCAL HISTORY: No service account found for server_id: {entry.server_id}")
                    entry.service_account = None
                    entry.service_type = 'unknown'
                    entry.server_name = 'Unknown Server'
                    entry.service_username = 'Unknown'
            else:
                current_app.logger.warning(f"DEBUG LOCAL HISTORY: No user_media_access_uuid or server_id available")
                entry.service_account = None
                entry.service_type = 'unknown'
                entry.server_name = 'Unknown Server'
                entry.service_username = 'Unknown'
    
    return render_template(
        'user/profile.html',
        title=f"App User Profile: {user_app_access.get_display_name()}",
        user=user_app_access,
        active_tab=tab,
        is_local_user=True,
        linked_accounts=linked_accounts,
        user_service_types=user_service_types,
        user_server_names=user_server_names,
        form=form,
        streaming_history=streaming_history,
        service_filter=request.args.get('service', 'all'),
        days_filter=int(request.args.get('days', 30)),
        now_utc=datetime.now(timezone.utc)
    )


@bp.route('/app_user/<username>/reset_password', methods=['GET', 'POST'])
@login_required
@permission_required('edit_user')
def reset_app_user_password(username):
    """Reset password for an app user (admin only)"""
    # URL decode the username to handle special characters
    try:
        username = urllib.parse.unquote(username)
    except Exception as e:
        current_app.logger.warning(f"Error decoding username parameter: {e}")
        abort(400)
    
    # Validate username
    if not username:
        abort(400)
    
    user_app_access = UserAppAccess.query.filter_by(username=username).first_or_404()
    
    from app.forms import UserResetPasswordForm
    form = UserResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            user_app_access.set_password(form.new_password.data)
            user_app_access.updated_at = datetime.utcnow()
            db.session.commit()
            
            log_event(EventType.SETTING_CHANGE, f"Password was reset for app user '{user_app_access.get_display_name()}'.", admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset successfully.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('user/partials/modals/reset_password_modal.html', form=form, user=user_app_access), 422
    
    # For GET request, just render the form
    return render_template('user/partials/modals/reset_password_modal.html', form=form, user=user_app_access)

@bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    """User account management page - similar to admin account page"""
    # Ensure this is a regular user, not an owner
    if isinstance(current_user, Owner):
        return redirect(url_for('settings.account'))
    
    # Ensure this is a UserAppAccess (user account)
    if not isinstance(current_user, UserAppAccess):
        flash('Access denied. Please log in with a valid user account.', 'danger')
        return redirect(url_for('auth.app_login'))
    
    from app.forms import ChangePasswordForm, TimezonePreferenceForm
    from app.models import UserPreferences
    
    # Initialize forms
    change_password_form = ChangePasswordForm()
    timezone_form = TimezonePreferenceForm()
    
    # Get user preferences (create if doesn't exist)
    # Note: UserPreferences is designed for admin_id, but we'll adapt it
    user_prefs = UserPreferences.get_timezone_preference(current_user.id)
    timezone_form.timezone_preference.data = user_prefs.get('preference', 'local')
    timezone_form.time_format.data = user_prefs.get('time_format', '12')
    
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'change_password' and change_password_form.validate_on_submit():
            # Verify current password
            if current_user.check_password(change_password_form.current_password.data):
                current_user.set_password(change_password_form.new_password.data)
                db.session.commit()
                log_event(EventType.ADMIN_PASSWORD_CHANGE, f"User '{current_user.get_display_name()}' changed their password.", user_id=current_user.id)
                flash('Password changed successfully.', 'success')
            else:
                flash('Current password is incorrect.', 'danger')
        
        elif form_type == 'timezone' and timezone_form.validate_on_submit():
            UserPreferences.set_timezone_preference(
                current_user.id,
                timezone_form.timezone_preference.data,
                timezone_form.local_timezone.data,
                timezone_form.time_format.data
            )
            flash('Timezone preferences updated successfully.', 'success')
        
        return redirect(url_for('user.account'))
    
    return render_template('user/account.html',
                         title="My Account",
                         user=current_user,
                         change_password_form=change_password_form,
                         timezone_form=timezone_form)