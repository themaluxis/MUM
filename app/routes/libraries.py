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
    libraries_by_service = {}
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    
    for server in all_servers:
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            try:
                libs = service.get_libraries()
                for lib in libs:
                    lib['server_name'] = server.name
                    lib['service_type'] = server.service_type.value
                    lib['server_id'] = server.id
                
                # Group by service type
                service_type = server.service_type.value.upper()
                if service_type not in libraries_by_service:
                    libraries_by_service[service_type] = {
                        'servers': {},
                        'total_libraries': 0,
                        'service_display_name': service_type.title()
                    }
                
                # Group by server within service
                if server.name not in libraries_by_service[service_type]['servers']:
                    libraries_by_service[service_type]['servers'][server.name] = {
                        'libraries': [],
                        'server_id': server.id,
                        'online': True  # Could add actual status check here
                    }
                
                libraries_by_service[service_type]['servers'][server.name]['libraries'].extend(libs)
                libraries_by_service[service_type]['total_libraries'] += len(libs)
                
            except Exception as e:
                current_app.logger.error(f"Error getting libraries from {server.name}: {e}")
                # Add offline server to the list
                service_type = server.service_type.value.upper()
                if service_type not in libraries_by_service:
                    libraries_by_service[service_type] = {
                        'servers': {},
                        'total_libraries': 0,
                        'service_display_name': service_type.title()
                    }
                
                if server.name not in libraries_by_service[service_type]['servers']:
                    libraries_by_service[service_type]['servers'][server.name] = {
                        'libraries': [],
                        'server_id': server.id,
                        'online': False,
                        'error': str(e)
                    }

    # Calculate total servers for layout decision
    total_servers = sum(len(service_data['servers']) for service_data in libraries_by_service.values())
    total_services = len(libraries_by_service)
    
    # If only one server/service, flatten the structure for simple display
    simple_libraries = []
    if total_servers == 1 and total_services == 1:
        for service_data in libraries_by_service.values():
            for server_data in service_data['servers'].values():
                for lib in server_data['libraries']:
                    simple_libraries.append(lib)

    return render_template(
        'libraries/index.html',
        title="Libraries",
        libraries_by_service=libraries_by_service,
        simple_libraries=simple_libraries,
        total_servers=total_servers,
        total_services=total_services,
        use_simple_layout=(total_servers == 1 and total_services == 1)
    )