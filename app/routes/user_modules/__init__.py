from flask import Blueprint

# Create the main user blueprint
user_bp = Blueprint("user", __name__, url_prefix='/user')

# Import submodules so their routes are registered to the blueprint
from . import main
from . import profile
from . import account
from . import history
from . import overseerr
from . import helpers