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
            'dashboard.settings',
            # Plugin management endpoints should work even when setup is not complete
            'plugins.enable_plugin', 'plugins.disable_plugin', 'plugins.reload_plugins',
            'plugins.install_plugin', 'plugins.uninstall_plugin', 'setup.plugins'
        ]
        
        # Also bypass if the endpoint starts with 'dashboard.' or 'media_servers.' or 'plugin_management.'
        if (request.endpoint in bypass_endpoints or 
            (request.endpoint and (request.endpoint.startswith('dashboard.') or 
                                 request.endpoint.startswith('media_servers.') or
                                 request.endpoint.startswith('setup.') or
                                 request.endpoint.startswith('plugin_management.')))):
            return f(*args, **kwargs)
        
        # We also check that we are not already on a setup page to avoid redirect loops.
        if request.endpoint and not request.endpoint.startswith('setup.'):
            flash("Application setup is not complete. Please follow the steps below.", "warning")
            return redirect(url_for('auth.app_login'))
        
        # If we are already on a setup page (like /setup/plex), allow it to run
        # so the user can complete the setup process.
        return f(*args, **kwargs)
    return decorated_function


def log_event(event_type, message: str, details: dict = None, # Removed type hint for EventType to avoid import here
              admin_id: int = None, user_id = None, invite_id: int = None):
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
            from app.models import Owner # Local import
            if isinstance(current_user, Owner):
                log_entry.admin_id = current_user.id
        elif admin_id: # Ensure explicitly passed admin_id is used
             log_entry.admin_id = admin_id


        if user_id: 
            # Handle UUID or numeric user ID
            try:
                if isinstance(user_id, str) and len(str(user_id)) > 10:
                    # Likely a UUID, try to get the user and extract numeric ID
                    user_obj, user_type = get_user_by_uuid(str(user_id))
                    # Only store local user IDs in the log
                    if user_obj and user_type == "user_app_access":
                        log_entry.user_id = user_obj.id
                else:
                    # Assume it's already a numeric ID (backward compatibility)
                    log_entry.user_id = int(user_id)
            except Exception as e:
                current_app.logger.warning(f"Invalid user_id format in log_event: {user_id}: {e}")
                # Don't set user_id if parsing fails
        if invite_id: log_entry.invite_id = invite_id

        db.session.add(log_entry)
        db.session.commit()
        # Event logged to database
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
            
            # Import user types locally to avoid circular imports
            from app.models import Owner, UserAppAccess
            
            # Owner always has all permissions
            if isinstance(current_user, Owner):
                return f(*args, **kwargs)
            
            # Check permissions for UserAppAccess (role-based)
            if isinstance(current_user, UserAppAccess):
                if current_user.has_permission(permission_name):
                    return f(*args, **kwargs)
            
            # Other user types don't have admin permissions
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for('dashboard.index'))
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
            
            # Import user types locally to avoid circular imports
            from app.models import Owner, UserAppAccess
            
            # Owner always has all permissions
            if isinstance(current_user, Owner):
                return f(*args, **kwargs)
            
            # Check if UserAppAccess has ANY of the permissions in the list
            if isinstance(current_user, UserAppAccess):
                for perm in permissions:
                    if current_user.has_permission(perm):
                        return f(*args, **kwargs)
            
            # If no permissions found, deny access
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

def get_user_by_uuid(user_uuid):
    """Get user (either type) by uuid"""
    from app.models import UserAppAccess
    from app.models_media_services import UserMediaAccess
    
    # Try UserMediaAccess first
    user = UserMediaAccess.query.filter_by(uuid=user_uuid).first()
    if user:
        return user, 'user_media_access'
    
    # Try UserAppAccess
    user = UserAppAccess.query.filter_by(uuid=user_uuid).first()
    if user:
        return user, 'user_app_access'
    
    return None, None
    
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

def format_json(data):
    """Format JSON data with proper indentation for display"""
    try:
        import json
        # If it's already a dict/list, format it directly
        if isinstance(data, (dict, list)):
            return json.dumps(data, indent=2, ensure_ascii=False)
        # If it's a string, try to parse it first
        elif isinstance(data, str):
            parsed = json.loads(data)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        else:
            # For other types, convert to string
            return str(data)
    except (json.JSONDecodeError, TypeError):
        # If it's not valid JSON, return as-is
        return str(data) if data is not None else ""

def extract_jellyfin_user_info(raw_data_str):
    """Extract Jellyfin user ID and PrimaryImageTag from raw JSON string"""
    try:
        import json
        import re
        
        if not raw_data_str or not raw_data_str.startswith('{'):
            return None, None
            
        # Try to parse as JSON first
        try:
            data = json.loads(raw_data_str)
            user_id = data.get('Id')
            primary_image_tag = data.get('PrimaryImageTag')
            return user_id, primary_image_tag
        except json.JSONDecodeError:
            # Fallback to regex extraction if JSON parsing fails
            id_match = re.search(r'"Id"\s*:\s*"([^"]+)"', raw_data_str)
            tag_match = re.search(r'"PrimaryImageTag"\s*:\s*"([^"]+)"', raw_data_str)
            
            user_id = id_match.group(1) if id_match else None
            primary_image_tag = tag_match.group(1) if tag_match else None
            
            return user_id, primary_image_tag
            
    except Exception as e:
        return None, None

def super_admin_required(f):
    """
    Decorator to ensure only the super admin (user ID 1) can access certain routes.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Import user types locally to avoid circular imports
        from app.models import Owner, UserAppAccess
        
        if not current_user.is_authenticated:
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for('dashboard.index'))
        
        # For Owner, check if ID is 1
        if isinstance(current_user, Owner):
            if current_user.id == 1:
                return f(*args, **kwargs)
        # For UserAppAccess, check if ID is 1 (super admin local user)
        elif isinstance(current_user, UserAppAccess):
            if current_user.id == 1:
                return f(*args, **kwargs)
        
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for('dashboard.index'))
    return decorated_function


def get_user_profile_url(user, **kwargs):
    """
    Generate the correct profile URL for any user type.
    
    Args:
        user: AppUser or ServiceAccount instance
        **kwargs: Additional URL parameters (tab, back, back_view, etc.)
    
    Returns:
        str: The appropriate URL for the user's profile
    """
    from flask import url_for
    from app.models import UserAppAccess
    import urllib.parse
    
    if isinstance(user, UserAppAccess):
        # URL encode the username to handle special characters
        encoded_username = urllib.parse.quote(user.username, safe='')
        return url_for('user.view_app_user', username=encoded_username, **kwargs)
    else:
        # Service Account - need to determine server and username
        server_info = get_primary_server_for_user(user)
        if server_info:
            server_name, username = server_info
            # URL encode both server nickname and username
            encoded_server_name = urllib.parse.quote(server_name, safe='')
            encoded_username = urllib.parse.quote(username, safe='')
            return url_for('user.view_service_account', 
                          server_nickname=encoded_server_name, 
                          server_username=encoded_username, 
                          **kwargs)
    return None


def get_primary_server_for_user(service_account):
    """
    Get the primary server and username for a service account.
    
    Args:
        service_account: ServiceAccount instance
        
    Returns:
        tuple: (server_name, username) or None if no server found
    """
    from app.models_media_services import UserMediaAccess
    
    # Get the first server this user has access to
    user_access = UserMediaAccess.query.filter_by(service_account_id=service_account.id).first()
    if not user_access:
        return None
    
    server = user_access.server
    server_name = server.name
    
    # Extract the appropriate username for this server
    username = extract_username_for_server(service_account, server)
    
    return (server_name, username)


def extract_username_for_server(service_account, server):
    """
    Extract the appropriate username for a specific server.
    
    Args:
        service_account: ServiceAccount instance
        server: MediaServer instance
        
    Returns:
        str: The username to use for this server
    """
    from app.models_media_services import UserMediaAccess
    
    # First, try to get the clean username from UserMediaAccess
    user_access = UserMediaAccess.query.filter_by(
        service_account_id=service_account.id,
        server_id=server.id
    ).first()
    
    if user_access and user_access.external_username:
        return user_access.external_username
    
    # Fallback to service account username (now universal for all services)
    username = service_account.username
    if '@' in username:
        # Handle any remaining legacy data with @service suffix
        return username.split('@')[0]
    return username


def get_user_type_display(user):
    """
    Get a human-readable display string for the user type.
    
    Args:
        user: AppUser or ServiceAccount instance
        
    Returns:
        str: Display string like "App User" or "Plex User"
    """
    from app.models import UserAppAccess
    
    if isinstance(user, UserAppAccess):
        return "App User"
    else:
        # Service Account - determine service type
        server_info = get_primary_server_for_user(user)
        if server_info:
            server_name, _ = server_info
            return f"{server_name} User"
        return "Service User"


def get_user_servers_and_types(user):
    """
    Get server names and service types for a user.
    
    Args:
        user: UserAppAccess instance or MockUser instance
        
    Returns:
        tuple: (server_names_list, service_types_list)
    """
    from app.models_media_services import UserMediaAccess
    from app.models import UserAppAccess
    
    # Handle UserAppAccess (local users)
    if isinstance(user, UserAppAccess):
        user_access_records = UserMediaAccess.query.filter_by(user_app_access_id=user.id).all()
    # Handle MockUser or service users (check for _user_type attribute)
    elif hasattr(user, '_user_type') and user._user_type == 'service':
        # This is a standalone service user - get their direct access record
        user_access_records = UserMediaAccess.query.filter_by(id=user.id, user_app_access_id=None).all()
    else:
        # Unknown user type or no access records
        return ([], [])
    
    server_names = []
    service_types = []
    
    for access in user_access_records:
        if access.server and access.server.name not in server_names:
            server_names.append(access.server.name)
        if access.server and access.server.service_type not in service_types:
            service_types.append(access.server.service_type)
    
    return (server_names, service_types)


def validate_username_for_routing(username, user_type='app'):
    """
    Validate a username for use in URL routing and check for conflicts.
    
    Args:
        username: The username to validate
        user_type: 'app' for app users, 'server' for server nicknames
        
    Returns:
        dict: {'valid': bool, 'conflicts': list, 'warnings': list}
    """
    result = {
        'valid': True,
        'conflicts': [],
        'warnings': []
    }
    
    # Basic validation
    if not username or not username.strip():
        result['valid'] = False
        result['conflicts'].append('Username cannot be empty')
        return result
    
    username = username.strip()
    
    # Check for problematic characters that could cause URL issues
    problematic_chars = ['/', '\\', '?', '#', '%', '&', '+', ' ']
    found_chars = [char for char in problematic_chars if char in username]
    if found_chars:
        result['warnings'].append(f"Username contains characters that may cause URL issues: {', '.join(found_chars)}")
    
    # Check for conflicts based on user type
    if user_type == 'app':
        # Check if username conflicts with existing server nicknames
        from app.models_media_services import MediaServer
        server_conflict = MediaServer.query.filter_by(name=username).first()
        if server_conflict:
            result['conflicts'].append(f"Username '{username}' conflicts with existing server nickname")
            result['valid'] = False
    
    elif user_type == 'server':
        # Check if server nickname conflicts with existing app usernames
        from app.models import UserAppAccess
        app_user_conflict = UserAppAccess.query.filter_by(username=username).first()
        if app_user_conflict:
            result['conflicts'].append(f"Server nickname '{username}' conflicts with existing app user username")
            result['valid'] = False
    
    # Check for case sensitivity issues
    if user_type == 'app':
        from app.models import UserAppAccess
        case_conflicts = UserAppAccess.query.filter(UserAppAccess.username.ilike(username)).filter(UserAppAccess.username != username).all()
        if case_conflicts:
            conflicting_usernames = [user.username for user in case_conflicts]
            result['warnings'].append(f"Similar usernames exist with different case: {', '.join(conflicting_usernames)}")
    
    return result


def get_safe_username_for_url(username):
    """
    Get a URL-safe version of a username.
    
    Args:
        username: The original username
        
    Returns:
        str: URL-encoded username
    """
    import urllib.parse
    if not username:
        return ''
    return urllib.parse.quote(str(username), safe='')


def resolve_user_route_conflict(path_segment):
    """
    Resolve potential conflicts when a URL path could be either an app user or server nickname.
    
    Args:
        path_segment: The URL path segment to resolve
        
    Returns:
        dict: {'type': 'app_user'|'server'|'ambiguous'|'none', 'user': user_obj, 'server': server_obj}
    """
    from app.models import UserAppAccess
    from app.models_media_services import MediaServer
    import urllib.parse
    
    # URL decode the path segment
    try:
        decoded_segment = urllib.parse.unquote(path_segment)
    except:
        decoded_segment = path_segment
    
    result = {
        'type': 'none',
        'user': None,
        'server': None
    }
    
    # Check for app user
    app_user = UserAppAccess.query.filter_by(username=decoded_segment).first()
    
    # Check for server
    server = MediaServer.query.filter_by(name=decoded_segment).first()
    
    if app_user and server:
        result['type'] = 'ambiguous'
        result['user'] = app_user
        result['server'] = server
    elif app_user:
        result['type'] = 'app_user'
        result['user'] = app_user
    elif server:
        result['type'] = 'server'
        result['server'] = server
    
    return result