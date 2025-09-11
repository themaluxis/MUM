# File: app/routes/user_modules/mass_edit.py
"""Mass editing and purge functionality for users"""

from flask import render_template, request, current_app, session, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.models import UserAppAccess, Owner
from app.models_media_services import MediaStreamHistory, UserMediaAccess, MediaServer
from app.forms import MassUserEditForm
from app.extensions import db
from app.utils.helpers import setup_required, permission_required
from app.services import user_service
from app.services.media_service_manager import MediaServiceManager
from app.routes.user_modules.helpers import MassEditMockUser
from . import users_bp
import json


@users_bp.route('/mass_edit_libraries_form')
@login_required
def mass_edit_libraries_form():
    """Get the mass edit libraries form for selected users"""
    current_app.logger.info("=== USERS PAGE: mass_edit_libraries_form() called - USING DATABASE DATA ===")
    user_ids_str = request.args.get('user_ids', '')
    current_app.logger.info(f"DEBUG: Raw user_ids_str received: '{user_ids_str}'")
    if not user_ids_str:
        return '<div class="alert alert-error">No users selected.</div>'
    
    # Parse user IDs - handle both prefixed and plain numeric IDs
    service_user_ids = []
    current_app.logger.info(f"DEBUG: Processing user IDs: {user_ids_str.split(',')}")
    
    for uid_str in user_ids_str.split(','):
        uid_str = uid_str.strip()
        current_app.logger.info(f"DEBUG: Processing individual ID: '{uid_str}'")
        if not uid_str:
            continue
        
        # Try to parse as UUID first
        try:
            from app.utils.helpers import get_user_by_uuid
            user_obj, user_type = get_user_by_uuid(uid_str)
            
            if user_obj and user_type == "user_media_access":
                service_user_ids.append(user_obj.id)
            elif user_obj and user_type == "user_app_access":
                current_app.logger.warning(f"Mass edit libraries attempted on local user {uid_str} - not supported")
            else:
                current_app.logger.warning(f"No user found for uuid {uid_str}")
        except Exception as e:
            current_app.logger.info(f"DEBUG: Failed to parse as uuid: {e}")
            current_app.logger.error(f"Invalid user UUID in mass edit libraries: {uid_str} - {e}")
    
    if not service_user_ids:
        return '<div class="alert alert-warning">Mass edit libraries is only available for service users. No valid service users were selected.</div>'
    
    current_app.logger.info(f"DEBUG: Querying for service_user_ids: {service_user_ids}")
    access_records = db.session.query(UserMediaAccess, UserAppAccess, MediaServer).join(
        UserAppAccess, UserMediaAccess.user_app_access_id == UserAppAccess.id, isouter=True
    ).join(
        MediaServer, UserMediaAccess.server_id == MediaServer.id
    ).filter(UserMediaAccess.id.in_(service_user_ids)).all()
    current_app.logger.info(f"DEBUG: Found {len(access_records)} access records")
    
    # Debug each access record
    for i, (access, user, server) in enumerate(access_records):
        current_app.logger.info(f"DEBUG: Record {i}: access={access.id if access else None}, user={user.id if user else None}, server={server.id if server else None}")
        if access:
            current_app.logger.info(f"DEBUG: Record {i} access details: external_username={access.external_username}, server_id={access.server_id}")
        if server:
            current_app.logger.info(f"DEBUG: Record {i} server details: name={server.server_nickname}, service_type={server.service_type}")
        if user:
            current_app.logger.info(f"DEBUG: Record {i} user details: username={user.username}")
        else:
            current_app.logger.info(f"DEBUG: Record {i} user is None (standalone service user)")

    services_data = {}
    for access, user, server in access_records:
        service_type_key = server.service_type.value
        if service_type_key not in services_data:
            services_data[service_type_key] = {
                'service_name': server.service_type.name.capitalize(),
                'servers': {}
            }
        
        if server.id not in services_data[service_type_key]['servers']:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            libraries = []
            for lib in db_libraries:
                libraries.append({
                    'id': lib.external_id,
                    'external_id': lib.external_id,
                    'name': lib.name
                })
            
            services_data[service_type_key]['servers'][server.id] = {
                'server_name': server.server_nickname,
                'users': [],
                'libraries': libraries,
                'current_library_ids': set(access.allowed_library_ids or [])
            }
        
        # Create display name for this user on this server
        display_name = f"{access.external_username} ({server.server_nickname})"
        services_data[service_type_key]['servers'][server.id]['users'].append(display_name)
        
        # Intersect library IDs for users on the same server
        current_ids = services_data[service_type_key]['servers'][server.id]['current_library_ids']
        current_ids.intersection_update(access.allowed_library_ids or [])

    current_app.logger.info(f"DEBUG: Built services_data with {len(services_data)} service types")
    for service_key, service_info in services_data.items():
        current_app.logger.info(f"DEBUG: Service {service_key} has {len(service_info['servers'])} servers")
        for server_id, server_data in service_info['servers'].items():
            current_app.logger.info(f"DEBUG: Server {server_id} ({server_data['server_name']}) has {len(server_data['users'])} users: {server_data['users']}")
    
    return render_template('users/partials/_mass_edit_libraries.html', 
                           services_data=services_data)


@users_bp.route('/mass_edit', methods=['POST'])
@login_required
@setup_required
@permission_required('mass_edit_users')
def mass_edit_users():
    """Handle mass edit operations on users"""
    current_app.logger.debug("--- MASS EDIT ROUTE START ---")
    
    # DEBUG 1: Print the raw form data received by Flask
    print(f"[SERVER DEBUG 1] Raw request.form: {request.form.to_dict()}")

    # We get user_ids manually from the request now
    user_ids_str = request.form.get('user_ids')
    toast_message = ""
    toast_category = "error"

    # Instantiate form for the other fields that DO need validation
    form = MassUserEditForm(request.form)
    
    # We still must populate the dynamic choices for the libraries field
    media_service_manager = MediaServiceManager()
    
    # Get libraries from all active servers, not just Plex
    available_libraries = {}
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    for server in all_servers:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            for lib in db_libraries:
                lib_id = lib.external_id
                lib_name = lib.name
                if lib_id:
                    # Use just the library name since server name is now shown in a separate badge
                    available_libraries[str(lib_id)] = lib_name
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {server.server_nickname}: {e}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # Manual validation for user_ids, then form validation for the rest
    if not user_ids_str:
        toast_message = "Validation Error: User Ids: This field is required."
        print("[SERVER DEBUG 2] user_ids_str is missing or empty.")
    elif form.validate():
        print(f"[SERVER DEBUG 3] Form validation PASSED. User IDs from request: '{user_ids_str}'")
        
        # Parse prefixed user IDs and extract actual database IDs
        user_ids = []
        # Convert UUIDs to actual user IDs for local users only
        from app.utils.helpers import get_user_by_uuid
        
        for uid_str in user_ids_str.split(','):
            uid_str = uid_str.strip()
            if not uid_str:
                continue
            try:
                user_obj, user_type = get_user_by_uuid(uid_str)
                if user_obj:
                    # For delete operations, support both local and service users
                    # For other mass edit operations, we only support service users (not local users)
                    if user_type == "user_media_access":
                        user_ids.append(uid_str)  # Use UUID for mass operations
                    elif user_type == "user_app_access":
                        # Check if this is a delete operation - if so, allow local users too
                        action = form.action.data if form.action.data else request.form.get('action', '')
                        if action == 'delete_users':
                            user_ids.append(uid_str)  # Allow local users for delete operations
                        else:
                            current_app.logger.warning(f"Mass edit attempted on local user {uid_str} - only service users supported for {action}")
                    else:
                        current_app.logger.warning(f"Unknown user type {user_type} for user {uid_str}")
                else:
                    current_app.logger.warning(f"User not found for UUID {uid_str}")
            except Exception as e:
                current_app.logger.error(f"Invalid user UUID in mass edit: {uid_str} - {e}")
        
        if not user_ids:
            action = form.action.data if form.action.data else request.form.get('action', '')
            if action == 'delete_users':
                toast_message = "No valid users selected for mass delete operation."
            else:
                toast_message = "No valid service users selected for mass edit operation."
            toast_category = "error"
        else:
            action = form.action.data
            try:
                if action == 'update_libraries':
                    # Parse libraries per server for service users
                    updates_by_server = {}
                    for key, value in request.form.items():
                        if key.startswith('libraries_server_'):
                            server_id = int(key.split('_')[-1])
                            if server_id not in updates_by_server:
                                updates_by_server[server_id] = []
                            updates_by_server[server_id] = request.form.getlist(key)

                    processed_count, error_count = user_service.mass_update_user_libraries_by_server(user_ids, updates_by_server, admin_id=current_user.id)
                    toast_message = f"Mass library update: {processed_count} service users updated, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
                elif action == 'extend_access':
                    days_to_extend = form.days_to_extend.data
                    if not days_to_extend or days_to_extend < 1:
                        toast_message = "Invalid number of days to extend."
                        toast_category = "error"
                    else:
                        processed_count, error_count = user_service.mass_extend_access(user_ids, days_to_extend, admin_id=current_user.id)
                        toast_message = f"Extended access for {processed_count} service users by {days_to_extend} days, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action == 'set_expiration':
                    new_expiration_date = form.new_expiration_date.data
                    if not new_expiration_date:
                        toast_message = "Expiration date is required."
                        toast_category = "error"
                    else:
                        processed_count, error_count = user_service.mass_set_expiration(user_ids, new_expiration_date, admin_id=current_user.id)
                        toast_message = f"Set expiration date for {processed_count} service users, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action == 'clear_expiration':
                    processed_count, error_count = user_service.mass_clear_expiration(user_ids, admin_id=current_user.id)
                    toast_message = f"Cleared expiration for {processed_count} service users, {error_count} errors."
                    toast_category = "success" if error_count == 0 else "warning"
                elif action == 'merge_into_local_account':
                    # Get form data for the new local account
                    merge_username = request.form.get('merge_username', '').strip()
                    merge_password = request.form.get('merge_password', '')
                    merge_confirm_password = request.form.get('merge_confirm_password', '')
                    
                    # Validate the merge form data
                    if not merge_username or not merge_password or not merge_confirm_password:
                        toast_message = "Username and password are required for creating a local account."
                        toast_category = "error"
                    elif len(merge_username) < 3 or len(merge_username) > 50:
                        toast_message = "Username must be between 3 and 50 characters."
                        toast_category = "error"
                    elif len(merge_password) < 8:
                        toast_message = "Password must be at least 8 characters."
                        toast_category = "error"
                    elif merge_password != merge_confirm_password:
                        toast_message = "Passwords do not match."
                        toast_category = "error"
                    elif not all(c.isalnum() or c in '_-' for c in merge_username):
                        toast_message = "Username can only contain letters, numbers, underscores, and hyphens."
                        toast_category = "error"
                    else:
                        # Check if username already exists
                        existing_user = UserAppAccess.query.filter_by(username=merge_username).first()
                        if existing_user:
                            toast_message = f"Username '{merge_username}' already exists. Please choose a different one."
                            toast_category = "error"
                        else:
                            try:
                                # Create the merge operation
                                processed_count, error_count, local_user_id = user_service.merge_service_users_into_local_account(
                                    user_ids, merge_username, merge_password, admin_id=current_user.id
                                )
                                if processed_count > 0:
                                    toast_message = f"Successfully created local account '{merge_username}' and linked {processed_count} service users. {error_count} errors."
                                    toast_category = "success" if error_count == 0 else "warning"
                                else:
                                    toast_message = f"Failed to create local account. {error_count} errors occurred."
                                    toast_category = "error"
                            except Exception as e:
                                toast_message = f"Error creating local account: {str(e)}"
                                toast_category = "error"
                elif action == 'delete_users':
                    if not form.confirm_delete.data:
                        toast_message = "Deletion was not confirmed. No action taken."
                        toast_category = "warning"
                    else:
                        # mass_delete_users already supports UUIDs, so pass them directly
                        processed_count, error_count = user_service.mass_delete_users(user_ids, admin_id=current_user.id)
                        toast_message = f"Mass delete: {processed_count} removed, {error_count} errors."
                        toast_category = "success" if error_count == 0 else "warning"
                elif action.endswith('_whitelist'):
                    should_add = action.startswith('add_to')
                    whitelist_type = "Bot" if "bot" in action else "Purge"
                    if whitelist_type == "Bot":
                        count = user_service.mass_update_bot_whitelist(user_ids, should_add, current_user.id)
                    else: # Purge
                        count = user_service.mass_update_purge_whitelist(user_ids, should_add, current_user.id)
                    action_text = "added to" if should_add else "removed from"
                    toast_message = f"{count} service user(s) {action_text} the {whitelist_type} Whitelist."
                    toast_category = "success"
                else:
                    toast_message = "Invalid action."
            except Exception as e:
                toast_message = f"Server Error: {str(e)[:100]}"
                print(f"[SERVER DEBUG 5] Exception during action '{action}': {e}")
                import traceback
                traceback.print_exc()
    else:
        # Form validation failed for other fields (e.g., action)
        error_list = []
        for field, errors in form.errors.items():
            field_label = getattr(form, field).label.text
            for error in errors:
                error_list.append(f"{field_label}: {error}")
                print(f"[SERVER DEBUG 4] Validation Error for '{field_label}': {error}")
        toast_message = "Validation Error: " + "; ".join(error_list)

    # Return response with toast message
    response = make_response("", 200)
    toast_payload = {"showToastEvent": {"message": toast_message, "category": toast_category}}
    response.headers['HX-Trigger'] = json.dumps(toast_payload)
    
    # Debug logging to help troubleshoot toast issues
    current_app.logger.debug(f"Mass edit complete. Toast message: '{toast_message}', category: '{toast_category}'")
    current_app.logger.debug(f"HX-Trigger header: {response.headers.get('HX-Trigger')}")
    
    return response


@users_bp.route('/purge_inactive', methods=['POST'])
@login_required
@setup_required
@permission_required('purge_users')
def purge_inactive_users():
    """Purge inactive users based on criteria"""
    try:
        user_ids_to_purge = request.form.getlist('user_ids_to_purge')
        if not user_ids_to_purge:
            return render_template('partials/_alert_message.html', message="No users were selected to be purged.", category='info'), 400

        # Pass all criteria to the service layer for a final, safe check
        results = user_service.purge_inactive_users(
            user_ids_to_purge=[int(uid) for uid in user_ids_to_purge],
            admin_id=current_user.id,
            inactive_days_threshold=request.form.get('inactive_days', type=int),
            exclude_sharers=request.form.get('exclude_sharers') == 'true',
            exclude_whitelisted=request.form.get('exclude_whitelisted') == 'true',
            ignore_creation_date_for_never_streamed=request.form.get('ignore_creation_date') == 'true'
        )
        return render_template('partials/_alert_message.html', 
                               message=results['message'], 
                               category='success' if results['errors'] == 0 else 'warning')
    except Exception as e:
        current_app.logger.error(f"Error during purge inactive users route: {e}", exc_info=True)
        return render_template('partials/_alert_message.html', message=f"An unexpected error occurred: {e}", category='error'), 500


@users_bp.route('/purge_inactive/preview', methods=['POST'])
@login_required
@setup_required
def preview_purge_inactive_users():
    """Preview users that would be purged based on criteria"""
    try:
        # Get form parameters
        inactive_days = request.form.get('inactive_days', 90, type=int)
        exclude_sharers = request.form.get('exclude_sharers') == 'true'
        exclude_whitelisted = request.form.get('exclude_whitelisted') == 'true'
        ignore_creation_date = request.form.get('ignore_creation_date') == 'true'
        
        # Get preview from service
        preview_data = user_service.preview_purge_inactive_users(
            inactive_days_threshold=inactive_days,
            exclude_sharers=exclude_sharers,
            exclude_whitelisted=exclude_whitelisted,
            ignore_creation_date_for_never_streamed=ignore_creation_date
        )
        
        return render_template('users/partials/purge_preview_modal.html', 
                               preview_data=preview_data,
                               inactive_days=inactive_days,
                               exclude_sharers=exclude_sharers,
                               exclude_whitelisted=exclude_whitelisted,
                               ignore_creation_date=ignore_creation_date)
    except Exception as e:
        current_app.logger.error(f"Error during purge preview: {e}", exc_info=True)
        return render_template('partials/_alert_message.html', 
                               message=f"Error generating preview: {e}", 
                               category='error'), 500