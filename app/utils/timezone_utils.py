# File: app/utils/timezone_utils.py
import os
import pytz
from datetime import datetime, timezone
from typing import Optional

def get_app_timezone():
    """Get the application timezone from TZ environment variable or default to UTC."""
    tz_name = os.environ.get('TZ', 'UTC')
    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        # Fallback to UTC if invalid timezone
        return pytz.UTC

def now():
    """Get current datetime in the application's configured timezone."""
    app_tz = get_app_timezone()
    return datetime.now(app_tz)

def utcnow():
    """Get current datetime in UTC (for database storage)."""
    return datetime.now(timezone.utc)

def to_app_timezone(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a datetime to the application's timezone."""
    if dt is None:
        return None
    
    app_tz = get_app_timezone()
    
    # If datetime is naive, assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt.astimezone(app_tz)

def format_datetime(dt: Optional[datetime], format_str: str = "%Y-%m-%d %H:%M", show_timezone: bool = True) -> str:
    """Format datetime in the application's timezone."""
    if dt is None:
        return "N/A"
    
    # Convert to app timezone for display
    local_dt = to_app_timezone(dt)
    
    if show_timezone:
        # Add timezone abbreviation
        tz_abbr = local_dt.strftime('%Z')
        if not tz_abbr:  # Some timezones don't have abbreviations
            tz_abbr = str(local_dt.tzinfo)
        return f"{local_dt.strftime(format_str)} {tz_abbr}"
    else:
        return local_dt.strftime(format_str)

def format_datetime_human(dt: Optional[datetime], include_time: bool = True) -> str:
    """Format datetime in a human-readable format using app timezone."""
    if dt is None:
        return "N/A"
    
    local_dt = to_app_timezone(dt)
    
    if include_time:
        return format_datetime(local_dt, "%Y-%m-%d %I:%M %p", show_timezone=True)
    else:
        return format_datetime(local_dt, "%Y-%m-%d", show_timezone=False)