# File: app/routes/media_servers.py
from flask import Blueprint, redirect, url_for

# This file is now just a re-exporter that provides backwards compatibility
# Import both blueprints for use in app/__init__.py
from app.routes.media_servers_modules.setup import bp as media_servers_setup_bp
from app.routes.media_servers_modules.admin import bp as media_servers_admin_bp

# Export both blueprints for registration in app/__init__.py
bp_setup = media_servers_setup_bp
bp_admin = media_servers_admin_bp