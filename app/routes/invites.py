# File: app/routes/invites.py
import uuid
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, g, make_response
from markupsafe import Markup # Import Markup from markupsafefrom flask_login import login_required, current_user 
from datetime import datetime, timezone
from app.utils.timezone_utils import utcnow
from urllib.parse import urlencode, quote as url_quote, urlparse, parse_qs, urlunparse 
from plexapi.myplex import MyPlexAccount 
from plexapi.exceptions import PlexApiException
from app.models import Invite, Setting, EventType, User, InviteUsage, AdminAccount, SettingValueType 
from app.forms import InviteCreateForm, InviteEditForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, calculate_expiry_date, permission_required
from app.utils.plex_auth_helpers import create_plex_pin_login, check_plex_pin_status, get_plex_auth_url
from app.services.media_service_factory import MediaServiceFactory
from app.services.media_service_manager import MediaServiceManager
from app.models_media_services import ServiceType 
import json
import time
from flask_login import login_required, current_user # <<< MAKE SURE login_required IS HERE
import requests

bp = Blueprint('invites', __name__)

DISCORD_API_BASE_URL = 'https://discord.com/api/v10' 

# Removed direct Plex API URLs and headers function - now using plexapi helpers

@bp.route('/manage') 
@login_required
@setup_required
def list_invites():
    page = request.args.get('page', 1, type=int)
    # Get view mode, defaulting to 'cards'
    view_mode = request.args.get('view', Setting.get('DEFAULT_INVITE_VIEW', 'cards'))

    items_per_page_setting = Setting.get('DEFAULT_INVITES_PER_PAGE', current_app.config.get('DEFAULT_INVITES_PER_PAGE', 10))
    items_per_page = int(items_per_page_setting) if items_per_page_setting else 10
    
    # Query logic is unchanged
    query = Invite.query
    filter_status = request.args.get('filter', 'all'); search_path = request.args.get('search_path', '').strip()
    if search_path: query = query.filter(Invite.custom_path.ilike(f"%{search_path}%"))
    now = datetime.utcnow() 
    if filter_status == 'active': query = query.filter(Invite.is_active == True, (Invite.expires_at == None) | (Invite.expires_at > now), (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses))
    elif filter_status == 'expired': query = query.filter(Invite.expires_at != None, Invite.expires_at <= now)
    elif filter_status == 'maxed': query = query.filter(Invite.max_uses != None, Invite.current_uses >= Invite.max_uses)
    elif filter_status == 'inactive': query = query.filter(Invite.is_active == False)
    
    invites_pagination = query.order_by(Invite.created_at.desc()).paginate(page=page, per_page=items_per_page, error_out=False)
    invites_count = query.count()
    
    # Create modal form logic
    form = InviteCreateForm()
    media_service_manager = MediaServiceManager()
    
    # Fetch all active servers
    all_servers = media_service_manager.get_all_servers(active_only=True)

    # For now, populate libraries from the first available server for the form
    available_libraries = {}
    if all_servers:
        first_server_service = MediaServiceFactory.create_service_from_db(all_servers[0])
        try:
            available_libraries = {lib['id']: lib['name'] for lib in first_server_service.get_libraries()}
        except Exception as e:
            current_app.logger.error(f"Could not fetch libraries for server {all_servers[0].name}: {e}")

    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    
    # Discord settings
    discord_oauth_enabled = Setting.get_bool('DISCORD_OAUTH_ENABLED', False)
    bot_is_enabled = Setting.get_bool('DISCORD_BOT_ENABLED', False)
    global_force_sso = Setting.get_bool('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False) or bot_is_enabled
    enable_discord_membership_requirement = Setting.get_bool('ENABLE_DISCORD_MEMBERSHIP_REQUIREMENT', False)
    form.require_discord_auth.data = global_force_sso
    form.require_discord_guild_membership.data = enable_discord_membership_requirement
    
    # If the request is from HTMX, render the list content partial
    if request.headers.get('HX-Request'):
        return render_template('invites/partials/invite_list_content.html', 
                               invites=invites_pagination,
                               all_servers=all_servers,
                               available_libraries=available_libraries,
                               current_view=view_mode,
                               current_per_page=items_per_page)

    # Create grouped_servers for the template
    grouped_servers = {}
    for server in all_servers:
        service_type_name = server.service_type.name.capitalize()
        if service_type_name not in grouped_servers:
            grouped_servers[service_type_name] = []
        grouped_servers[service_type_name].append(server)

    # For a full page load, render the main list.html
    return render_template('invites/list.html', 
                           title="Manage Invites", 
                           invites_count=invites_count, 
                           form=form, 
                           all_servers=all_servers,
                           grouped_servers=grouped_servers,
                           available_libraries=available_libraries, 
                           current_per_page=items_per_page,
                           discord_oauth_enabled=discord_oauth_enabled,
                           global_force_sso=global_force_sso,
                           enable_discord_membership_requirement=enable_discord_membership_requirement,
                           current_view=view_mode) # Pass the view mode

@bp.route('/manage/create', methods=['POST'])
@login_required
@setup_required
@permission_required('create_invites')
def create_invite():
    form = InviteCreateForm()
    media_service_manager = MediaServiceManager()
    
    # Server and library logic
    all_servers = media_service_manager.get_all_servers(active_only=True)
    selected_server_ids_str = request.form.get('server_ids', '')
    selected_server_ids = [id.strip() for id in selected_server_ids_str.split(',') if id.strip()]
    
    # For the form, we'll use libraries from the first selected server for now
    # The frontend will handle per-server library selection via AJAX
    available_libraries = {}
    if selected_server_ids:
        first_server = media_service_manager.get_server_by_id(selected_server_ids[0])
        if first_server:
            service = MediaServiceFactory.create_service_from_db(first_server)
            try:
                available_libraries = {lib['id']: lib['name'] for lib in service.get_libraries()}
            except Exception as e:
                current_app.logger.error(f"Could not fetch libraries for server {first_server.name}: {e}")
    
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    
    # Discord settings
    discord_oauth_enabled = Setting.get_bool('DISCORD_OAUTH_ENABLED', False)
    bot_is_enabled = Setting.get_bool('DISCORD_BOT_ENABLED', False)
    global_force_sso = Setting.get_bool('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False) or bot_is_enabled
    global_require_guild = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)
    
    # Handle dynamic library selection from multiple servers
    if request.method == 'POST':
        # Get all submitted library IDs from the form
        submitted_libraries = request.form.getlist('libraries')
        
        # Use different logic for single vs multi-server invites
        if len(selected_server_ids) == 1:
            # Single server - choices already set above, no need to rebuild
            pass
        else:
            # Multi-server - use conflict handling logic
            all_valid_choices = []
            servers_libraries = {}  # server_id -> {lib_id: lib_name}
            
            # First pass: collect all libraries from all servers
            for server_id in selected_server_ids:
                server = media_service_manager.get_server_by_id(server_id)
                if server:
                    service = MediaServiceFactory.create_service_from_db(server)
                    try:
                        server_libraries = service.get_libraries()
                        server_lib_dict = {lib['id']: lib['name'] for lib in server_libraries}
                        servers_libraries[server.id] = {
                            'server': server,
                            'libraries': server_lib_dict
                        }
                    except Exception as e:
                        current_app.logger.error(f"Error fetching libraries for validation from server {server.name}: {e}")
            
            # Second pass: detect conflicts and build choices
            for server_id, server_data in servers_libraries.items():
                server = server_data['server']
                server_lib_dict = server_data['libraries']
                
                for lib_id, lib_name in server_lib_dict.items():
                    # Check if this lib_id exists in other servers
                    conflicts_with_other_servers = any(
                        lib_id in other_data['libraries'] 
                        for other_server_id, other_data in servers_libraries.items() 
                        if other_server_id != server_id
                    )
                    
                    if conflicts_with_other_servers:
                        # Use prefixed ID for conflicts
                        prefixed_lib_id = f"{server.id}_{lib_id}"
                        all_valid_choices.append((prefixed_lib_id, f"[{server.name}] {lib_name}"))
                    else:
                        # No conflict, use original ID
                        all_valid_choices.append((lib_id, lib_name))
            
            # Update form choices for multi-server
            form.libraries.choices = all_valid_choices
        
        # Set the form data
        if submitted_libraries:
            form.libraries.data = submitted_libraries

    toast_message_text = ""
    toast_category = "info"

    if form.validate_on_submit():
        # Validate that at least one server is selected
        if not selected_server_ids:
            # Add a custom error for server selection
            flash("Please select at least one server to grant access to.", "danger")
            grouped_servers = {}
            for server in all_servers:
                service_type_name = server.service_type.name.capitalize()
                if service_type_name not in grouped_servers:
                    grouped_servers[service_type_name] = []
                grouped_servers[service_type_name].append(server)
            return render_template('invites/partials/create_invite_modal.html', form=form, grouped_servers=grouped_servers, available_libraries=available_libraries, discord_oauth_enabled=discord_oauth_enabled, global_force_sso=global_force_sso, global_require_guild=global_require_guild), 422
        
        custom_path = form.custom_path.data.strip() if form.custom_path.data else None
        if custom_path:
            existing_invite = Invite.query.filter(Invite.custom_path == custom_path, Invite.is_active == True).first()
            if existing_invite and existing_invite.is_usable:
                error_msg = f"An active and usable invite with the custom path '{custom_path}' already exists."
                form.custom_path.errors.append(error_msg)
                # Need to pass grouped_servers back to the template on error
                grouped_servers = {}
                for server in all_servers:
                    service_type_name = server.service_type.name.capitalize()
                    if service_type_name not in grouped_servers:
                        grouped_servers[service_type_name] = []
                    grouped_servers[service_type_name].append(server)
                return render_template('invites/partials/create_invite_modal.html', form=form, grouped_servers=grouped_servers, available_libraries=available_libraries, discord_oauth_enabled=discord_oauth_enabled, global_force_sso=global_force_sso, global_require_guild=global_require_guild), 422
        
        # Convert date object to datetime at the end of the selected day
        expires_at = datetime.combine(form.expires_at.data, datetime.max.time()) if form.expires_at.data else None
        
        membership_duration = None
        if form.membership_expires_at.data:
            delta = form.membership_expires_at.data - date.today()
            membership_duration = delta.days + 1 # Add 1 to include the current day

        max_uses = form.number_of_uses.data if form.number_of_uses.data and form.number_of_uses.data > 0 else None
        
        new_invite = Invite(
            custom_path=custom_path, expires_at=expires_at, max_uses=max_uses,
            grant_library_ids=form.libraries.data or [],
            allow_downloads=form.allow_downloads.data,
            invite_to_plex_home=form.invite_to_plex_home.data,
            allow_live_tv=form.allow_live_tv.data,
            membership_duration_days=membership_duration, created_by_admin_id=current_user.id,
            require_discord_auth=form.require_discord_auth.data,
            require_discord_guild_membership=form.require_discord_guild_membership.data,
            server_id=selected_server_ids[0] if selected_server_ids else None  # Keep for backward compatibility
        )
        try:
            db.session.add(new_invite)
            db.session.flush()  # Flush to get the invite ID
            
            # Add all selected servers to the invite
            if selected_server_ids:
                for server_id in selected_server_ids:
                    server = media_service_manager.get_server_by_id(server_id)
                    if server:
                        new_invite.servers.append(server)
            
            db.session.commit()
            invite_url = new_invite.get_full_url(g.app_base_url or request.url_root.rstrip('/'))
            log_msg_details = f"Downloads: {'Enabled' if new_invite.allow_downloads else 'Disabled'}."
            if new_invite.membership_duration_days: log_msg_details += f" Membership: {new_invite.membership_duration_days} days."
            else: log_msg_details += " Membership: Permanent."
            if hasattr(new_invite, 'force_discord_auth') and new_invite.force_discord_auth is not None: log_msg_details += f" Force Discord Auth: {new_invite.force_discord_auth} (Override)."
            if hasattr(new_invite, 'force_guild_membership') and new_invite.force_guild_membership is not None: log_msg_details += f" Force Guild Membership: {new_invite.force_guild_membership} (Override)."
                
            log_event(EventType.INVITE_CREATED, f"Invite created: Path='{custom_path or new_invite.token}'. {log_msg_details}", invite_id=new_invite.id, admin_id=current_user.id)
            toast_message_text = f"Invite link created successfully!"; toast_category = "success"
            if request.headers.get('HX-Request'):
                response = make_response(""); response.status_code = 204 
                trigger_payload = {"refreshInvitesList": True, "showToastEvent": {"message": toast_message_text, "category": toast_category}}
                response.headers['HX-Trigger-After-Swap'] = json.dumps(trigger_payload)
                return response
            flash(f"Invite link created: {invite_url}", toast_category) 
            return redirect(url_for('invites.list_invites'))
        except Exception as e:
            db.session.rollback(); current_app.logger.error(f"Error creating invite in DB: {e}", exc_info=True)
            toast_message_text = f"Error creating invite: {str(e)[:100]}"; toast_category = "danger"
            if request.headers.get('HX-Request'):
                response = make_response("Error saving invite to database.", 500) 
                response.headers['HX-Trigger-After-Swap'] = json.dumps({"showToastEvent": {"message": toast_message_text, "category": toast_category}})
                return response
            flash(toast_message_text, toast_category); return redirect(url_for('invites.list_invites'))
    else: 
        if request.headers.get('HX-Request'):
            grouped_servers = {}
            for server in all_servers:
                service_type_name = server.service_type.name.capitalize()
                if service_type_name not in grouped_servers:
                    grouped_servers[service_type_name] = []
                grouped_servers[service_type_name].append(server)
            return render_template('invites/partials/create_invite_modal.html', form=form, grouped_servers=grouped_servers, available_libraries=available_libraries, discord_oauth_enabled=discord_oauth_enabled, global_force_sso=global_force_sso, global_require_guild=global_require_guild), 422
        for field, errors_list in form.errors.items():
            for error in errors_list: flash(f"Error in {getattr(form, field).label.text}: {error}", "danger")
        return redirect(url_for('invites.list_invites'))

@bp.route('/manage/toggle-status/<int:invite_id>', methods=['POST'])
@login_required
@setup_required
@permission_required('edit_invites')
def toggle_invite_status(invite_id):
    """Toggle invite active/inactive status"""
    invite = Invite.query.get_or_404(invite_id)
    
    try:
        # Toggle the status
        invite.is_active = not invite.is_active
        db.session.commit()
        
        status_text = "activated" if invite.is_active else "deactivated"
        log_event(EventType.SETTING_CHANGE, f"Invite '{invite.custom_path or invite.token}' (ID: {invite_id}) {status_text} by admin.", invite_id=invite_id, admin_id=current_user.id)
        
        # Return the updated single invite card
        return render_template('invites/partials/single_invite_card.html', invite=invite)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling invite status {invite_id}: {e}")
        return f'<div class="alert alert-error"><span>Error updating invite status: {e}</span></div>', 500

@bp.route('/manage/delete/<int:invite_id>', methods=['DELETE'])
@login_required
@setup_required
@permission_required('delete_invites')
def delete_invite(invite_id):
    invite = Invite.query.get_or_404(invite_id)
    path_or_token = invite.custom_path or invite.token # For logging and toast message
    mum_invite_id_for_log = invite.id # Store before deletion

    try:
        db.session.delete(invite)
        db.session.commit()
        
        log_event(EventType.INVITE_DELETED, 
                  f"Invite '{path_or_token}' deleted.", 
                  invite_id=mum_invite_id_for_log, # Use the stored ID for log
                  admin_id=current_user.id)
        
        toast_message = f"Invite '{path_or_token}' deleted successfully."
        toast_category = "success"
        
        # Prepare headers for HTMX response
        headers = {}
        trigger_payload = {
            "showToastEvent": {"message": toast_message, "category": toast_category},
            # "refreshInvitesList": True # This will be triggered by the swap on the row itself if list needs full refresh
                                         # Or, if removing the row isn't enough and you want the whole list to re-fetch pagination etc.
                                         # For now, let's assume row removal is sufficient immediate feedback.
                                         # If pagination needs update, the list container should also listen for a specific event
                                         # or be triggered by the successful deletion.
                                         # Let's keep it simple: the row is removed, toast is shown.
                                         # If the count on the page title needs updating, that requires refreshing more.
                                         # For full refresh including count:
            "refreshInvitesList": True 
        }
        headers['HX-Trigger'] = json.dumps(trigger_payload)
        
        # HTMX will remove the row based on hx-target and hx-swap="outerHTML".
        # We return an empty response with a 200 OK, and the headers do the work.
        current_app.logger.info(f"Invite '{path_or_token}' deleted. Sending HX-Trigger: {headers['HX-Trigger']}")
        return make_response("", 200, headers)

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting invite '{path_or_token}': {e}", exc_info=True)
        log_event(EventType.ERROR_GENERAL, 
                  f"Error deleting invite '{path_or_token}': {str(e)}", 
                  invite_id=mum_invite_id_for_log, 
                  admin_id=current_user.id)
        
        toast_message = f"Error deleting invite '{path_or_token}'. Please try again."
        toast_category = "error"
        headers = {}
        trigger_payload = {
            "showToastEvent": {"message": toast_message, "category": toast_category}
            # Optionally, still trigger a list refresh to ensure UI consistency on error
            # "refreshInvitesList": True 
        }
        headers['HX-Trigger'] = json.dumps(trigger_payload)
        
        # Return an error status that HTMX can interpret as a failure for the swap,
        # but still send the toast.
        # A 200 with an error toast is also fine, as the swap won't happen on error if hx-swap handles errors.
        # For simplicity, let's still return 200 but the toast indicates error.
        # The hx-target on the button for row removal will still try to happen unless swap specifies otherwise for errors.
        # Since the row might not be deleted on error, let's not rely on outerHTML swap for error feedback.
        # It's better to just show the toast.
        return make_response("", 200, headers) # Still 200, toast will show error

@bp.route('/manage/usages/<int:invite_id>', methods=['GET'])
@login_required
@setup_required
def view_invite_usages(invite_id):
    invite = Invite.query.get_or_404(invite_id)
    usages = InviteUsage.query.filter_by(invite_id=invite.id).order_by(InviteUsage.used_at.desc()).all()
    return render_template('invites/partials/usage_modal.html', invite=invite, usages=usages)

@bp.route('/invite/<invite_path_or_token>', methods=['GET', 'POST'])
@setup_required 
def process_invite_form(invite_path_or_token):
    from flask_wtf import FlaskForm
    from app.services import invite_service
    invite, error_message_from_validation = invite_service.validate_invite_usability(invite_path_or_token)
    
    if request.method == 'GET' and not error_message_from_validation and invite:
        log_event(EventType.INVITE_VIEWED, f"Invite '{invite.custom_path or invite.token}' (ID: {invite.id}) viewed/accessed.", invite_id=invite.id)

    if error_message_from_validation: 
        return render_template('invites/public_invite.html', error=error_message_from_validation, invite=None, form=FlaskForm(), discord_sso_is_mandatory=False, show_discord_button=False)

    if not invite:
        flash("The invite link is invalid or no longer available.", "danger")
        return redirect(url_for('invites.invite_landing_page'))

    form_instance = FlaskForm()
    already_authenticated_plex_user_info = session.get(f'invite_{invite.id}_plex_user')
    already_authenticated_discord_user_info = session.get(f'invite_{invite.id}_discord_user')
    
    # --- MODIFIED: Determine effective Discord settings using invite fields ---
    oauth_is_generally_enabled = Setting.get_bool('DISCORD_OAUTH_ENABLED', False)
    
    effective_require_sso = invite.require_discord_auth
    effective_require_guild = invite.require_discord_guild_membership

    # These settings are fetched for display purposes if guild membership is required
    setting_discord_guild_id = Setting.get('DISCORD_GUILD_ID')
    setting_discord_server_invite_url = Setting.get('DISCORD_SERVER_INVITE_URL')
    show_discord_button = oauth_is_generally_enabled
    
    # --- NEW: Get server name for display ---
    server_name = g.app_name or 'the server'
    if invite and invite.server_id:
        from app.models_media_services import MediaServer
        server = MediaServer.query.get(invite.server_id)
        if server:
            server_name = server.name
    # --- END NEW ---

    # Get all servers for template logic
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    # Check if user accounts are enabled
    allow_user_accounts = Setting.get_bool('ALLOW_USER_ACCOUNTS', False)
    user_account_created = session.get(f'invite_{invite.id}_user_account_created', False)
    
    # Create user account form if needed
    account_form = None
    if allow_user_accounts:
        from app.forms import UserAccountCreationForm
        account_form = UserAccountCreationForm()

    if request.method == 'POST':
        auth_method = request.form.get('auth_method'); action_taken = request.form.get('action')
        
        # Handle user account creation if enabled
        if action_taken == 'create_user_account' and allow_user_accounts:
            from app.forms import UserAccountCreationForm
            from werkzeug.security import generate_password_hash
            
            account_form = UserAccountCreationForm()
            if account_form.validate_on_submit():
                try:
                    # Create new user account
                    new_user = User(
                        primary_username=account_form.username.data,
                        primary_email=account_form.email.data,
                        password_hash=generate_password_hash(account_form.password.data),
                        created_at=utcnow(),
                        used_invite_id=invite.id
                    )
                    db.session.add(new_user)
                    db.session.commit()
                    
                    # Mark account as created in session
                    session[f'invite_{invite.id}_user_account_created'] = True
                    session[f'invite_{invite.id}_user_account_id'] = new_user.id
                    
                    flash("Account created successfully! Please continue with the authentication steps.", "success")
                    log_event(EventType.MUM_USER_ADDED_FROM_PLEX, f"User account '{account_form.username.data}' created via invite {invite.id}", user_id=new_user.id, invite_id=invite.id)
                    
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"Error creating user account for invite {invite.id}: {e}")
                    flash("Error creating account. Please try again.", "error")
            else:
                # Form validation failed, show errors
                for field, errors in account_form.errors.items():
                    for error in errors:
                        flash(f"{getattr(account_form, field).label.text}: {error}", "error")
        
        elif auth_method == 'plex':
            session['plex_oauth_invite_id'] = invite.id 
            try:
                # Use direct API calls like the sample code instead of plexapi
                import requests
                from urllib.parse import urlencode
                
                # Generate headers like the sample code
                app_name = Setting.get('APP_NAME', 'MUM')
                client_id = f"MUM-App-v1-InvitePlexLink-{str(invite.id)[:8]}"
                
                # Step 1: Create PIN using direct API call
                pin_response = requests.post(
                    "https://plex.tv/api/v2/pins",
                    headers={"Accept": "application/json"},
                    data={
                        "strong": "true",
                        "X-Plex-Product": app_name,
                        "X-Plex-Client-Identifier": client_id,
                    },
                )
                
                if pin_response.status_code != 201:
                    raise Exception(f"Failed to create PIN: {pin_response.status_code} - {pin_response.text}")
                
                pin_data = pin_response.json()
                pin_id = pin_data["id"]
                pin_code = pin_data["code"]
                
                current_app.logger.debug(f"PIN creation - PIN code: {pin_code}")
                current_app.logger.debug(f"PIN creation - PIN ID: {pin_id}")
                
                # Store the necessary details for the callback
                session['plex_pin_code_invite_flow'] = pin_code
                session['plex_pin_id_invite_flow'] = pin_id
                session['plex_client_id_invite_flow'] = client_id
                session['plex_app_name_invite_flow'] = app_name
                
                # Step 2: Generate auth URL like the sample code
                app_base_url = Setting.get('APP_BASE_URL', request.url_root.rstrip('/'))
                callback_path_segment = url_for('invites.plex_oauth_callback', _external=False)
                forward_url_to_our_app = f"{app_base_url.rstrip('/')}{callback_path_segment}"
                
                encoded_params = urlencode({
                    "clientID": client_id,
                    "code": pin_code,
                    "context[device][product]": app_name,
                    "forwardUrl": forward_url_to_our_app,
                })
                auth_url_for_user_to_visit = f"https://app.plex.tv/auth#?{encoded_params}"
                
                return redirect(auth_url_for_user_to_visit)
            except Exception as e:
                flash(f"Could not initiate Plex login: {str(e)[:150]}", "danger")
                log_event(EventType.ERROR_PLEX_API, f"Invite {invite.id}: Plex PIN init failed: {e}", invite_id=invite.id)
        
        elif auth_method == 'discord': # ... (Discord auth logic is unchanged)
            if not show_discord_button: flash("Discord login is not currently available.", "warning")
            else:
                admin_provided_oauth_url = Setting.get('DISCORD_OAUTH_AUTH_URL'); client_id_from_settings = Setting.get('DISCORD_CLIENT_ID')
                if admin_provided_oauth_url and client_id_from_settings:
                    session['discord_oauth_invite_id'] = invite.id; session['discord_oauth_state_invite'] = str(uuid.uuid4())
                    parsed_url = urlparse(admin_provided_oauth_url)
                    query_params = parse_qs(parsed_url.query); query_params['state'] = [session['discord_oauth_state_invite']]
                    expected_redirect_uri = Setting.get('DISCORD_REDIRECT_URI_INVITE') or url_for('invites.discord_oauth_callback', _external=True)
                    if 'redirect_uri' not in query_params or query_params.get('redirect_uri', [''])[0] != expected_redirect_uri: query_params['redirect_uri'] = [expected_redirect_uri]
                    final_query_string = urlencode(query_params, doseq=True)
                    final_discord_auth_url = urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, final_query_string, parsed_url.fragment))
                    return redirect(final_discord_auth_url)
                elif client_id_from_settings:
                    session['discord_oauth_invite_id'] = invite.id; session['discord_oauth_state_invite'] = str(uuid.uuid4())
                    redirect_uri = Setting.get('DISCORD_REDIRECT_URI_INVITE') or url_for('invites.discord_oauth_callback', _external=True)
                    required_scopes = "identify email guilds"; params = {'client_id': client_id_from_settings, 'redirect_uri': redirect_uri, 'response_type': 'code', 'scope': required_scopes, 'state': session['discord_oauth_state_invite']}
                    discord_auth_url = f"{DISCORD_API_BASE_URL}/oauth2/authorize?{urlencode(params)}"
                    return redirect(discord_auth_url)
                else: flash("Discord integration is not properly configured by admin for login.", "danger")

        elif action_taken == 'accept_invite':
            if not already_authenticated_plex_user_info: flash("Please sign in with Plex first to accept the invite.", "warning")
            elif effective_require_sso and not already_authenticated_discord_user_info: flash("Discord account linking is required for this invite. Please link your Discord account.", "warning")
            else:
                # Check if user account was created during this invite flow
                existing_user_id = session.get(f'invite_{invite.id}_user_account_id')
                current_app.logger.debug(f"Invite acceptance - Looking for session key: invite_{invite.id}_user_account_id")
                current_app.logger.debug(f"Invite acceptance - Found existing_user_id: {existing_user_id}")
                current_app.logger.debug(f"Invite acceptance - Session keys: {list(session.keys())}")
                
                success, result_object_or_message = invite_service.accept_invite_and_grant_access(
                    invite=invite, 
                    plex_user_uuid=already_authenticated_plex_user_info['uuid'], 
                    plex_username=already_authenticated_plex_user_info['username'], 
                    plex_email=already_authenticated_plex_user_info['email'], 
                    plex_thumb=already_authenticated_plex_user_info['thumb'], 
                    # Pass the entire dictionary as a single argument
                    discord_user_info=already_authenticated_discord_user_info, 
                    ip_address=request.remote_addr,
                    existing_user_id=existing_user_id
                )
                if success: 
                    session.pop(f'invite_{invite.id}_plex_user', None); session.pop(f'invite_{invite.id}_discord_user', None)
                    flash(f"Welcome, {already_authenticated_plex_user_info['username']}! Access granted to the Plex server.", "success")
                    return redirect(url_for('invites.invite_success', username=already_authenticated_plex_user_info['username']))
                else: flash(f"Failed to accept invite: {result_object_or_message}", "danger")
        
        return redirect(url_for('invites.process_invite_form', invite_path_or_token=invite_path_or_token))

    # Determine if we should use the steps-based template
    # Use steps if:
    # - User accounts are enabled (account creation needs to be step 1)
    # - Discord OAuth is enabled 
    # - Multiple servers are available
    has_multiple_servers_available = len(all_servers) > 1
    
    use_steps_template = allow_user_accounts or show_discord_button or has_multiple_servers_available
    
    template_name = 'invites/public_invite_steps.html' if use_steps_template else 'invites/public_invite.html'
    
    return render_template(template_name, 
                           form=form_instance, 
                           invite=invite, 
                           error=None,
                           invite_path_or_token=invite_path_or_token, 
                           # Pass the effective values to the template
                           discord_sso_is_mandatory=effective_require_sso,
                           setting_require_guild_membership=effective_require_guild,
                           show_discord_button=show_discord_button,
                           already_authenticated_plex_user=already_authenticated_plex_user_info, 
                           already_authenticated_discord_user=already_authenticated_discord_user_info,
                           setting_discord_guild_id=setting_discord_guild_id,
                           setting_discord_server_invite_url=setting_discord_server_invite_url,
                           server_name=server_name,
                           allow_user_accounts=allow_user_accounts,
                           user_account_created=user_account_created,
                           account_form=account_form
                           )

@bp.route('/plex_callback') # Path is /invites/plex_callback
@setup_required
def plex_oauth_callback():
    invite_id = session.get('plex_oauth_invite_id')
    pin_code_from_session = session.get('plex_pin_code_invite_flow')
    pin_id_from_session = session.get('plex_pin_id_invite_flow')
    client_id_from_session = session.get('plex_client_id_invite_flow')
    app_name_from_session = session.get('plex_app_name_invite_flow')
    
    current_app.logger.debug(f"Plex callback - invite_id from session: {invite_id}")
    current_app.logger.debug(f"Plex callback - pin_code_from_session: {pin_code_from_session}")
    current_app.logger.debug(f"Plex callback - pin_id_from_session: {pin_id_from_session}")
    current_app.logger.debug(f"Plex callback - client_id_from_session: {client_id_from_session}")
    
    invite_path_or_token_for_redirect = "error_path" 
    if invite_id: 
        temp_invite_for_redirect = Invite.query.get(invite_id)
        if temp_invite_for_redirect: 
            invite_path_or_token_for_redirect = temp_invite_for_redirect.custom_path or temp_invite_for_redirect.token
    
    fallback_redirect = url_for('invites.process_invite_form', invite_path_or_token=invite_path_or_token_for_redirect)
    
    if not invite_id or not pin_code_from_session or not pin_id_from_session or not client_id_from_session:
        flash('Plex login callback invalid. Try invite again.', 'danger')
        # Clear all session keys related to this flow
        session.pop('plex_oauth_invite_id', None)
        session.pop('plex_pin_code_invite_flow', None)
        session.pop('plex_pin_id_invite_flow', None)
        session.pop('plex_client_id_invite_flow', None)
        session.pop('plex_app_name_invite_flow', None)
        return redirect(fallback_redirect) 
    
    invite = Invite.query.get(invite_id)
    if not invite: 
        flash('Invite not found. Try again.', 'danger')
        return redirect(url_for('invites.invite_landing_page'))
    
    try:
        from plexapi.myplex import MyPlexPinLogin
        
        # Use direct API approach exactly like the sample code
        current_app.logger.debug(f"Plex callback - Using direct API approach to check PIN ID {pin_id_from_session} (PIN code: {pin_code_from_session})")
        
        import requests
        
        # Retry mechanism for OAuth timing issues
        max_retries = 3
        retry_delay = 1  # seconds
        plex_auth_token = None
        
        for attempt in range(max_retries):
            current_app.logger.debug(f"Plex callback - Authentication attempt {attempt + 1}/{max_retries}")
            
            try:
                # Make direct API call exactly like the sample code
                headers = {"accept": "application/json"}
                data = {"code": pin_code_from_session, "X-Plex-Client-Identifier": client_id_from_session}
                
                check_url = f"https://plex.tv/api/v2/pins/{pin_id_from_session}"
                response = requests.get(check_url, headers=headers, data=data, timeout=10)
                
                current_app.logger.debug(f"Plex callback - PIN check response status: {response.status_code}")
                current_app.logger.debug(f"Plex callback - PIN check response text: {response.text[:500]}")
                
                if response.status_code == 200:
                    pin_data = response.json()
                    current_app.logger.debug(f"Plex callback - PIN data: {pin_data}")
                    
                    if pin_data.get('authToken'):
                        plex_auth_token = pin_data['authToken']
                        current_app.logger.info(f"Plex callback - Successfully retrieved auth token via direct API for PIN {pin_code_from_session}")
                        break
                    else:
                        current_app.logger.debug(f"Plex callback - PIN {pin_code_from_session} not yet authenticated (no authToken)")
                elif response.status_code == 404:
                    current_app.logger.warning(f"Plex callback - PIN {pin_code_from_session} not found (404)")
                else:
                    current_app.logger.warning(f"Plex callback - PIN check failed with status {response.status_code}: {response.text[:200]}")
                    
            except Exception as e:
                current_app.logger.error(f"Plex callback - Error checking PIN via API: {e}")
                
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                current_app.logger.debug(f"Plex callback - Waiting {retry_delay}s before retry...")
                time.sleep(retry_delay)
        
        if not plex_auth_token:
            current_app.logger.warning(f"Plex callback - PIN {pin_code_from_session} not authenticated after {max_retries} attempts")
            flash('Plex PIN not yet authenticated. Please complete the authentication on plex.tv/link', 'warning')
            return redirect(fallback_redirect)

        plex_account = MyPlexAccount(token=plex_auth_token)
        
        session[f'invite_{invite.id}_plex_user'] = {
            'id': getattr(plex_account, 'id', None), 
            'uuid': getattr(plex_account, 'uuid', None), 
            'username': getattr(plex_account, 'username', None), 
            'email': getattr(plex_account, 'email', None), 
            'thumb': getattr(plex_account, 'thumb', None)
        }
        log_event(EventType.INVITE_USED_SUCCESS_PLEX, f"Plex auth success for {plex_account.username} on invite {invite.id}.", invite_id=invite.id)

    except PlexApiException as e_plex:
        flash(f'Plex API error: {str(e_plex)}', 'danger')
        log_event(EventType.ERROR_PLEX_API, f"Invite {invite.id}: Plex PIN check PlexApiException: {e_plex}", invite_id=invite.id)
    except Exception as e: 
        flash(f"Error during Plex login for invite: {str(e)[:150]}", "danger")
        log_event(EventType.ERROR_PLEX_API, f"Invite {invite.id}: Plex callback error: {e}", invite_id=invite.id)
    finally: 
        session.pop('plex_oauth_invite_id', None)
        session.pop('plex_pin_code_invite_flow', None)
        session.pop('plex_headers_invite_flow', None)
        
    return redirect(fallback_redirect)

@bp.route('/discord_callback')
@setup_required
def discord_oauth_callback():
    invite_id_from_session = session.get('discord_oauth_invite_id')
    returned_state = request.args.get('state')
    
    invite_path_for_redirect_on_error = "unknown_invite_path"
    invite_object_for_redirect = None
    if invite_id_from_session:
        invite_object_for_redirect = Invite.query.get(invite_id_from_session)
        if invite_object_for_redirect:
            invite_path_for_redirect_on_error = invite_object_for_redirect.custom_path or invite_object_for_redirect.token
    
    public_invite_page_url_with_path = url_for('invites.process_invite_form', invite_path_or_token=invite_path_for_redirect_on_error)
    generic_invite_landing_url = url_for('invites.invite_landing_page')

    if not invite_id_from_session or not returned_state or returned_state != session.pop('discord_oauth_state_invite', None):
        flash('Discord login failed: Invalid session or state. Please try the invite link again.', 'danger')
        current_app.logger.warning("Discord OAuth Callback: Invalid state or missing invite_id in session.")
        return redirect(public_invite_page_url_with_path if invite_object_for_redirect else generic_invite_landing_url)

    if not invite_object_for_redirect:
        flash('Discord login failed: Invite information is no longer available. Please try a fresh invite link.', 'danger')
        current_app.logger.warning(f"Discord OAuth Callback: Invite ID {invite_id_from_session} not found in DB after state check.")
        return redirect(generic_invite_landing_url)

    code = request.args.get('code')
    if not code:
        error_description = request.args.get("error_description", "Authentication with Discord failed. No authorization code received.")
        flash(f'Discord login failed: {error_description}', 'danger')
        log_event(EventType.ERROR_DISCORD_API, f"Discord OAuth callback failed (no code): {error_description}", invite_id=invite_id_from_session)
        return redirect(public_invite_page_url_with_path)

    client_id = Setting.get('DISCORD_CLIENT_ID')
    client_secret = Setting.get('DISCORD_CLIENT_SECRET')
    redirect_uri_for_token_exchange = Setting.get('DISCORD_REDIRECT_URI_INVITE') 
    
    if not (client_id and client_secret and redirect_uri_for_token_exchange):
        flash('Discord integration is not properly configured by the admin. Cannot complete login.', 'danger')
        log_event(EventType.ERROR_DISCORD_API, "Discord OAuth callback failed: MUM settings (client_id/secret/redirect_uri_invite) missing.", invite_id=invite_id_from_session)
        return redirect(public_invite_page_url_with_path)

    token_url = f"{DISCORD_API_BASE_URL}/oauth2/token"
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri_for_token_exchange
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_response = requests.post(token_url, data=payload, headers=headers, timeout=15)
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data['access_token']
        
        user_info_url = f"{DISCORD_API_BASE_URL}/users/@me"
        auth_headers = {'Authorization': f'Bearer {access_token}'}
        user_response = requests.get(user_info_url, headers=auth_headers, timeout=10)
        user_response.raise_for_status()
        discord_user_data = user_response.json()
        
        discord_username_from_oauth = f"{discord_user_data['username']}#{discord_user_data['discriminator']}" if discord_user_data.get('discriminator') and discord_user_data.get('discriminator') != '0' else discord_user_data['username']
        
        # Determine the effective "Require Guild Membership" setting for this specific invite
        if invite_object_for_redirect.force_guild_membership is not None:
            effective_require_guild = invite_object_for_redirect.force_guild_membership
        else:
            effective_require_guild = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)
        
        if effective_require_guild:
            current_app.logger.info(f"Discord OAuth Callback: Guild membership is required for invite {invite_object_for_redirect.id}.")
            configured_guild_id_str = Setting.get('DISCORD_GUILD_ID')
            if not configured_guild_id_str or not configured_guild_id_str.isdigit():
                flash('Server configuration error: Target Discord Server ID for membership check is not set or invalid. Please contact admin.', 'danger')
                session.pop('discord_oauth_invite_id', None)
                return redirect(public_invite_page_url_with_path)
            
            configured_guild_id = int(configured_guild_id_str)
            user_guilds_url = f"{DISCORD_API_BASE_URL}/users/@me/guilds"
            guilds_response = requests.get(user_guilds_url, headers=auth_headers, timeout=10)
            guilds_response.raise_for_status()
            user_guilds_list = guilds_response.json()
            is_member = any(str(g.get('id')) == str(configured_guild_id) for g in user_guilds_list)

            if not is_member:
                server_invite_link = Setting.get('DISCORD_SERVER_INVITE_URL')
                error_html = "To accept this invite, you must be a member of our Discord server."
                if server_invite_link: error_html += f" Please join using the button below and then attempt to link your Discord account again on the invite page."
                else: error_html += " Please contact an administrator for an invite to the server."
                flash(Markup(error_html), 'warning')
                log_event(EventType.DISCORD_BOT_GUILD_MEMBER_CHECK_FAIL, f"User {discord_username_from_oauth} (ID: {discord_user_data['id']}) failed guild membership check for guild {configured_guild_id}.", invite_id=invite_object_for_redirect.id)
                session.pop('discord_oauth_invite_id', None)
                return redirect(public_invite_page_url_with_path)
        
        # If all checks pass, store all relevant info in the session
        discord_user_info_for_session = {
            'id': discord_user_data.get('id'), 
            'username': discord_username_from_oauth,
            'avatar': discord_user_data.get('avatar'),
            'email': discord_user_data.get('email'),
            'verified': discord_user_data.get('verified')
        }
        session[f'invite_{invite_object_for_redirect.id}_discord_user'] = discord_user_info_for_session
        log_event(EventType.INVITE_USED_SUCCESS_DISCORD, f"Discord auth success for {discord_username_from_oauth} on invite {invite_object_for_redirect.id}.", invite_id=invite_object_for_redirect.id)

    except requests.exceptions.HTTPError as e_http:
        error_message = f"Discord API Error ({e_http.response.status_code})"
        try: 
            error_json = e_http.response.json()
            error_message = error_json.get('error_description', error_json.get('message', error_message))
        except ValueError: 
            error_message = e_http.response.text[:200] if e_http.response.text else error_message
        flash(f'Failed to link Discord: {error_message}', 'danger')
        log_event(EventType.ERROR_DISCORD_API, f"Invite {invite_id_from_session}: Discord callback HTTPError: {error_message}", invite_id=invite_id_from_session, details={'status_code': e_http.response.status_code})
    except Exception as e_gen:
        flash('An unexpected error occurred during Discord login. Please try again.', 'danger')
        log_event(EventType.ERROR_DISCORD_API, f"Invite {invite_id_from_session}: Unexpected Discord callback error: {e_gen}", invite_id=invite_id_from_session, details={'error': str(e_gen)})
    finally:
        session.pop('discord_oauth_invite_id', None) 

    return redirect(public_invite_page_url_with_path)

@bp.route('/success') # Path is /invites/success
@setup_required 
def invite_success():
    username = request.args.get('username', 'there'); plex_app_url = "https://app.plex.tv"
    allow_user_accounts = Setting.get_bool('ALLOW_USER_ACCOUNTS', False)
    return render_template('invites/success.html', username=username, plex_app_url=plex_app_url, allow_user_accounts=allow_user_accounts)

@bp.route('/') # Defines the base /invites/ path
@setup_required 
def invite_landing_page(): # Renamed from placeholder
    flash("Please use a specific invite link.", "info")
    if current_user.is_authenticated: 
        return redirect(url_for('dashboard.index'))
    # If not authenticated and no specific invite, perhaps redirect to admin login or a generic info page
    return redirect(url_for('auth.app_login')) 

@bp.route('/manage/edit/<int:invite_id>', methods=['GET'])
@login_required
@setup_required
def get_edit_invite_form(invite_id):
    invite = Invite.query.get_or_404(invite_id)
    form = InviteEditForm(obj=invite)

    # Populate form with existing data
    if invite.expires_at and invite.expires_at > datetime.now(timezone.utc):
        days_left = (invite.expires_at - datetime.now(timezone.utc)).days + 1
        form.expires_in_days.data = days_left if days_left > 0 else 0
    else:
        form.expires_in_days.data = 0
    
    form.number_of_uses.data = invite.max_uses or 0
    form.membership_duration_days.data = invite.membership_duration_days
    form.allow_downloads.data = invite.allow_downloads
    form.invite_to_plex_home.data = invite.invite_to_plex_home
    form.allow_live_tv.data = invite.allow_live_tv
    form.grant_purge_whitelist.data = invite.grant_purge_whitelist
    form.grant_bot_whitelist.data = invite.grant_bot_whitelist
    
    media_service_manager = MediaServiceManager()
    all_servers = media_service_manager.get_all_servers(active_only=True)
    
    grouped_servers = {}
    for server in all_servers:
        service_type_name = server.service_type.name.capitalize()
        if service_type_name not in grouped_servers:
            grouped_servers[service_type_name] = []
        grouped_servers[service_type_name].append(server)

    # Get libraries from all attached servers
    available_libraries = {}
    servers_with_libraries = {}
    invite_servers = invite.servers if invite.servers else []
    
    # Fallback to legacy single server if no servers in many-to-many relationship
    invite_server = None
    if not invite_servers and invite.server_id:
        invite_server = media_service_manager.get_server_by_id(invite.server_id)
        if invite_server:
            invite_servers = [invite_server]
    
    # Collect libraries from all servers
    for server in invite_servers:
        try:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                server_libraries = service.get_libraries()
                server_lib_dict = {lib['id']: lib['name'] for lib in server_libraries}
                servers_with_libraries[server.id] = {
                    'server': server,
                    'libraries': server_lib_dict
                }
                # Add to combined libraries (prefix with server name if there are conflicts)
                for lib_id, lib_name in server_lib_dict.items():
                    if lib_id in available_libraries:
                        # Handle ID conflicts by prefixing with server name
                        available_libraries[f"{server.id}_{lib_id}"] = f"[{server.name}] {lib_name}"
                    else:
                        available_libraries[lib_id] = lib_name
        except Exception as e:
            current_app.logger.error(f"Could not fetch libraries for server {server.name}: {e}")
    
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # If grant_library_ids is an empty list, it signifies access to ALL libraries.
    # In this case, we pre-select all the available library checkboxes in the form.
    if invite.grant_library_ids == []:
        form.libraries.data = list(available_libraries.keys())
    else:
        form.libraries.data = list(invite.grant_library_ids or [])

    # Discord settings
    bot_is_enabled = Setting.get_bool('DISCORD_BOT_ENABLED', False)
    global_force_sso = Setting.get_bool('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False) or bot_is_enabled
    global_require_guild = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)
    
    form.require_discord_auth.data = invite.require_discord_auth
    form.require_discord_guild_membership.data = invite.require_discord_guild_membership

    return render_template(
        'invites/partials/edit_invite_modal.html',
        form=form,
        invite=invite,
        grouped_servers=grouped_servers,
        invite_server=invite_server,
        servers_with_libraries=servers_with_libraries,
        global_require_guild=global_require_guild
    )


# --- NEW: Edit Invite POST Route (for saving changes) ---
@bp.route('/api/server/<int:server_id>/libraries', methods=['GET'])
@login_required
@setup_required
def get_server_libraries(server_id):
    """Get libraries for a specific server"""
    try:
        media_service_manager = MediaServiceManager()
        server = media_service_manager.get_server_by_id(server_id)
        
        if not server:
            return {'error': 'Server not found'}, 404
            
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {'error': 'Could not create service for server'}, 500
            
        libraries = service.get_libraries()
        return {
            'success': True,
            'libraries': [{'id': lib['id'], 'name': lib['name']} for lib in libraries]
        }
        
    except Exception as e:
        current_app.logger.error(f"Error fetching libraries for server {server_id}: {e}")
        return {'error': str(e)}, 500

@bp.route('/manage/edit/<int:invite_id>', methods=['POST'])
@login_required
@setup_required
@permission_required('edit_invites')
def update_invite(invite_id):
    invite = Invite.query.get_or_404(invite_id)
    form = InviteEditForm()
    
    media_service_manager = MediaServiceManager()
    
    # Correctly fetch libraries for the specific server associated with the invite
    available_libraries = {}
    invite_server = None
    if invite.server_id:
        invite_server = media_service_manager.get_server_by_id(invite.server_id)
        if invite_server:
            service = MediaServiceFactory.create_service_from_db(invite_server)
            try:
                available_libraries = {lib['id']: lib['name'] for lib in service.get_libraries()}
            except Exception as e:
                current_app.logger.error(f"Could not fetch libraries for server {invite_server.name}: {e}")
    
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]

    # Global Discord settings for comparison
    bot_is_enabled = Setting.get_bool('DISCORD_BOT_ENABLED', False)
    global_force_sso = Setting.get_bool('DISCORD_BOT_REQUIRE_SSO_ON_INVITE', False) or bot_is_enabled
    global_require_guild = Setting.get_bool('DISCORD_REQUIRE_GUILD_MEMBERSHIP', False)
    
    if form.validate_on_submit():
        # Expiration
        if form.clear_expiry.data:
            invite.expires_at = None
        elif form.expires_in_days.data is not None and form.expires_in_days.data > 0:
            invite.expires_at = calculate_expiry_date(form.expires_in_days.data)
        # Note: If days is 0, we let it expire naturally if it's already past. No change.

        # Max Uses
        if form.clear_max_uses.data:
            invite.max_uses = None
        elif form.number_of_uses.data is not None:
             # Allow setting to 0, which means unlimited (NULL in DB)
            invite.max_uses = form.number_of_uses.data if form.number_of_uses.data > 0 else None

        # Membership Duration
        if form.clear_membership_duration.data:
            invite.membership_duration_days = None
        elif form.membership_duration_days.data is not None and form.membership_duration_days > 0:
            invite.membership_duration_days = form.membership_duration_days.data

        # Library Access Logic
        # If the number of selected libraries equals the total number of available libraries,
        # store an empty list to signify "all libraries". Otherwise, store the selected list.
        if len(form.libraries.data) == len(available_libraries):
            invite.grant_library_ids = []
        else:
            invite.grant_library_ids = form.libraries.data
        
        # Other boolean fields
        invite.allow_downloads = form.allow_downloads.data
        invite.invite_to_plex_home = form.invite_to_plex_home.data
        invite.allow_live_tv = form.allow_live_tv.data
        invite.grant_purge_whitelist = form.grant_purge_whitelist.data
        invite.grant_bot_whitelist = form.grant_bot_whitelist.data

        invite.require_discord_auth = form.require_discord_auth.data
        invite.require_discord_guild_membership = form.require_discord_guild_membership.data

        db.session.commit()
        log_event(EventType.SETTING_CHANGE, f"Invite '{invite.custom_path or invite.token}' updated.", invite_id=invite.id, admin_id=current_user.id)
        
        response = make_response("", 204)
        trigger_payload = {"refreshInvitesList": True, "showToastEvent": {"message": "Invite updated successfully!", "category": "success"}}
        response.headers['HX-Trigger-After-Swap'] = json.dumps(trigger_payload)
        return response
    
    # If validation fails, re-render the form partial with errors
    # We need to reconstruct the context for the template
    all_servers = media_service_manager.get_all_servers(active_only=True)
    grouped_servers = {}
    for server in all_servers:
        service_type_name = server.service_type.name.capitalize()
        if service_type_name not in grouped_servers:
            grouped_servers[service_type_name] = []
        grouped_servers[service_type_name].append(server)

    return render_template(
        'invites/partials/edit_invite_modal.html',
        form=form,
        invite=invite,
        grouped_servers=grouped_servers,
        invite_server=invite_server,
        global_require_guild=global_require_guild
    ), 422