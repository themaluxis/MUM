"""
Centralized timeout management utility
"""
from flask import current_app
from app.models import Setting

def get_api_timeout() -> int:
    """
    Get the centralized API timeout value from settings.
    
    Returns:
        int: Timeout value in seconds (default: 3)
    """
    try:
        timeout = Setting.get('API_TIMEOUT_SECONDS', 3)
        return int(timeout)
    except (ValueError, TypeError):
        current_app.logger.warning("Invalid API_TIMEOUT_SECONDS setting, using default: 3")
        return 3

def get_api_timeout_with_fallback(fallback: int = 3) -> int:
    """
    Get the API timeout with a custom fallback value.
    
    Args:
        fallback (int): Fallback timeout value if setting is invalid
        
    Returns:
        int: Timeout value in seconds
    """
    try:
        timeout = Setting.get('API_TIMEOUT_SECONDS', fallback)
        return int(timeout)
    except (ValueError, TypeError):
        current_app.logger.warning(f"Invalid API_TIMEOUT_SECONDS setting, using fallback: {fallback}")
        return fallback