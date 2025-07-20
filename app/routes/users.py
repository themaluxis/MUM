# File: app/routes/users.py
from flask import Blueprint, render_template, request, current_app, session, make_response 
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.models import User, Setting, EventType, AdminAccount, StreamHistory
from app.models_media_services import ServiceType
from app.models_media_services import ServiceType
from app.forms import UserEditForm, MassUserEditForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required
from app.services import user_service
from app.services.unified_user_service import UnifiedUserService
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
import json
from datetime import datetime, timezone, timedelta # Ensure these are imported
from sqlalchemy.exc import IntegrityError

bp = Blueprint('users', __name__)

@bp.route('/')
@login_required
@setup_required
def list_users():
    page = request.args.get('page', 1, type=int)
    view_mode = request.args.get('view', Setting.get('DEFAULT_USER_VIEW', 'cards'))
   
    session_per_page_key = 'users_list_per_page'
    default_per_page_config = current_app.config.get('DEFAULT_USERS_PER_PAGE', 12)
    try:
        items_per_page = int(request.args.get('per_page'))
        if items_per_page not in [12, 24, 48, 96]:
            raise ValueError("Invalid per_page value from request.args")
        session[session_per_page_key] = items_per_page
    except (TypeError, ValueError):
        items_per_page = session.get(session_per_page_key, default_per_page_config)
        if items_per_page not in [12, 24, 48, 96]:
            items_per_page = default_per_page_config
            session[session_per_page_key] = items_per_page

    query = User.query
    search_term = request.args.get('search', '').strip()
    if search_term:
        query = query.filter(or_(User.plex_username.ilike(f"%{search_term}%"), User.plex_email.ilike(f"%{search_term}%")))

    server_filter_id = request.args.get('server_id', 'all')
    if server_filter_id != 'all':
        # Ensure server_filter_id is an integer for the join
        try:
            server_filter_id_int = int(server_filter_id)
            from app.models_media_services import UserMediaAccess
            query = query.join(UserMediaAccess).filter(UserMediaAccess.server_id == server_filter_id_int)
        except ValueError:
            current_app.logger.warning(f"Invalid server_id received: {server_filter_id}")
            # Optionally, handle this error more gracefully, e.g., by showing an alert

    filter_type = request.args.get('filter_type', '')
    if filter_type == 'home_user': query = query.filter(User.is_home_user == True)
    elif filter_type == 'shares_back': query = query.filter(User.shares_back == True)
    elif filter_type == 'has_discord': query = query.filter(User.discord_user_id != None)
    elif filter_type == 'no_discord': query = query.filter(User.discord_user_id == None)
   
    # --- START OF ENHANCED SORTING LOGIC ---
    sort_by_param = request.args.get('sort_by', 'username_asc')
    sort_parts = sort_by_param.rsplit('_', 1)
    sort_column = sort_parts[0]
    sort_direction = 'desc' if len(sort_parts) > 1 and sort_parts[1] == 'desc' else 'asc'

    # Handle sorting that requires joins and aggregation
    if sort_column in ['total_plays', 'total_duration']:
        # For stats sorting, we must join and group
        query = query.outerjoin(User.stream_history).group_by(User.id)
        
        # Select the User object and the aggregated columns
        sort_field = func.count(StreamHistory.id) if sort_column == 'total_plays' else func.sum(func.coalesce(StreamHistory.duration_seconds, 0))
        query = query.add_columns(sort_field.label('sort_value'))
        
        if sort_direction == 'desc':
            query = query.order_by(db.desc('sort_value').nullslast())
        else:
            query = query.order_by(db.asc('sort_value').nullsfirst())
    else:
        # Standard sorting on direct User model fields
        sort_map = {
            'username': User.plex_username,
            'email': User.plex_email,
            'last_streamed': User.last_streamed_at,
            'plex_join_date': User.plex_join_date,
            'created_at': User.created_at
        }
        
        # Default to sorting by username if the column is invalid
        sort_field = sort_map.get(sort_column, User.plex_username)

        if sort_direction == 'desc':
            # Use .nullslast() to ensure users with no data appear at the end
            query = query.order_by(sort_field.desc().nullslast())
        else:
            # Use .nullsfirst() to ensure users with no data appear at the beginning
            query = query.order_by(sort_field.asc().nullsfirst())
    # --- END OF ENHANCED SORTING LOGIC ---

    admin_accounts = AdminAccount.query.filter(AdminAccount.plex_uuid.isnot(None)).all()
    admins_by_uuid = {admin.plex_uuid: admin for admin in admin_accounts}
    users_pagination = query.paginate(page=page, per_page=items_per_page, error_out=False)
    users_count = query.count()

    # Extract users from pagination results (handling complex queries that return tuples)
    users_on_page = [item[0] if isinstance(item, tuple) else item for item in users_pagination.items]
    user_ids_on_page = [user.id for user in users_on_page]

    # Fetch additional data for the current page
    stream_stats = user_service.get_bulk_user_stream_stats(user_ids_on_page)
    last_ips = user_service.get_bulk_last_known_ips(user_ids_on_page)
    
    # Get library access info for each user, and sort it
    user_library_access = {}
    user_sorted_libraries = {}
    from app.models_media_services import UserMediaAccess
    access_records = UserMediaAccess.query.filter(UserMediaAccess.user_id.in_(user_ids_on_page)).all()
    for access in access_records:
        if access.user_id not in user_library_access:
            user_library_access[access.user_id] = []
        user_library_access[access.user_id].extend(access.allowed_library_ids)

    media_service_manager = MediaServiceManager()
    plex_servers = media_service_manager.get_servers_by_type(ServiceType.PLEX, active_only=True)
    if not plex_servers:
        available_libraries = {}
    else:
        plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
        available_libraries = {lib['id']: lib['name'] for lib in plex_service.get_libraries()}

    for user_id, lib_ids in user_library_access.items():
        lib_names = [available_libraries.get(str(lib_id), f'Unknown Lib {lib_id}') for lib_id in lib_ids]
        user_sorted_libraries[user_id] = sorted(lib_names, key=str.lower)

    mass_edit_form = MassUserEditForm()  

    default_inactive_days = 90
    default_exclude_sharers = True

    purge_settings_context = {
        'inactive_days': request.form.get('inactive_days', default_inactive_days, type=int),
        'exclude_sharers': request.form.get('exclude_sharers', 'true' if default_exclude_sharers else 'false').lower() == 'true'
    }
   
    # Enhanced context dictionary with additional data
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers()
    server_dropdown_options = [{"id": "all", "name": "All Servers"}]
    for server in all_servers:
        server_dropdown_options.append({
            "id": server.id,
            "name": f"{server.name} ({server.service_type.value.capitalize()})"
        })

    template_context = {
        'title': "Managed Users",
        'users': users_pagination,
        'users_count': users_count,
        'stream_stats': stream_stats,
        'last_ips': last_ips,
        'user_library_access': user_library_access,
        'user_sorted_libraries': user_sorted_libraries,
        'current_view': view_mode,
        'available_libraries': available_libraries,
        'mass_edit_form': mass_edit_form,
        'selected_users_count': 0,
        'current_per_page': items_per_page,
        'purge_settings': purge_settings_context,
        'admin_plex_uuids': {admin.plex_uuid for admin in admin_accounts},
        'admins_by_uuid': admins_by_uuid,
        'sort_column': sort_column,
        'sort_direction': sort_direction,
        'server_dropdown_options': server_dropdown_options
    }

    if request.headers.get('HX-Request'):
        return render_template('users/partials/user_list_content.html', **template_context)

    return render_template('users/list.html', **template_context)

@bp.route('/sync', methods=['POST'])
@login_required
@setup_required
def sync_all_users():
    """
    Performs Plex user synchronization and returns an HTML response
    with htmx headers to trigger modals and toasts.
    """
    current_app.logger.info("Starting Plex user synchronization.")

    # --- Part 1: Core Synchronization Logic ---
    try:
        # Use the new unified user service instead of the old plex_service
        sync_result = UnifiedUserService.sync_all_users()
        
        if not sync_result['success']:
            current_app.logger.error(f"User sync failed: {sync_result.get('message', 'Unknown error')}")
            raw_plex_users_with_access = None
        else:
            # If using unified service, we can return early with the results
            modal_html = render_template('users/partials/sync_results_modal.html',
                                       sync_result=sync_result)
            trigger_payload = {
                "showToastEvent": {"message": sync_result.get('message', 'Sync completed'), "category": "success"},
                "openSyncResultsModal": True,
                "refreshUserList": True
            }
            headers = {
                'HX-Retarget': '#syncResultModalContainer',
                'HX-Reswap': 'innerHTML',
                'HX-Trigger-After-Swap': json.dumps(trigger_payload)
            }
            return make_response(modal_html, 200, headers)
            
    except Exception as e:
        current_app.logger.error(f"Critical error during user synchronization: {e}", exc_info=True)
        raw_plex_users_with_access = None

    # Fallback error handling if unified service fails
    if raw_plex_users_with_access is None:
        sync_result = {
            'success': False,
            'added': 0,
            'updated': 0,
            'errors': 1,
            'error_messages': ["Failed to retrieve users from media services."],
            'servers_synced': 0
        }
        modal_html = render_template('users/partials/sync_results_modal.html',
                                     sync_result=sync_result)
        trigger_payload = {
            "showToastEvent": {"message": "Sync failed: Could not contact media services.", "category": "error"},
            "openSyncResultsModal": True
        }
        headers = {
            'HX-Retarget': '#syncResultModalContainer',
            'HX-Reswap': 'innerHTML',
            'HX-Trigger-After-Swap': json.dumps(trigger_payload)
        }
        return make_response(modal_html, 200, headers)

    mum_users_all = User.query.all()
    mum_users_map_by_plex_id = {user.plex_user_id: user for user in mum_users_all if user.plex_user_id is not None}
    mum_users_map_by_plex_uuid = {user.plex_uuid: user for user in mum_users_all if user.plex_uuid}
    mum_users_map_by_username = {user.plex_username.lower(): user for user in mum_users_all if user.plex_username}

    added_users_details = []
    updated_users_details = []
    removed_users_details = []
    error_count = 0
    error_messages = []

    current_plex_user_ids_on_server = {item['id'] for item in raw_plex_users_with_access if item.get('id') is not None}

    # Process each user from the Plex sync
    for plex_user_data in raw_plex_users_with_access:
        # This includes checking for existing users, updating fields,
        # creating new users, and handling IntegrityError.
        plex_id = plex_user_data.get('id')
        plex_uuid_from_sync = plex_user_data.get('uuid')
        plex_username_from_sync = plex_user_data.get('username')

        if not plex_username_from_sync:
            msg = "Plex user data missing 'username'. Skipping."
            current_app.logger.warning(msg)
            error_count += 1
            error_messages.append(msg)
            continue

        mum_user = mum_users_map_by_plex_id.get(plex_id) or mum_users_map_by_plex_uuid.get(plex_uuid_from_sync)
        if not mum_user:
            mum_user = mum_users_map_by_username.get(plex_username_from_sync.lower())
            if mum_user:
                current_app.logger.warning(f"Found user '{plex_username_from_sync}' by username, but ID/UUID did not match. Updating existing record (ID: {mum_user.id}).")

        new_library_ids = list(plex_user_data.get('allowed_library_ids_on_server', []))
        accepted_at_str = plex_user_data.get('acceptedAt')
        plex_join_date_dt = None
        if accepted_at_str and accepted_at_str.isdigit():
            try:
                plex_join_date_dt = datetime.fromtimestamp(int(accepted_at_str), tz=timezone.utc)
            except (ValueError, TypeError):
                plex_join_date_dt = None
        
        if mum_user:
            changes = []
            if mum_user.plex_user_id != plex_id: changes.append("Plex User ID updated"); mum_user.plex_user_id = plex_id
            if mum_user.plex_uuid != plex_uuid_from_sync: changes.append("Plex UUID updated"); mum_user.plex_uuid = plex_uuid_from_sync
            if mum_user.plex_username != plex_username_from_sync: changes.append(f"Username changed"); mum_user.plex_username = plex_username_from_sync
            if set(mum_user.allowed_library_ids or []) != set(new_library_ids): changes.append("Libraries updated"); mum_user.allowed_library_ids = new_library_ids
            if plex_join_date_dt and (mum_user.plex_join_date is None or mum_user.plex_join_date != plex_join_date_dt.replace(tzinfo=None)):
                changes.append("Plex join date updated"); mum_user.plex_join_date = plex_join_date_dt.replace(tzinfo=None)

            if changes:
                mum_user.last_synced_with_plex = datetime.utcnow(); mum_user.updated_at = datetime.utcnow()
                updated_users_details.append({'username': plex_username_from_sync, 'changes': changes})
        else:
            try:
                new_user = User(
                    plex_user_id=plex_id, plex_uuid=plex_uuid_from_sync, plex_username=plex_username_from_sync,
                    plex_email=plex_user_data.get('email'), plex_thumb_url=plex_user_data.get('thumb'),
                    allowed_library_ids=new_library_ids, is_home_user=plex_user_data.get('is_home_user', False),
                    shares_back=plex_user_data.get('shares_back', False), is_plex_friend=plex_user_data.get('is_friend', False),
                    plex_join_date=plex_join_date_dt.replace(tzinfo=None) if plex_join_date_dt else None,
                    last_synced_with_plex=datetime.utcnow()
                )
                db.session.add(new_user)
                added_users_details.append({'username': plex_username_from_sync, 'plex_id': plex_id})
            except IntegrityError as ie:
                db.session.rollback(); msg = f"Integrity error adding '{plex_username_from_sync}': {ie}."; current_app.logger.error(msg); error_count += 1; error_messages.append(msg)
            except Exception as e:
                db.session.rollback(); msg = f"Error creating user '{plex_username_from_sync}': {e}"; current_app.logger.error(msg, exc_info=True); error_count += 1; error_messages.append(msg)

    # Process removals
    for user in mum_users_all:
        if user.plex_user_id not in current_plex_user_ids_on_server:
            removed_users_details.append({'username': user.plex_username, 'mum_id': user.id, 'plex_id': user.plex_user_id})
            db.session.delete(user)

    # Commit all session changes to the database
    if added_users_details or updated_users_details or removed_users_details or error_count > 0:
        try:
            db.session.commit()
            log_event(EventType.PLEX_SYNC_USERS_COMPLETE, f"Plex user sync complete. Added: {len(added_users_details)}, Updated: {len(updated_users_details)}, Removed: {len(removed_users_details)}, Errors: {error_count}.", details={"added": len(added_users_details), "updated": len(updated_users_details), "removed": len(removed_users_details)})
        except Exception as e_commit:
            db.session.rollback(); msg = f"DB commit error during sync: {e_commit}"; error_messages.append(msg); error_count += 1
            # Clear lists as the transaction failed
            added_users_details, updated_users_details, removed_users_details = [], [], []

    # --- Part 2: Response Generation Logic ---
    response_headers = {}
    
    if added_users_details or updated_users_details or removed_users_details or error_count > 0:
        modal_html = render_template('users/partials/sync_results_modal.html',
                                     added_users=added_users_details,
                                     updated_users=updated_users_details,
                                     removed_users=removed_users_details,
                                     error_count=error_count,
                                     error_messages=error_messages)

        response_headers['HX-Retarget'] = '#syncResultModalContainer'
        response_headers['HX-Reswap'] = 'innerHTML'

        toast_message = "Sync complete. Changes detected, see details."
        toast_category = "success"
        if error_count > 0 and not (added_users_details or updated_users_details or removed_users_details):
            toast_message = f"Sync encountered {error_count} error(s). See details."
            toast_category = "error"
        elif error_count > 0:
            toast_message = f"Sync complete with {error_count} error(s) and other changes."
            toast_category = "warning"

        trigger_payload = {
            "showToastEvent": {"message": toast_message, "category": toast_category},
            "openSyncResultsModal": True,
            "refreshUserList": True
        }
        response_headers['HX-Trigger-After-Swap'] = json.dumps(trigger_payload)

        return make_response(modal_html, 200, response_headers)
    
    else:
        # No changes and no errors
        trigger_payload = {
            "showToastEvent": {"message": "Sync complete. No changes were made.", "category": "success"},
            "refreshUserList": True
        }
        response_headers['HX-Trigger'] = json.dumps(trigger_payload)
        
        return make_response("", 200, response_headers)

@bp.route('/delete/<int:user_id>', methods=['DELETE'])
@login_required
@setup_required
@permission_required('delete_user')
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    username = user.plex_username
    try:
        user_service.delete_user_from_mum_and_plex(user_id, admin_id=current_user.id)
        
        # Create a toast message payload
        toast = {
            "showToastEvent": {
                "message": f"User '{username}' has been successfully removed.",
                "category": "success"
            }
        }
        
        # Create an empty response and add the HX-Trigger header
        response = make_response("", 200)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response

    except Exception as e:
        current_app.logger.error(f"Route Error deleting user {username}: {e}", exc_info=True)
        log_event(EventType.ERROR_GENERAL, f"Route: Failed to delete user {username}: {e}", user_id=user_id, admin_id=current_user.id)
        
        # Create an error toast message payload
        toast = {
            "showToastEvent": {
                "message": f"Error deleting user '{username}': {str(e)[:100]}",
                "category": "error"
            }
        }
        
        # Respond with an error status and the trigger header
        # Note: HTMX will NOT swap the target on a 500 error unless told to.
        # But it WILL process the trigger header, showing the toast.
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast)
        return response

@bp.route('/mass_edit_libraries_form')
@login_required
def mass_edit_libraries_form():
    user_ids_str = request.args.get('user_ids', '')
    if not user_ids_str:
        return '<div class="alert alert-error">No users selected.</div>'
    
    user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.isdigit()]
    
    # Group users by server
    from app.models_media_services import UserMediaAccess, MediaServer
    access_records = db.session.query(UserMediaAccess, User, MediaServer).join(User, UserMediaAccess.user_id == User.id).join(MediaServer, UserMediaAccess.server_id == MediaServer.id).filter(UserMediaAccess.user_id.in_(user_ids)).all()

    servers_data = {}
    for access, user, server in access_records:
        if server.id not in servers_data:
            service = MediaServiceFactory.create_service_from_db(server)
            libraries = service.get_libraries() if service else []
            servers_data[server.id] = {
                'server_name': server.name,
                'service_type': server.service_type.value,
                'users': [],
                'libraries': libraries,
                'current_library_ids': set() # To store common libraries
            }
        servers_data[server.id]['users'].append(user)
        if not servers_data[server.id]['current_library_ids']:
            servers_data[server.id]['current_library_ids'].update(access.allowed_library_ids or [])
        else:
            servers_data[server.id]['current_library_ids'].intersection_update(access.allowed_library_ids or [])

    return render_template('users/partials/_mass_edit_libraries.html', servers_data=servers_data)

@bp.route('/mass_edit', methods=['POST'])
@login_required
@setup_required
@permission_required('mass_edit_users')
def mass_edit_users():
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
    plex_servers = media_service_manager.get_servers_by_type(ServiceType.PLEX, active_only=True)
    if not plex_servers:
        available_libraries = {}
    else:
        plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
        available_libraries = {lib['id']: lib['name'] for lib in plex_service.get_libraries()}
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # Manual validation for user_ids, then form validation for the rest
    if not user_ids_str:
        toast_message = "Validation Error: User Ids: This field is required."
        print("[SERVER DEBUG 2] user_ids_str is missing or empty.")
    elif form.validate():
        print(f"[SERVER DEBUG 3] Form validation PASSED. User IDs from request: '{user_ids_str}'")
        user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.isdigit()]
        action = form.action.data
        try:
            if action == 'update_libraries':
                # The new logic will parse libraries per server
                updates_by_server = {}
                for key, value in request.form.items():
                    if key.startswith('libraries_server_'):
                        server_id = int(key.split('_')[-1])
                        if server_id not in updates_by_server:
                            updates_by_server[server_id] = []
                        updates_by_server[server_id] = request.form.getlist(key)

                processed_count, error_count = user_service.mass_update_user_libraries_by_server(user_ids, updates_by_server, admin_id=current_user.id)
                toast_message = f"Mass library update: {processed_count} users updated, {error_count} errors."
                toast_category = "success" if error_count == 0 else "warning"
            elif action == 'delete_users':
                if not form.confirm_delete.data:
                    toast_message = "Deletion was not confirmed. No action taken."
                    toast_category = "warning"
                else:
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
                toast_message = f"{count} user(s) {action_text} the {whitelist_type} Whitelist."
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

    # Re-rendering logic (unchanged)
    page = request.args.get('page', 1, type=int)
    view_mode = request.args.get('view', Setting.get('DEFAULT_USER_VIEW', 'cards'))
    items_per_page = session.get('users_list_per_page', int(current_app.config.get('DEFAULT_USERS_PER_PAGE', 12)))
    
    query = User.query
    search_term = request.args.get('search', '').strip()
    if search_term: query = query.filter(or_(User.plex_username.ilike(f"%{search_term}%"), User.plex_email.ilike(f"%{search_term}%")))
    
    filter_type = request.args.get('filter_type', '')
    if filter_type == 'home_user': query = query.filter(User.is_home_user == True)
    elif filter_type == 'shares_back': query = query.filter(User.shares_back == True)
    elif filter_type == 'has_discord': query = query.filter(User.discord_user_id != None)
    elif filter_type == 'no_discord': query = query.filter(User.discord_user_id == None)
    
    sort_by = request.args.get('sort_by', 'username_asc')
    if sort_by == 'username_desc': query = query.order_by(User.plex_username.desc())
    elif sort_by == 'last_streamed_desc': query = query.order_by(User.last_streamed_at.desc().nullslast())
    elif sort_by == 'last_streamed_asc': query = query.order_by(User.last_streamed_at.asc().nullsfirst())
    elif sort_by == 'created_at_desc': query = query.order_by(User.created_at.desc())
    elif sort_by == 'created_at_asc': query = query.order_by(User.created_at.asc())
    else: query = query.order_by(User.plex_username.asc())
    
    users_pagination = query.paginate(page=page, per_page=items_per_page, error_out=False)
    users_count = query.count()
    
    # Get library access info for each user
    user_library_access = {}
    from app.models_media_services import UserMediaAccess
    user_ids_on_page = [user.id for user in users_pagination.items]
    access_records = UserMediaAccess.query.filter(UserMediaAccess.user_id.in_(user_ids_on_page)).all()
    for access in access_records:
        if access.user_id not in user_library_access:
            user_library_access[access.user_id] = []
        user_library_access[access.user_id].extend(access.allowed_library_ids)

    response_html = render_template('users/partials/user_list_content.html',
                                    users=users_pagination,
                                    users_count=users_count,
                                    user_library_access=user_library_access,
                                    available_libraries=available_libraries,
                                    current_view=view_mode,
                                    current_per_page=items_per_page)
    
    response = make_response(response_html)
    toast_payload = {"showToastEvent": {"message": toast_message, "category": toast_category}}
    response.headers['HX-Trigger-After-Swap'] = json.dumps(toast_payload)
    
    return response

@bp.route('/purge_inactive', methods=['POST'])
@login_required
@setup_required
@permission_required('purge_users')
def purge_inactive_users():
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
    
@bp.route('/purge_inactive/preview', methods=['POST'])
@login_required
@setup_required
def preview_purge_inactive_users():
    inactive_days_str = request.form.get('inactive_days')
    
    # For checkboxes, if they are not in request.form, it means they were unchecked.
    # The value is 'true' only if they are checked and sent.
    exclude_sharers_val = request.form.get('exclude_sharers') # Will be 'true' or None
    exclude_whitelisted_val = request.form.get('exclude_purge_whitelisted') # Will be 'true' or None
    ignore_creation_date_val = request.form.get('ignore_creation_date')

    current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Received form data: inactive_days='{inactive_days_str}', exclude_sharers='{exclude_sharers_val}', exclude_whitelisted='{exclude_whitelisted_val}'")
    
    try:
        inactive_days = int(inactive_days_str) if inactive_days_str and inactive_days_str.isdigit() else 90 # Default if empty or non-digit
        
        # If checkbox is checked, request.form.get() will be 'true' (matching the value="true" in HTML)
        # If unchecked, request.form.get() will be None.
        exclude_sharers = (exclude_sharers_val == 'true')
        exclude_whitelisted = (exclude_whitelisted_val == 'true')
        ignore_creation_date = (ignore_creation_date_val == 'true')

        current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Parsed criteria: inactive_days={inactive_days}, exclude_sharers={exclude_sharers}, exclude_whitelisted={exclude_whitelisted}")

        if inactive_days < 7:
            return render_template('partials/_alert_message.html', message="Inactivity period must be at least 7 days.", category='error'), 400
        
        eligible_users = user_service.get_users_eligible_for_purge(
            inactive_days_threshold=inactive_days,
            exclude_sharers=exclude_sharers,
            exclude_whitelisted=exclude_whitelisted,
            ignore_creation_date_for_never_streamed=ignore_creation_date
        )
        
        current_app.logger.info(f"User_Routes.py - preview_purge_inactive_users(): Found {len(eligible_users)} users eligible for purge based on criteria.")

        purge_criteria = {
            'inactive_days': inactive_days,
            'exclude_sharers': exclude_sharers,
            'exclude_whitelisted': exclude_whitelisted,
            'ignore_creation_date': ignore_creation_date
        }

        return render_template('users/partials/purge_preview_modal.html', 
                               eligible_users=eligible_users, 
                               purge_criteria=purge_criteria)
    except ValueError as ve: # For int conversion error if any
        current_app.logger.error(f"User_Routes.py - preview_purge_inactive_users(): ValueError parsing form: {ve}")
        return render_template('partials/_alert_message.html', message=f"Invalid input: {ve}", category='error'), 400
    except Exception as e:
        current_app.logger.error(f"User_Routes.py - preview_purge_inactive_users(): Error generating purge preview: {e}", exc_info=True)
        return render_template('partials/_alert_message.html', message=f"An unexpected error occurred generating purge preview: {e}", category='error'), 500
    
@bp.route('/quick_edit_form/<int:user_id>')
@login_required
@permission_required('edit_user')
def get_quick_edit_form(user_id):
    user = User.query.get_or_404(user_id)
    form = UserEditForm(obj=user) # Pre-populate form with existing data

    # Populate dynamic choices
    media_service_manager = MediaServiceManager()
    plex_servers = media_service_manager.get_servers_by_type(ServiceType.PLEX, active_only=True)
    if not plex_servers:
        available_libraries = {}
    else:
        plex_service = MediaServiceFactory.create_service_from_db(plex_servers[0])
        available_libraries = {lib['id']: lib['name'] for lib in plex_service.get_libraries()}
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    
    # Pre-populate the fields with the user's current settings
    from app.models_media_services import UserMediaAccess
    access = UserMediaAccess.query.filter_by(user_id=user.id, server_id=plex_servers[0].id).first()
    if access:
        form.libraries.data = list(access.allowed_library_ids or [])
    else:
        form.libraries.data = []
    form.allow_downloads.data = user.allow_downloads
    form.allow_4k_transcode.data = user.allow_4k_transcode
    form.is_discord_bot_whitelisted.data = user.is_discord_bot_whitelisted
    form.is_purge_whitelisted.data = user.is_purge_whitelisted
    
    # Updated logic for DateField - the form will automatically populate access_expires_at 
    # from the user object via obj=user, but we can explicitly set it if needed
    if user.access_expires_at:
        # Convert datetime to date for the DateField
        form.access_expires_at.data = user.access_expires_at.date()
    
    # We pass the _settings_tab partial, which contains the form we need.
    return render_template(
        'users/partials/settings_tab.html',
        form=form,
        user=user
    )