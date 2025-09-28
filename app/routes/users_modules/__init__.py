# File: app/routes/user_modules/__init__.py
"""
Users module package following Flask blueprint best practices

This package contains all user management functionality split into focused modules:
- main: Core user listing and view preferences  
- sync: User synchronization with external services
- delete: User deletion operations
- mass_edit: Bulk operations on multiple users
- linking: Account linking/unlinking functionality
- api: API endpoints for user management
- debug: Debug and quick edit functionality
- helpers: Shared utility functions and classes
"""

from flask import Blueprint

# Create the main users blueprint
users_bp = Blueprint("users", __name__)

# Import submodules so their routes are registered to the blueprint
from . import main
from . import sync
from . import delete
from . import mass_edit
from . import linking
from . import api
from . import debug
from . import helpers
from . import history