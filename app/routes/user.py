# File: app/routes/user.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, make_response
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
from app.models import User, AdminAccount, StreamHistory, EventType
from app.forms import UserEditForm, UserResetPasswordForm
from app.extensions import db
from app.utils.helpers import permission_required, log_event
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import ServiceType
from app.services.media_service_manager import MediaServiceManager
from app.services import user_service
import json

# Note the new blueprint name and singular URL prefix
bp = Blueprint('user', __name__, url_prefix='/user')

@bp.route('/')
@bp.route('/index')
@bp.route('/dashboard')
@login_required
def index():
    """User dashboard/index page for regular user accounts - accessible at /user/dashboard"""
    # Ensure this is a regular user, not an admin
    if isinstance(current_user, AdminAccount):
        return redirect(url_for('dashboard.index'))
    
    # Ensure this is a User with password_hash (user account)
    if not isinstance(current_user, User) or not current_user.password_hash:
        flash('Access denied. Please log in with a valid user account.', 'danger')
        return redirect(url_for('auth.user_login'))
    
    # Get application name for welcome message
    from app.models import Setting
    app_name = Setting.get('APP_NAME', 'MUM')
    
    return render_template('user/index.html', 
                         title="Welcome", 
                         app_name=app_name,
                         user=current_user)

@bp.route('/<int:user_id>', methods=['GET', 'POST'])
@login_required
@permission_required('view_user')
def view_user(user_id):
    # Get the active tab from the URL query. Default to 'profile' for GET, 'settings' for POST context.
    tab = request.args.get('tab', 'settings' if request.method == 'POST' else 'profile')
    
    user = User.query.get_or_404(user_id)
    
    # Correctly instantiate the form:
    # On POST, it's populated from request.form.
    # On GET, it's populated from the user object.
    form = UserEditForm(request.form if request.method == 'POST' else None, obj=user)
    
    # Populate dynamic choices for the form - only show libraries from servers this user has access to
    from app.models_media_services import UserMediaAccess
    from app.services.media_service_factory import MediaServiceFactory
    user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
    
    available_libraries = {}
    current_app.logger.info(f"DEBUG KAVITA FORM: Building available libraries for user {user.id}")
    
    for access in user_access_records:
        try:
            service = MediaServiceFactory.create_service_from_db(access.server)
            current_app.logger.info(f"DEBUG KAVITA FORM: Processing server {access.server.name} (type: {access.server.service_type.value})")
            current_app.logger.info(f"DEBUG KAVITA FORM: User access record allowed_library_ids: {access.allowed_library_ids}")
            
            if service:
                server_libraries = service.get_libraries()
                current_app.logger.info(f"DEBUG KAVITA FORM: Server libraries from API: {[{lib.get('id'): lib.get('name')} for lib in server_libraries]}")
                
                for lib in server_libraries:
                    lib_id = lib.get('external_id') or lib.get('id')
                    lib_name = lib.get('name', 'Unknown')
                    if lib_id:
                        # For Kavita, create compound IDs to match the format used in user access records
                        if access.server.service_type.value == 'kavita':
                            compound_lib_id = f"{lib_id}_{lib_name}"
                            available_libraries[compound_lib_id] = lib_name
                            current_app.logger.info(f"DEBUG KAVITA FORM: Added Kavita library: {compound_lib_id} -> {lib_name}")
                        else:
                            available_libraries[str(lib_id)] = lib_name
                            current_app.logger.info(f"DEBUG KAVITA FORM: Added non-Kavita library: {lib_id} -> {lib_name}")
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {access.server.name}: {e}")
    
    current_app.logger.info(f"DEBUG KAVITA FORM: Final available_libraries: {available_libraries}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    current_app.logger.info(f"DEBUG KAVITA FORM: Form choices set to: {form.libraries.choices}")

    # Handle form submission for the settings tab
    if form.validate_on_submit(): # This handles (if request.method == 'POST' and form.validate())
        try:
            # Updated expiration logic to handle DateField calendar picker
            access_expiration_changed = False
            
            if form.clear_access_expiration.data:
                if user.access_expires_at is not None:
                    user.access_expires_at = None
                    access_expiration_changed = True
            elif form.access_expiration.data:
                # WTForms gives a date object. Combine with max time to set expiry to end of day.
                new_expiry_datetime = datetime.combine(form.access_expiration.data, datetime.max.time())
                # Only update if the date is actually different
                if user.access_expires_at is None or user.access_expires_at.date() != new_expiry_datetime.date():
                    user.access_expires_at = new_expiry_datetime
                    access_expiration_changed = True
            
            # Get current library IDs from UserMediaAccess records
            current_library_ids = []
            for access in user_access_records:
                current_library_ids.extend(access.allowed_library_ids or [])
            
            original_library_ids = set(current_library_ids)
            new_library_ids_from_form = set(form.libraries.data or [])
            libraries_changed = (original_library_ids != new_library_ids_from_form)

            # Update user fields directly (not library-related)
            user.notes = form.notes.data
            user.is_discord_bot_whitelisted = form.is_discord_bot_whitelisted.data
            user.is_purge_whitelisted = form.is_purge_whitelisted.data
            user.allow_4k_transcode = form.allow_4k_transcode.data
            
            # Update library access in UserMediaAccess records if changed
            if libraries_changed:
                for access in user_access_records:
                    try:
                        # Get the service for this server
                        service = MediaServiceFactory.create_service_from_db(access.server)
                        if service:
                            # Get libraries available on this server
                            server_libraries = service.get_libraries()
                            server_lib_ids = [lib.get('external_id') or lib.get('id') for lib in server_libraries]
                            
                            # Filter the new library IDs to only include ones available on this server
                            new_libs_for_this_server = [lib_id for lib_id in new_library_ids_from_form if lib_id in server_lib_ids]
                            
                            # Special handling for Jellyfin: if all libraries are selected, use '*' wildcard
                            if (access.server.service_type == ServiceType.JELLYFIN and 
                                set(new_libs_for_this_server) == set(server_lib_ids) and 
                                len(server_lib_ids) > 0):
                                new_libs_for_this_server = ['*']
                            
                            # Update the access record
                            access.allowed_library_ids = new_libs_for_this_server
                            access.updated_at = datetime.utcnow()
                            
                            # Update the media service if it supports user access updates
                            if hasattr(service, 'update_user_access'):
                                # For Plex users, use plex_user_id; for others, use the external_user_id
                                user_identifier = user.plex_user_id if user.plex_user_id else access.external_user_id
                                if user_identifier:
                                    service.update_user_access(user_identifier, new_libs_for_this_server)
                    except Exception as e:
                        current_app.logger.error(f"Error updating library access for server {access.server.name}: {e}")
                
                log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' library access updated", user_id=user.id, admin_id=current_user.id)
            
            user.updated_at = datetime.utcnow()
            
            if access_expiration_changed:
                if user.access_expires_at is None:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' access expiration cleared.", user_id=user.id, admin_id=current_user.id)
                else:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' access expiration set to {user.access_expires_at.strftime('%Y-%m-%d')}.", user_id=user.id, admin_id=current_user.id)
            
            # This commit saves all changes from user_service and the expiration date
            db.session.commit()
            
            if request.headers.get('HX-Request'):
                # Re-fetch user data to ensure the form is populated with the freshest data after save
                user = User.query.get_or_404(user_id)
                form_after_save = UserEditForm(obj=user)
                
                # Re-populate the dynamic choices and data for the re-rendered form
                form_after_save.libraries.choices = list(available_libraries.items())
                
                # Get current library IDs from UserMediaAccess records for the re-rendered form
                current_library_ids_after_save = []
                updated_user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
                for access in updated_user_access_records:
                    current_library_ids_after_save.extend(access.allowed_library_ids or [])
                
                # Handle special case for Jellyfin users with '*' (all libraries access)
                if current_library_ids_after_save == ['*']:
                    # If user has "All Libraries" access, check all available library checkboxes
                    form_after_save.libraries.data = list(available_libraries.keys())
                else:
                    form_after_save.libraries.data = list(set(current_library_ids_after_save))

                # OOB-SWAP LOGIC
                # 1. Render the updated form for the modal (the primary target)
                modal_html = render_template('users/partials/settings_tab.html', form=form_after_save, user=user)

                # 2. Render the updated user card for the OOB swap
                # We need the same context that the main user list uses for a card
                from app.models_media_services import UserMediaAccess
                
                # Get all user access records for proper library display
                all_user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
                user_sorted_libraries = {}
                user_service_types = {}
                user_server_names = {}
                
                # Collect library IDs from all access records
                all_library_ids = []
                user_service_types[user.id] = []
                user_server_names[user.id] = []
                
                for access in all_user_access_records:
                    all_library_ids.extend(access.allowed_library_ids or [])
                    # Track service types
                    if access.server.service_type not in user_service_types[user.id]:
                        user_service_types[user.id].append(access.server.service_type)
                    # Track server names
                    if access.server.name not in user_server_names[user.id]:
                        user_server_names[user.id].append(access.server.name)
                
                # Handle special case for Jellyfin users with '*' (all libraries access)
                if all_library_ids == ['*']:
                    lib_names = ['All Libraries']
                else:
                    # Check if this user has library_names available (for services like Kavita)
                    if hasattr(user, 'library_names') and user.library_names:
                        # Use library_names from the user object
                        lib_names = user.library_names
                    else:
                        # Fallback to looking up in available_libraries
                        # For Kavita unique IDs (format: "0_Comics"), extract the name part
                        lib_names = []
                        for lib_id in all_library_ids:
                            if '_' in str(lib_id) and str(lib_id).split('_', 1)[0].isdigit():
                                # This looks like a Kavita unique ID (e.g., "0_Comics"), extract the name
                                lib_name = str(lib_id).split('_', 1)[1]
                                lib_names.append(lib_name)
                            else:
                                # Regular library ID lookup
                                lib_names.append(available_libraries.get(str(lib_id), f'Unknown Lib {lib_id}'))
                user_sorted_libraries[user.id] = sorted(lib_names, key=str.lower)
                
                admins_by_uuid = {admin.plex_uuid: admin for admin in AdminAccount.query.filter(AdminAccount.plex_uuid.isnot(None)).all()}

                card_html = render_template(
                    'users/partials/_single_user_card.html',
                    user=user,
                    user_sorted_libraries=user_sorted_libraries,
                    user_service_types=user_service_types,
                    user_server_names=user_server_names,
                    admins_by_uuid=admins_by_uuid,
                    current_user=current_user 
                )
                
                # 3. Add the oob-swap attribute to the card's root div
                card_html_oob = card_html.replace(f'id="user-card-{user.id}"', f'id="user-card-{user.id}" hx-swap-oob="true"')

                # 4. Combine the modal and card HTML for the response
                final_html = modal_html + card_html_oob

                # Create the toast message payload
                toast_payload = {
                    "showToastEvent": {
                        "message": f"User '{user.get_display_name()}' updated successfully.",
                        "category": "success"
                    }
                }
                
                # Create the response and add the HX-Trigger header
                response = make_response(final_html)
                response.headers['HX-Trigger'] = json.dumps(toast_payload)
                return response
            else:
                # Fallback for standard form submissions remains the same
                flash(f"User '{user.get_display_name()}' updated successfully.", "success")
                back_param = request.args.get('back')
                back_view_param = request.args.get('back_view')
                redirect_params = {'user_id': user.id, 'tab': 'settings'}
                if back_param:
                    redirect_params['back'] = back_param
                if back_view_param:
                    redirect_params['back_view'] = back_view_param
                return redirect(url_for('user.view_user', **redirect_params))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating user {user.get_display_name()}: {e}", exc_info=True)
            flash(f"Error updating user: {e}", "danger")

    if request.method == 'POST' and form.errors:
        if request.headers.get('HX-Request'):
            return render_template('users/partials/settings_tab.html', form=form, user=user), 422

    if request.method == 'GET':
        # Get current library IDs from UserMediaAccess records (same as quick edit form)
        current_library_ids = []
        for access in user_access_records:
            current_library_ids.extend(access.allowed_library_ids or [])
        
        current_app.logger.info(f"DEBUG KAVITA FORM: Current library IDs from access records: {current_library_ids}")
        current_app.logger.info(f"DEBUG KAVITA FORM: Available library keys: {list(available_libraries.keys())}")
        
        # Handle special case for Jellyfin users with '*' (all libraries access)
        if current_library_ids == ['*']:
            # If user has "All Libraries" access, check all available library checkboxes
            form.libraries.data = list(available_libraries.keys())
            current_app.logger.info(f"DEBUG KAVITA FORM: Jellyfin wildcard case - setting form data to: {form.libraries.data}")
        else:
            # For Kavita users, ensure we're using the compound IDs that match the available_libraries keys
            validated_library_ids = []
            for lib_id in current_library_ids:
                current_app.logger.info(f"DEBUG KAVITA FORM: Processing library ID: {lib_id}")
                if str(lib_id) in available_libraries:
                    validated_library_ids.append(str(lib_id))
                    current_app.logger.info(f"DEBUG KAVITA FORM: Direct match found for: {lib_id}")
                else:
                    current_app.logger.info(f"DEBUG KAVITA FORM: No direct match for {lib_id}, searching for compound ID...")
                    # This might be a legacy ID format, try to find a matching compound ID
                    found_match = False
                    for available_id in available_libraries.keys():
                        if '_' in available_id and available_id.startswith(f"{lib_id}_"):
                            validated_library_ids.append(available_id)
                            current_app.logger.info(f"DEBUG KAVITA FORM: Found compound match: {lib_id} -> {available_id}")
                            found_match = True
                            break
                    
                    # If no compound match, try matching by library name (for Kavita ID changes)
                    if not found_match and '_' in str(lib_id):
                        stored_lib_name = str(lib_id).split('_', 1)[1]  # Extract name from stored ID
                        current_app.logger.info(f"DEBUG KAVITA FORM: Trying name match for: {stored_lib_name}")
                        for available_id, available_name in available_libraries.items():
                            if available_name == stored_lib_name:
                                validated_library_ids.append(available_id)
                                current_app.logger.info(f"DEBUG KAVITA FORM: Found name match: {lib_id} -> {available_id} (name: {stored_lib_name})")
                                found_match = True
                                break
                    
                    if not found_match:
                        current_app.logger.warning(f"DEBUG KAVITA FORM: No match found for library ID: {lib_id}")
            
            form.libraries.data = list(set(validated_library_ids))  # Remove duplicates
            current_app.logger.info(f"DEBUG KAVITA FORM: Final form.libraries.data: {form.libraries.data}")
        # Remove the old access_expires_in_days logic since we're now using DateField
        # The form will automatically populate access_expires_at from the user object via obj=user

    stream_stats = user_service.get_user_stream_stats(user_id)
    last_ip_map = user_service.get_bulk_last_known_ips([user_id])
    last_ip = last_ip_map.get(user_id)
    user.stream_stats = stream_stats
    user.total_plays = stream_stats.get('global', {}).get('all_time_plays', 0)
    user.total_duration = stream_stats.get('global', {}).get('all_time_duration_seconds', 0)
    user.last_known_ip = last_ip if last_ip else 'N/A'
    
    stream_history_pagination = None
    kavita_reading_stats = None
    kavita_reading_history = None
    
    if tab == 'history':
        page = request.args.get('page', 1, type=int)
        
        # Check if this is a Kavita user and get reading data
        is_kavita_user = False
        kavita_user_id = None
        
        current_app.logger.info(f"DEBUG KAVITA HISTORY: Checking user {user.id} for Kavita access")
        current_app.logger.info(f"DEBUG KAVITA HISTORY: User access records: {[(access.server.name, access.server.service_type.value, access.external_user_id) for access in user_access_records]}")
        
        for access in user_access_records:
            if access.server.service_type.value == 'kavita':
                is_kavita_user = True
                kavita_user_id = access.external_user_id
                current_app.logger.info(f"DEBUG KAVITA HISTORY: Found Kavita user! Server: {access.server.name}, External User ID: {kavita_user_id}")
                break
        
        current_app.logger.info(f"DEBUG KAVITA HISTORY: Is Kavita user: {is_kavita_user}, User ID: {kavita_user_id}")
        
        if is_kavita_user and kavita_user_id:
            # Get Kavita reading data
            try:
                from app.services.media_service_factory import MediaServiceFactory
                kavita_server = None
                for access in user_access_records:
                    if access.server.service_type.value == 'kavita':
                        kavita_server = access.server
                        break
                
                if kavita_server:
                    service = MediaServiceFactory.create_service_from_db(kavita_server)
                    if service:
                        kavita_reading_stats = service.get_user_reading_stats(kavita_user_id)
                        kavita_reading_history = service.get_user_reading_history(kavita_user_id)
                        current_app.logger.info(f"DEBUG KAVITA HISTORY: Stats: {kavita_reading_stats}")
                        current_app.logger.info(f"DEBUG KAVITA HISTORY: History: {kavita_reading_history}")
            except Exception as e:
                current_app.logger.error(f"Error fetching Kavita reading data: {e}")
        
        if not is_kavita_user:
            # For non-Kavita users, use regular stream history
            stream_history_pagination = StreamHistory.query.filter_by(user_id=user.id)\
                .order_by(StreamHistory.started_at.desc())\
                .paginate(page=page, per_page=15, error_out=False)
            
    # Get user service types and server names for service-aware display
    user_service_types = {}
    user_server_names = {}
    from app.models_media_services import UserMediaAccess
    user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
    user_service_types[user.id] = []
    user_server_names[user.id] = []
    for access in user_access_records:
        if access.server.service_type not in user_service_types[user.id]:
            user_service_types[user.id].append(access.server.service_type)
        if access.server.name not in user_server_names[user.id]:
            user_server_names[user.id].append(access.server.name)

    if request.headers.get('HX-Request') and tab == 'history':
        return render_template('users/partials/history_tab_content.html', 
                             user=user, 
                             history_logs=stream_history_pagination,
                             kavita_reading_stats=kavita_reading_stats,
                             kavita_reading_history=kavita_reading_history,
                             user_service_types=user_service_types)
        
    return render_template(
        'users/profile.html',
        title=f"User Profile: {user.get_display_name()}",
        user=user,
        form=form,
        history_logs=stream_history_pagination,
        kavita_reading_stats=kavita_reading_stats,
        kavita_reading_history=kavita_reading_history,
        active_tab=tab,
        is_admin=AdminAccount.query.filter_by(plex_uuid=user.plex_uuid).first() is not None if user.plex_uuid else False,
        stream_stats=stream_stats,
        user_service_types=user_service_types,
        user_server_names=user_server_names,  # Add this context variable
        now_utc=datetime.now(timezone.utc)
    )

@bp.route('/<int:user_id>/delete_history', methods=['POST'])
@login_required
@permission_required('edit_user') # Or a more specific permission if you add one
def delete_stream_history(user_id):
    history_ids_to_delete = request.form.getlist('history_ids[]')
    if not history_ids_to_delete:
        # This can happen if the form is submitted with no boxes checked
        return make_response("<!-- no-op -->", 200)

    try:
        # Convert IDs to integers for safe querying
        ids_as_int = [int(id_str) for id_str in history_ids_to_delete]
        
        # Perform the bulk delete
        num_deleted = db.session.query(StreamHistory).filter(
            StreamHistory.user_id == user_id, # Security check: only delete for the specified user
            StreamHistory.id.in_(ids_as_int)
        ).delete(synchronize_session=False)
        
        db.session.commit()
        
        current_app.logger.info(f"Admin {current_user.id} deleted {num_deleted} history entries for user {user_id}.")
        
        # This payload will show a success toast.
        toast_payload = {
            "showToastEvent": {
                "message": f"Successfully deleted {num_deleted} history entries.",
                "category": "success"
            }
        }
        
        # This will trigger both the toast and a custom event to refresh the table.
        # Note: We now use htmx.trigger() in the template itself for a cleaner flow.
        response = make_response("", 200)
        response.headers['HX-Trigger'] = json.dumps(toast_payload)
        
        return response

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting stream history for user {user_id}: {e}", exc_info=True)
        # Send an error toast on failure
        toast_payload = {
            "showToastEvent": {
                "message": "Error deleting history records.",
                "category": "error"
            }
        }
        response = make_response("", 500)
        response.headers['HX-Trigger'] = json.dumps(toast_payload)
        return response

@bp.route('/<int:user_id>/reset_password', methods=['GET', 'POST'])
@login_required
@permission_required('edit_user')
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    
    # Only allow reset for local accounts created through invites (have password_hash and used_invite_id)
    if not user.password_hash or not user.used_invite_id:
        flash('Password reset is only available for local user accounts created through invites.', 'danger')
        return redirect(url_for('user.view_user', user_id=user_id, tab='settings'))
    
    form = UserResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            user.set_password(form.new_password.data)
            db.session.commit()
            
            log_event(EventType.SETTING_CHANGE, f"Password was reset for user '{user.get_display_name()}'.", user_id=user.id, admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset successfully.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('users/partials/reset_password_modal.html', form=form, user=user), 422
    
    # For GET request, just render the form
    return render_template('users/partials/reset_password_modal.html', form=form, user=user)

@bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    """User account management page - similar to admin account page"""
    # Ensure this is a regular user, not an admin
    if isinstance(current_user, AdminAccount):
        return redirect(url_for('settings.account'))
    
    # Ensure this is a User with password_hash (user account)
    if not isinstance(current_user, User) or not current_user.password_hash:
        flash('Access denied. Please log in with a valid user account.', 'danger')
        return redirect(url_for('auth.user_login'))
    
    from app.forms import ChangePasswordForm, TimezonePreferenceForm
    from app.models import UserPreferences
    
    # Initialize forms
    change_password_form = ChangePasswordForm()
    timezone_form = TimezonePreferenceForm()
    
    # Get user preferences (create if doesn't exist)
    # Note: UserPreferences is designed for admin_id, but we'll adapt it
    user_prefs = UserPreferences.get_timezone_preference(current_user.id)
    timezone_form.timezone_preference.data = user_prefs.get('preference', 'local')
    timezone_form.time_format.data = user_prefs.get('time_format', '12')
    
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'change_password' and change_password_form.validate_on_submit():
            # Verify current password
            if current_user.check_password(change_password_form.current_password.data):
                current_user.set_password(change_password_form.new_password.data)
                db.session.commit()
                log_event(EventType.ADMIN_PASSWORD_CHANGE, f"User '{current_user.get_display_name()}' changed their password.", user_id=current_user.id)
                flash('Password changed successfully.', 'success')
            else:
                flash('Current password is incorrect.', 'danger')
        
        elif form_type == 'timezone' and timezone_form.validate_on_submit():
            UserPreferences.set_timezone_preference(
                current_user.id,
                timezone_form.timezone_preference.data,
                timezone_form.local_timezone.data,
                timezone_form.time_format.data
            )
            flash('Timezone preferences updated successfully.', 'success')
        
        return redirect(url_for('user.account'))
    
    return render_template('user/account.html',
                         title="My Account",
                         user=current_user,
                         change_password_form=change_password_form,
                         timezone_form=timezone_form)