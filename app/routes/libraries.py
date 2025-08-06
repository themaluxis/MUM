from flask import Blueprint, render_template, current_app
from flask_login import login_required
from app.utils.helpers import setup_required
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory

bp = Blueprint('libraries', __name__)

@bp.route('/libraries')
@login_required
@setup_required
# Optional: Add a new permission check here if desired
# @permission_required('view_libraries')
def index():
    all_libraries = []
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    for server in all_servers:
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            try:
                libs = service.get_libraries()
                for lib in libs:
                    lib['server_name'] = server.name
                    lib['service_type'] = server.service_type.value
                all_libraries.extend(libs)
            except Exception as e:
                current_app.logger.error(f"Error getting libraries from {server.name}: {e}")

    return render_template(
        'libraries/index.html',
        title="Libraries",
        libraries=all_libraries
    )