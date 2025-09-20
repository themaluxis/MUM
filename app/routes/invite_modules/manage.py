"""
Invite management functionality - Admin list, create, toggle status, view usages
"""

import time
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, current_app, g, make_response
from flask_login import login_required, current_user
from app.models import Invite, Setting, EventType, UserAppAccess
from app.models_media_services import MediaServer
from app.forms import InviteCreateForm
from app.extensions import db
from app.utils.helpers import setup_required, permission_required, log_event
from app.services.media_service_manager import MediaServiceManager
from . import invites_bp
import json

@invites_bp.route('/invites') 
@login_required
@setup_required
@permission_required('manage_invites')
def list_invites():
    # Redirect local users away from admin pages
    if isinstance(current_user, UserAppAccess) and not current_user.has_permission('manage_invites'):
        flash('You do not have permission to access the invites management page.', 'danger')
        return redirect(url_for('user.index'))
    
    start_time = time.time()
    
    page = request.args.get('page', 1, type=int)
    # Get view mode, defaulting to 'cards'
    view_mode = request.args.get('view', Setting.get('DEFAULT_INVITE_VIEW', 'cards'))

    items_per_page_setting = Setting.get('DEFAULT_INVITES_PER_PAGE', current_app.config.get('DEFAULT_INVITES_PER_PAGE', 10))
    items_per_page = int(items_per_page_setting) if items_per_page_setting else 10
    
    # Query logic is unchanged
    query = Invite.query
    filter_status = request.args.get('filter', 'all')
    search_path = request.args.get('search_path', '').strip()
    if search_path: 
        query = query.filter(Invite.custom_path.ilike(f"%{search_path}%"))
    now = datetime.utcnow() 
    if filter_status == 'active': 
        query = query.filter(Invite.is_active == True, (Invite.expires_at == None) | (Invite.expires_at > now), (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses))
    elif filter_status == 'expired': 
        query = query.filter(Invite.expires_at != None, Invite.expires_at <= now)
    elif filter_status == 'maxed': 
        query = query.filter(Invite.max_uses != None, Invite.current_uses >= Invite.max_uses)
    elif filter_status == 'inactive': 
        query = query.filter(Invite.is_active == False)
    
    invites_pagination = query.order_by(Invite.created_at.desc()).paginate(page=page, per_page=items_per_page, error_out=False)
    invites_count = query.count()
    
    # Create modal form logic
    form = InviteCreateForm()
    media_service_manager = MediaServiceManager()
    
    # Fetch all active servers
    all_servers = media_service_manager.get_all_servers(active_only=True)

    # For now, populate libraries from the first available server for the form
    # Don't pre-load libraries - they will be loaded dynamically based on server selection
    available_libraries = {}
    form.libraries.choices = []
    
    # Build comprehensive library data for invite cards display
    # This will help map library IDs to names and service types in the template
    libraries_by_server = {}
    all_libraries_lookup = {}
    
    for server in all_servers:
        try:
            from app.models_media_services import MediaLibrary
            # Load libraries from database for each server
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            server_libraries = {}
            
            for lib in db_libraries:
                lib_data = {
                    'id': lib.id,
                    'external_id': lib.external_id,
                    'name': lib.name,
                    'server_id': server.id,
                    'server_name': server.server_nickname,
                    'service_type': server.service_type.value
                }
                server_libraries[lib.external_id] = lib_data
                
                # Store in global lookup - use prefixed format for Kavita to match invite creation
                if server.service_type.name.upper() == 'KAVITA':
                    prefixed_id = f"[{server.service_type.name.upper()}]-{server.server_nickname}-{lib.external_id}"
                    all_libraries_lookup[prefixed_id] = lib_data
                else:
                    # For other services (including AudioBookshelf), use raw external_id
                    all_libraries_lookup[lib.external_id] = lib_data
                    
                    # For AudioBookshelf, also add a prefixed version for backward compatibility
                    if server.service_type.name.upper() == 'AUDIOBOOKSHELF':
                        prefixed_id = f"[{server.service_type.name.upper()}]-{server.server_nickname}-{lib.external_id}"
                        all_libraries_lookup[prefixed_id] = lib_data
            
            libraries_by_server[server.id] = server_libraries
            current_app.logger.debug(f"Loaded {len(server_libraries)} libraries for server {server.server_nickname}")
            
        except Exception as e:
            current_app.logger.error(f"Failed to load libraries for server {server.server_nickname}: {e}")
            libraries_by_server[server.id] = {}
    
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
                               libraries_by_server=libraries_by_server,
                               all_libraries_lookup=all_libraries_lookup,
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
    result = render_template('invites/index.html', 
                           title="Manage Invites", 
                           invites_count=invites_count, 
                           form=form, 
                           all_servers=all_servers,
                           grouped_servers=grouped_servers,
                           available_libraries=available_libraries,
                           libraries_by_server=libraries_by_server,
                           all_libraries_lookup=all_libraries_lookup,
                           current_per_page=items_per_page,
                           discord_oauth_enabled=discord_oauth_enabled,
                           global_force_sso=global_force_sso,
                           enable_discord_membership_requirement=enable_discord_membership_requirement,
                           current_view=view_mode)
    
    # Log performance for slow requests only
    total_time = time.time() - start_time
    if total_time > 1.0:  # Only log if over 1 second
        current_app.logger.warning(f"Slow invites page load: {total_time:.3f}s")
    
    return result

@invites_bp.route('/invites/create', methods=['POST'])
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
    
    # For the form, we'll use libraries from the database for fast loading
    # The frontend will handle per-server library selection via AJAX
    available_libraries = {}
    if selected_server_ids:
        first_server = media_service_manager.get_server_by_id(selected_server_ids[0])
        if first_server:
            try:
                from app.models_media_services import MediaLibrary
                # Load libraries from database (much faster than API calls)
                db_libraries = MediaLibrary.query.filter_by(server_id=first_server.id).all()
                available_libraries = {lib.external_id: lib.name for lib in db_libraries}
                current_app.logger.info(f"Loaded {len(available_libraries)} libraries from database for server {first_server.server_nickname}")
            except Exception as e:
                current_app.logger.error(f"Failed to fetch libraries from database for server {first_server.server_nickname}: {e}")
                available_libraries = {}
    
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
            # Single server - only use prefixed format for Kavita
            first_server = media_service_manager.get_server_by_id(selected_server_ids[0])
            if first_server:
                service_type = first_server.service_type.name.upper()
                if service_type == 'KAVITA':
                    # Convert to prefixed format for Kavita (to avoid ID conflicts)
                    unique_choices = []
                    for lib_id, lib_name in available_libraries.items():
                        unique_lib_id = f"[{service_type}]-{first_server.server_nickname}-{lib_id}"
                        unique_choices.append((unique_lib_id, lib_name))
                    form.libraries.choices = unique_choices
                # For UUID-based services, keep the raw external_id format (no change needed)
        else:
            # Multi-server - use conflict handling logic
            all_valid_choices = []
            servers_libraries = {}  # server_id -> {lib_id: lib_name}
            
            # First pass: collect all libraries from all servers (using database)
            for server_id in selected_server_ids:
                server = media_service_manager.get_server_by_id(server_id)
                if server:
                    try:
                        from app.models_media_services import MediaLibrary
                        # Load libraries from database (much faster than API calls)
                        db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
                        server_lib_dict = {lib.external_id: lib.name for lib in db_libraries}
                        
                        if not server_lib_dict:
                            current_app.logger.warning(f"Server {server.server_nickname} has no libraries in database - may need sync")
                            flash(f"Info: Server '{server.server_nickname}' has no libraries in database. Use the refresh button to sync from server.", "info")
                        
                        servers_libraries[server.id] = {
                            'server': server,
                            'libraries': server_lib_dict
                        }
                        current_app.logger.info(f"Loaded {len(server_lib_dict)} libraries from database for server {server.server_nickname}")
                    except Exception as e:
                        current_app.logger.error(f"Failed to fetch libraries from database for server {server.server_nickname}: {e}")
                        flash(f"Error: Could not load libraries for server '{server.server_nickname}' from database.", "error")
                        # Still add the server with empty libraries to avoid undefined errors
                        servers_libraries[server.id] = {
                            'server': server,
                            'libraries': {}
                        }
            
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
                    
                    service_type = server.service_type.name.upper()
                    
                    if conflicts_with_other_servers or service_type == 'KAVITA':
                        # Use prefixed format for Kavita (always) or when there are ID conflicts
                        unique_lib_id = f"[{service_type}]-{server.server_nickname}-{lib_id}"
                        all_valid_choices.append((unique_lib_id, f"[{server.server_nickname}] {lib_name}"))
                    else:
                        # Use raw UUID for non-conflicting UUID-based services
                        all_valid_choices.append((lib_id, f"[{server.server_nickname}] {lib_name}"))
            
            # Update form choices for multi-server
            form.libraries.choices = all_valid_choices
            
            # The frontend now submits libraries in unique format, so we can use them directly
            if submitted_libraries:
                # Remove duplicates from submitted libraries first
                unique_submitted = list(dict.fromkeys(submitted_libraries))  # Preserves order, removes duplicates
                
                # Validate that submitted libraries exist in our available choices
                valid_choices = [choice[0] for choice in all_valid_choices]
                validated_libraries = []
                
                for submitted_lib_id in unique_submitted:
                    # Skip undefined or malformed library IDs
                    if not submitted_lib_id or 'undefined' in submitted_lib_id:
                        current_app.logger.warning(f"Skipping malformed library ID: {submitted_lib_id}")
                        continue
                    
                    if submitted_lib_id in valid_choices:
                        validated_libraries.append(submitted_lib_id)
                    else:
                        # Check if this is a prefixed library ID format (used for Kavita)
                        # Format: [SERVICE_TYPE]-ServerName-LibraryID
                        if submitted_lib_id.startswith('[') and ']-' in submitted_lib_id and submitted_lib_id.count('-') >= 2:
                            # Extract the actual library ID from the prefixed format
                            try:
                                # Split: [SERVICE_TYPE]-ServerName-LibraryID
                                service_part, remainder = submitted_lib_id.split(']-', 1)
                                service_type = service_part[1:]  # Remove the opening [
                                server_name, library_id = remainder.split('-', 1)
                                
                                # Check if this server is in our selected servers
                                matching_server = None
                                for server_id in selected_server_ids:
                                    server = media_service_manager.get_server_by_id(server_id)
                                    if server and server.server_nickname == server_name:
                                        matching_server = server
                                        break
                                
                                if matching_server:
                                    # Server exists - store the actual library ID without prefix
                                    validated_libraries.append(library_id)
                                    current_app.logger.info(f"Extracted library ID from prefixed format: {submitted_lib_id} -> {library_id}")
                                else:
                                    current_app.logger.warning(f"Invalid library ID submitted (server not found): {submitted_lib_id}")
                            except Exception as e:
                                current_app.logger.warning(f"Error parsing prefixed library ID {submitted_lib_id}: {e}")
                        else:
                            # Raw UUID format (used for UUID-based services like Plex, Jellyfin, etc.)
                            # Validate against database instead of live API to avoid server connectivity issues
                            from app.models_media_services import MediaLibrary
                            
                            library_found = False
                            for server_id in selected_server_ids:
                                # Check if library exists in database for this server
                                db_library = MediaLibrary.query.filter_by(
                                    server_id=server_id,
                                    external_id=submitted_lib_id
                                ).first()
                                
                                if db_library:
                                    validated_libraries.append(submitted_lib_id)
                                    current_app.logger.info(f"Validated raw library ID: {submitted_lib_id} from server {db_library.server.server_nickname} (database)")
                                    library_found = True
                                    break
                            
                            if not library_found:
                                current_app.logger.warning(f"Invalid library ID submitted (not found in database for selected servers): {submitted_lib_id}")
                
                form.libraries.data = validated_libraries
        
        # Set the form data for single server case
        if len(selected_server_ids) == 1 and submitted_libraries:
            form.libraries.data = submitted_libraries

    toast_message_text = ""
    toast_category = "info"

    # Set up library choices BEFORE form validation for multi-server invites
    if selected_server_ids and len(selected_server_ids) > 1:
        # Multi-server invite - need to set up unique library choices
        from app.models_media_services import MediaLibrary
        all_valid_choices = []
        
        for server_id in selected_server_ids:
            server = MediaServer.query.get(server_id)
            if not server:
                continue
                
            # Get libraries for this server from database
            db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
            for lib in db_libraries:
                if server.service_type.name.upper() == 'KAVITA':
                    # Use prefixed format for Kavita to avoid ID conflicts
                    unique_lib_id = f"[{server.service_type.name.upper()}]-{server.server_nickname}-{lib.external_id}"
                    all_valid_choices.append((unique_lib_id, f"[{server.server_nickname}] {lib.name}"))
                else:
                    # Use raw UUID for non-conflicting UUID-based services
                    all_valid_choices.append((lib.external_id, f"[{server.server_nickname}] {lib.name}"))
        
        # Update form choices for multi-server
        form.libraries.choices = all_valid_choices
    elif selected_server_ids and len(selected_server_ids) == 1:
        # Single server - use simple library choices
        from app.models_media_services import MediaLibrary
        server_id = selected_server_ids[0]
        db_libraries = MediaLibrary.query.filter_by(server_id=server_id).all()
        form.libraries.choices = [(lib.external_id, lib.name) for lib in db_libraries]
    else:
        # No servers selected - empty choices
        form.libraries.choices = []

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
        from datetime import date
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
            membership_duration_days=membership_duration, created_by_owner_id=current_user.id,
            require_discord_auth=form.require_discord_auth.data,
            require_discord_guild_membership=form.require_discord_guild_membership.data
            # Removed server_id assignment - now using many-to-many servers relationship
        )
        try:
            db.session.add(new_invite)
            db.session.flush()  # Flush to get the invite ID
            
            # Clear any automatically added servers first
            new_invite.servers.clear()
            
            # Add all selected servers to the invite
            if selected_server_ids:
                for server_id in selected_server_ids:
                    server = media_service_manager.get_server_by_id(server_id)
                    if server and server not in new_invite.servers:
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

@invites_bp.route('/invites/toggle-status/<int:invite_id>', methods=['POST'])
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
        
        # Return the updated invite card row to match HTMX target
        from datetime import datetime
        from app.services.media_service_manager import MediaServiceManager
        
        now = datetime.now()
        media_service_manager = MediaServiceManager()
        all_servers = media_service_manager.get_all_servers(active_only=True)
        
        # Build library lookup for template
        all_libraries_lookup = {}
        for server in all_servers:
            try:
                from app.models_media_services import MediaLibrary
                db_libraries = MediaLibrary.query.filter_by(server_id=server.id).all()
                for lib in db_libraries:
                    lib_data = {
                        'id': lib.id,
                        'external_id': lib.external_id,
                        'name': lib.name,
                        'server_id': server.id,
                        'server_name': server.server_nickname,
                        'service_type': server.service_type.value
                    }
                    if server.service_type.name.upper() == 'KAVITA':
                        prefixed_id = f"[{server.service_type.name.upper()}]-{server.server_nickname}-{lib.external_id}"
                        all_libraries_lookup[prefixed_id] = lib_data
                    else:
                        all_libraries_lookup[lib.external_id] = lib_data
                        if server.service_type.name.upper() == 'AUDIOBOOKSHELF':
                            prefixed_id = f"[{server.service_type.name.upper()}]-{server.server_nickname}-{lib.external_id}"
                            all_libraries_lookup[prefixed_id] = lib_data
            except Exception as e:
                current_app.logger.error(f"Failed to load libraries for server {server.server_nickname}: {e}")
        
        # Return just the toggle buttons for HTMX replacement
        response = make_response(render_template('invites/partials/status_badge.html', invite=invite))
        
        # Trigger multiple updates: status badge and bulk actions
        response.headers['HX-Trigger'] = json.dumps({
            'refreshBulkActions': True,
            'updateStatusBadge': {'inviteId': invite.id, 'isActive': invite.is_active}
        })
        
        return response
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling invite status {invite_id}: {e}")
        return f'<div class="alert alert-error"><span>Error updating invite status: {e}</span></div>', 500

@invites_bp.route('/invites/delete/<int:invite_id>', methods=['DELETE'])
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
        }
        headers['HX-Trigger'] = json.dumps(trigger_payload)
        
        return make_response("", 200, headers) # Still 200, toast will show error

@invites_bp.route('/invites/usages/<int:invite_id>', methods=['GET'])
@login_required
@setup_required
def view_invite_usages(invite_id):
    from app.models import InviteUsage
    invite = Invite.query.get_or_404(invite_id)
    usages = InviteUsage.query.filter_by(invite_id=invite.id).order_by(InviteUsage.used_at.desc()).all()
    return render_template('invites/partials/usage_modal.html', invite=invite, usages=usages)