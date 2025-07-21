# File: app/routes/user.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, make_response
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
from app.models import User, AdminAccount, StreamHistory, EventType
from app.forms import UserEditForm
from app.extensions import db
from app.utils.helpers import permission_required, log_event
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import ServiceType
from app.services.media_service_manager import MediaServiceManager
from app.services import user_service
import json

# Note the new blueprint name and singular URL prefix
bp = Blueprint('user', __name__, url_prefix='/user')

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
    user_access_records = UserMediaAccess.query.filter_by(user_id=user.id).all()
    
    available_libraries = {}
    for access in user_access_records:
        try:
            service = MediaServiceFactory.create_service_from_db(access.server)
            if service:
                server_libraries = service.get_libraries()
                for lib in server_libraries:
                    lib_id = lib.get('external_id') or lib.get('id')
                    lib_name = lib.get('name', 'Unknown')
                    if lib_id:
                        available_libraries[str(lib_id)] = lib_name
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {access.server.name}: {e}")
    
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

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
            
            original_library_ids = set(user.allowed_library_ids or [])
            new_library_ids_from_form = set(form.libraries.data or [])
            libraries_changed = (original_library_ids != new_library_ids_from_form)

            update_data = {
                'notes': form.notes.data,
                'is_discord_bot_whitelisted': form.is_discord_bot_whitelisted.data,
                'is_purge_whitelisted': form.is_purge_whitelisted.data,
                'admin_id': current_user.id,
                'new_library_ids': list(new_library_ids_from_form) if libraries_changed else None,
                'allow_downloads': form.allow_downloads.data,
                'allow_4k_transcode': form.allow_4k_transcode.data
            }
            
            user_service.update_user_details(user_id=user.id, **update_data)
            
            if access_expiration_changed:
                if user.access_expires_at is None:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.plex_username}' access expiration cleared.", user_id=user.id, admin_id=current_user.id)
                else:
                    log_event(EventType.SETTING_CHANGE, f"User '{user.plex_username}' access expiration set to {user.access_expires_at.strftime('%Y-%m-%d')}.", user_id=user.id, admin_id=current_user.id)
            
            # This commit saves all changes from user_service and the expiration date
            db.session.commit()
            
            if request.headers.get('HX-Request'):
                # Re-fetch user data to ensure the form is populated with the freshest data after save
                user = User.query.get_or_404(user_id)
                form_after_save = UserEditForm(obj=user)
                
                # Re-populate the dynamic choices and data for the re-rendered form
                form_after_save.libraries.choices = list(available_libraries.items())
                form_after_save.libraries.data = list(user.allowed_library_ids or [])

                # OOB-SWAP LOGIC
                # 1. Render the updated form for the modal (the primary target)
                modal_html = render_template('users/partials/settings_tab.html', form=form_after_save, user=user)

                # 2. Render the updated user card for the OOB swap
                # We need the same context that the main user list uses for a card
                from app.models_media_services import UserMediaAccess
                user_library_access = UserMediaAccess.query.filter_by(user_id=user.id).first()
                user_sorted_libraries = {}
                if user_library_access:
                    # Handle special case for Jellyfin users with '*' (all libraries access)
                    if user_library_access.allowed_library_ids == ['*']:
                        lib_names = ['All Libraries']
                    else:
                        lib_names = [available_libraries.get(str(lib_id), f'Unknown Lib {lib_id}') for lib_id in user_library_access.allowed_library_ids]
                    user_sorted_libraries[user.id] = sorted(lib_names, key=str.lower)
                
                admins_by_uuid = {admin.plex_uuid: admin for admin in AdminAccount.query.filter(AdminAccount.plex_uuid.isnot(None)).all()}

                card_html = render_template(
                    'users/partials/_single_user_card.html',
                    user=user,
                    user_sorted_libraries=user_sorted_libraries,
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
                        "message": f"User '{user.plex_username}' updated successfully.",
                        "category": "success"
                    }
                }
                
                # Create the response and add the HX-Trigger header
                response = make_response(final_html)
                response.headers['HX-Trigger'] = json.dumps(toast_payload)
                return response
            else:
                # Fallback for standard form submissions remains the same
                flash(f"User '{user.plex_username}' updated successfully.", "success")
                return redirect(url_for('user.view_user', user_id=user.id, tab='settings'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating user {user.plex_username}: {e}", exc_info=True)
            flash(f"Error updating user: {e}", "danger")

    if request.method == 'POST' and form.errors:
        if request.headers.get('HX-Request'):
            return render_template('users/partials/settings_tab.html', form=form, user=user), 422

    if request.method == 'GET':
        form.libraries.data = list(user.allowed_library_ids or [])
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
    if tab == 'history':
        page = request.args.get('page', 1, type=int)
        # The session monitor now handles logging active streams directly to the DB.
        # We can just query the table and order by started_at to see the latest,
        # which will include any active streams (where stopped_at is NULL).
        stream_history_pagination = StreamHistory.query.filter_by(user_id=user.id)\
            .order_by(StreamHistory.started_at.desc())\
            .paginate(page=page, per_page=15, error_out=False)
            
    # No need for the elif, stream_stats are now always available on the user object
    # elif tab == 'profile':
    #     stream_stats = user_service.get_user_stream_stats(user_id)

    if request.headers.get('HX-Request') and tab == 'history':
        return render_template('users/partials/history_tab_content.html', 
                             user=user, 
                             history_logs=stream_history_pagination)
        
    return render_template(
        'users/profile.html',
        title=f"User Profile: {user.plex_username}",
        user=user,
        form=form,
        history_logs=stream_history_pagination,
        active_tab=tab,
        is_admin=AdminAccount.query.filter_by(plex_uuid=user.plex_uuid).first() is not None if user.plex_uuid else False,
        stream_stats=stream_stats,
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