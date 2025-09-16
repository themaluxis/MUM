"""
Invites module package following Flask blueprint best practices

This package contains all invite management functionality split into focused modules:
- main: Core invite processing and public functionality
- manage: Admin management (list, create, toggle, delete)
- auth: Authentication initiation (Plex and Discord)
- callbacks: OAuth callback handlers
- edit: Invite editing functionality  
- bulk_operations: Bulk operations on multiple invites
"""

from flask import Blueprint

# Create the main invites blueprint
invites_bp = Blueprint("invites", __name__)

# Import submodules so their routes are registered to the blueprint
from . import main
from . import manage
from . import auth
from . import callbacks
from . import edit
from . import bulk_operations