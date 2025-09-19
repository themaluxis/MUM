# User Module

This module handles individual user functionality including dashboard, profile management, account settings, and streaming history.

## Structure
- main.py: Core dashboard and index functionality  
- profile.py: User profile viewing and editing
- account.py: User account management and settings
- history.py: Streaming history and deletion operations
- overseerr.py: Overseerr integration and requests
- helpers.py: Shared utilities and helper functions

## Adding New Features
1. Create new .py file in this directory
2. Import the shared blueprint: `from . import user_bp`
3. Add routes using `@user_bp.route()`
4. Update this README and the __init__.py imports