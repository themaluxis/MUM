# File: app/utils/helpers.py
import re
from datetime import datetime, timezone, timedelta
from app.utils.timezone_utils import to_app_timezone, format_datetime_human as tz_format_datetime_human
from flask import current_app, flash, url_for, g as flask_g, redirect, request # Use flask_g to avoid conflict with local g
from functools import wraps
from flask_login import current_user
# app.models import HistoryLog, EventType # This creates circular import if models also import helpers
# from app.extensions import db # Same here

# It's better to import db and models within the function or pass them if needed,
# or ensure helpers don't directly cause DB interaction at module level.

def is_setup_complete():
    """
    Helper function to check the global setup flag.
    The g.setup_complete flag is set on each request by the before_request hook.
    """
    return getattr(flask_g, 'setup_complete', False)


def setup_required(f):
    """
    Decorator to ensure that the application setup has been completed
    before allowing access to a route. If setup is not complete, it redirects
    the user to the first step of the setup process.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # The 'g.setup_complete' flag is the primary check. If it's true,
        # the user can proceed to the requested page.
        if is_setup_complete():
            return f(*args, **kwargs)

        # If setup is not complete, we need to redirect the user.
        # This check complements the global before_request hook in app/__init__.py
        # and acts as a direct protector on the route.
        
        # Allow dashboard and media server management endpoints to bypass setup check
        # since the app is already configured and we're just managing existing setup
        bypass_endpoints = [
            'dashboard.settings_plugin_configure', 'dashboard.settings_plugins',
            'dashboard.settings'
        ]
        
        # Also bypass if the endpoint starts with 'dashboard.' or 'media_servers.'
        if (request.endpoint in bypass_endpoints or 
            (request.endpoint and (request.endpoint.startswith('dashboard.') or 
                                 request.endpoint.startswith('media_servers.') or
                                 request.endpoint.startswith('setup.')))):
            return f(*args, **kwargs)
        
        # We also check that we are not already on a setup page to avoid redirect loops.
        if request.endpoint and not request.endpoint.startswith('setup.'):
            flash("Application setup is not complete. Please follow the steps below.", "warning")
            return redirect(url_for('setup.account_setup'))
        
        # If we are already on a setup page (like /setup/plex), allow it to run
        # so the user can complete the setup process.
        return f(*args, **kwargs)
    return decorated_function


def log_event(event_type, message: str, details: dict = None, # Removed type hint for EventType to avoid import here
              admin_id: int = None, user_id: int = None, invite_id: int = None):
    """Logs an event to the HistoryLog. Gracefully handles DB not ready."""
    from app.models import HistoryLog, EventType as EventTypeEnum # Local import for models and Enum
    from app.extensions import db # Local import for db
    from flask_login import current_user # Local import for current_user

    if not isinstance(event_type, EventTypeEnum): # Use the imported Enum
        current_app.logger.error(f"Invalid event_type provided to log_event: {event_type}")
        return

    try:
        # Check if HistoryLog table exists before trying to write to it
        # This is especially for early startup/CLI commands like `flask db upgrade`
        engine_conn = None
        history_table_exists = False
        try:
            engine_conn = db.engine.connect()
            history_table_exists = db.engine.dialect.has_table(engine_conn, HistoryLog.__tablename__)
        finally:
            if engine_conn:
                engine_conn.close()

        if not history_table_exists:
            current_app.logger.info(f"History_logs table not found. Skipping log: {event_type.name} - {message}")
            return

        log_entry = HistoryLog(
            event_type=event_type,
            message=message,
            details=details or {}
        )

        if admin_id is None and current_user and current_user.is_authenticated and hasattr(current_user, 'id'):
            from app.models import AdminAccount # Local import
            if isinstance(current_user, AdminAccount):
                log_entry.admin_id = current_user.id
        elif admin_id: # Ensure explicitly passed admin_id is used
             log_entry.admin_id = admin_id


        if user_id: log_entry.user_id = user_id
        if invite_id: log_entry.invite_id = invite_id

        db.session.add(log_entry)
        db.session.commit()
        current_app.logger.info(f"Event logged: {event_type.name} - {message}")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error logging event (original: {event_type.name} - {message}): {e}")

def calculate_expiry_date(days: int) -> datetime | None:
    if days is None or days <= 0: return None
    return datetime.now(timezone.utc) + timedelta(days=days)

def format_datetime_human(dt: datetime | None, include_time=True, naive_as_utc=True) -> str:
    """Format datetime using the application's configured timezone."""
    return tz_format_datetime_human(dt, include_time)

def time_ago(dt: datetime | None, naive_as_utc=True) -> str:
    if dt is None: return "Never"
    dt_aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None and naive_as_utc else dt
    now = datetime.now(timezone.utc)
    diff = now - dt_aware
    if diff.total_seconds() < 0: return "In the future"
    seconds = int(diff.total_seconds()); days = diff.days; months = days // 30; years = days // 365
    if seconds < 60: return "just now"
    elif seconds < 3600: minutes = seconds // 60; return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif seconds < 86400: hours = seconds // 3600; return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif days < 7: return f"{days} day{'s' if days > 1 else ''} ago"
    elif days < 30: weeks = days // 7; return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    elif months < 12: return f"{months} month{'s' if months > 1 else ''} ago"
    else: return f"{years} year{'s' if years > 1 else ''} ago"

def humanize_time(dt):
    """
    Converts a datetime object to a human-readable string.
    e.g., '2 hours ago', '3 days ago', 'in 5 minutes'
    """
    if dt is None:
        return "Never"
    now = datetime.now(dt.tzinfo)
    diff = now - dt
    seconds = diff.total_seconds()
    
    if seconds < 0:
        # Future dates
        seconds = abs(seconds)
        if seconds < 60:
            return "in a few seconds"
        if seconds < 3600:
            return f"in {int(seconds / 60)} minutes"
        if seconds < 86400:
            return f"in {int(seconds / 3600)} hours"
        return f"in {int(seconds / 86400)} days"
    
    # Past dates
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)} minutes ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)} hours ago"
    if seconds < 2592000: # 30 days
        return f"{int(seconds / 86400)} days ago"
    if seconds < 31536000: # 365 days
        return f"{int(seconds / 2592000)} months ago"
    return f"{int(seconds / 31536000)} years ago"


def generate_plex_auth_url(plex_client_id, forward_url, app_name="Multimedia User Manager"):
    from plexapi.myplex import MyPlexAccount # Local import
    try:
        pin_data = MyPlexAccount.get_plex_pin(plex_client_id,product_name=app_name,forwardUrl=forward_url)
        pin_id = pin_data['id']; pin_code = pin_data['code']
        auth_url_with_pin = f"https://app.plex.tv/auth#?clientID={plex_client_id}&code={pin_code}&context[device][product]={app_name.replace(' ', '%20')}"
        return pin_id, auth_url_with_pin
    except Exception as e: current_app.logger.error(f"Error generating Plex PIN: {e}"); return None, None

def check_plex_pin_auth(plex_client_id, pin_id):
    from plexapi.myplex import MyPlexAccount # Local import
    try:
        auth_token = MyPlexAccount.check_plex_pin(plex_client_id, pin_id)
        if auth_token: return auth_token
        return None
    except Exception as e: current_app.logger.error(f"Error checking Plex PIN: {e}"); return None

def sanitize_filename(filename: str) -> str:
    if not filename: return "untitled"
    filename = filename.split('/')[-1].split('\\')[-1]
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    filename = re.sub(r'__+', '_', filename)
    filename = filename.strip('_.-')
    if not filename: return "sanitized_file"
    return filename

def permission_required(permission_name):
    """Decorator to check if a logged-in user has a specific permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.app_login'))
            if not current_user.has_permission(permission_name):
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for('dashboard.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def any_permission_required(permissions):
    """
    Checks if a user has at least one of the permissions in the provided list.
    'permissions' should be a list of permission name strings.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.app_login'))
            # Super admin always passes
            if current_user.id == 1:
                return f(*args, **kwargs)
            # Check if user has ANY of the permissions in the list
            for perm in permissions:
                if current_user.has_permission(perm):
                    return f(*args, **kwargs)
            # If loop finishes without returning, user has none of the permissions
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for('dashboard.index'))
        return decorated_function
    return decorator

def get_text_color_for_bg(hex_color):
    """
    Determines if black or white text is more readable on a given hex background color.
    Returns '#FFFFFF' for white or '#000000' for black.
    """
    if not hex_color or len(hex_color) != 7:
        return '#FFFFFF' # Default to white for invalid colors
    try:
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        # Formula for perceived brightness (luminance)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return '#000000' if luminance > 0.5 else '#FFFFFF'
    except Exception:
        return '#FFFFFF' # Fallback
    
def format_duration(total_seconds):
    """Formats a duration in seconds into a human-readable string like '1d 4h 5m'."""
    if not total_seconds or total_seconds < 0:
        return "0m"
    
    delta = timedelta(seconds=int(total_seconds))
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts: # Always show minutes if no other parts
        parts.append(f"{minutes}m")
        
    return " ".join(parts[:3]) # Show at most 3 parts (e.g., d, h, m)