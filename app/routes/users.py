# File: app/routes/users.py
"""Main users module following Flask blueprint best practices"""

# Import the users blueprint from the user_modules package
# This automatically registers all routes from the submodules
from app.routes.users_modules import users_bp as bp