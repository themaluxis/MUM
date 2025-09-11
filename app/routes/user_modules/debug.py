# File: app/routes/user_modules/debug.py
"""Debug and quick edit functionality for users"""

from flask import render_template, request, current_app
from flask_login import login_required, current_user
from app.models import UserAppAccess
from app.models_media_services import UserMediaAccess
from app.forms import UserEditForm
from app.utils.helpers import permission_required
from . import users_bp


@users_bp.route('/debug_info/<uuid:user_uuid>')
@login_required
def get_user_debug_info(user_uuid):
    """Get raw user data for debugging purposes - ONLY uses stored data, NO API calls"""
    # Get user by uuid
    from app.utils.helpers import get_user_by_uuid
    user_obj, user_type = get_user_by_uuid(str(user_uuid))
    
    if not user_obj:
        current_app.logger.error(f"User not found with uuid: {user_uuid}")
        return f"<p class='text-error'>User not found: {user_uuid}</p>"
    
    actual_id = user_obj.id
    
    if user_type == "user_app_access":
        # This is a local UserAppAccess user
        user = UserAppAccess.query.get(actual_id)
    
        if not user:
            return f"<p class='text-error'>Local user with ID {actual_id} not found</p>"
        
        # This is a local UserAppAccess user - user variable already set above
        pass
    
    elif user_type == "user_media_access":
        # This is a standalone service user, get the UserMediaAccess record
        access = UserMediaAccess.query.filter(
            UserMediaAccess.id == actual_id
        ).first()
        
        if not access:
            return f"<p class='text-error'>Service user with ID {actual_id} not found</p>"
        
        # Create a mock user object for the template
        class MockUser:
            def __init__(self, access, user_id):
                self.id = user_id  # Keep the prefixed ID for display
                self.username = access.external_username or 'Unknown'
                self.email = access.external_email
                self.notes = access.notes
                self.created_at = access.created_at
                self.last_login_at = access.last_activity_at
                self.media_accesses = [access]
                self.access_expires_at = access.access_expires_at
                self.discord_user_id = access.discord_user_id
                self.is_active = access.is_active
                self._is_standalone = True
                self._access_record = access
            
            def get_display_name(self):
                return self._access_record.external_username or 'Unknown'
            
            def get_avatar(self, default_url=None):
                """Return avatar URL for MockUser - service users typically don't have avatars"""
                return default_url
        
        user = MockUser(access, user_uuid)
    
    try:
        # Enhanced debugging for raw service data
        current_app.logger.info(f"=== DEBUG INFO REQUEST FOR USER {user_uuid} ===")
        current_app.logger.info(f"Username: {user.get_display_name()}")
        
        # Check for service-specific data in UserMediaAccess records
        if hasattr(user, '_is_standalone') and user._is_standalone:
            # For standalone users, the access record is stored in _access_record
            user_accesses = [user._access_record]
        else:
            # For regular users, query by user_app_access_id using actual_id
            user_accesses = UserMediaAccess.query.filter_by(user_app_access_id=actual_id).all()
        
        has_service_data = False
        for access in user_accesses:
            if access.service_settings:
                has_service_data = True
                current_app.logger.info(f"Service data found for {access.server.service_type.value} server: {access.server.server_nickname}")
                current_app.logger.info(f"Service settings type: {type(access.service_settings)}")
                current_app.logger.info(f"Service settings preview: {str(access.service_settings)[:100]}...")
        
        if not has_service_data:
            current_app.logger.warning(f"No stored service data for user {user.get_display_name()} - user needs to sync")
            
        # Check which services this user belongs to
        if hasattr(user, '_is_standalone') and user._is_standalone:
            # For standalone users, use the access record we already have
            user_access = [user._access_record]
        else:
            # For regular users, query by user_app_access_id
            user_access = UserMediaAccess.query.filter_by(user_app_access_id=user.id).all()
        current_app.logger.info(f"User has access to {len(user_access)} servers:")
        for access in user_access:
            current_app.logger.info(f"  - Server: {access.server.server_nickname} (Type: {access.server.service_type.value})")
        
        # Render the template with the user data
        return render_template('users/partials/user_debug_info_modal.html', user=user)
        
    except Exception as e:
        current_app.logger.error(f"Error getting debug info for user {user_uuid}: {e}", exc_info=True)
        return f"<p class='text-error'>Error fetching user data: {str(e)}</p>"


@users_bp.route('/quick_edit_form/<uuid:user_uuid>')
@login_required
@permission_required('edit_user')
def get_quick_edit_form(user_uuid):
    """Get quick edit form for a user"""
    # Get user by uuid
    from app.utils.helpers import get_user_by_uuid
    user_obj, user_type = get_user_by_uuid(str(user_uuid))
    
    if not user_obj:
        current_app.logger.error(f"User not found with uuid: {user_uuid}")
        return '<div class="alert alert-error">User not found.</div>'
    
    actual_id = user_obj.id
    
    if user_type == "user_app_access":
        # Local user - get UserAppAccess record
        user = UserAppAccess.query.get_or_404(actual_id)
    elif user_type == "user_media_access":
        # Service user - get UserMediaAccess record and create a compatible object
        access = UserMediaAccess.query.get_or_404(actual_id)
        
        # Create a mock user object that's compatible with the form
        class MockUser:
            def __init__(self, access):
                self.id = actual_id  # Use actual ID for form processing
                self.username = access.external_username or 'Unknown'
                self.email = access.external_email
                self.notes = access.notes
                self.created_at = access.created_at
                self.last_login_at = access.last_activity_at
                self.access_expires_at = access.access_expires_at
                self.discord_user_id = access.discord_user_id
                self.is_discord_bot_whitelisted = False  # Service users don't have this
                self.is_purge_whitelisted = False  # Service users don't have this
                self._access_record = access
                self._is_service_user = True
            
            def get_display_name(self):
                return self.username or 'Unknown'
        
        user = MockUser(access)
    else:
        return '<div class="alert alert-error">Unknown user type.</div>'
    
    form = UserEditForm(obj=user)  # Pre-populate form with existing data

    # Populate dynamic choices - only show libraries from servers this user has access to
    if user_type == "user_app_access":
        # Local user - get all their UserMediaAccess records
        user_access_records = UserMediaAccess.query.filter_by(user_app_access_id=actual_id).all()
    elif user_type == "user_media_access":
        # Service user - get their specific UserMediaAccess record
        user_access_records = [user._access_record]
    
    available_libraries = {}
    current_library_ids = []
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Building available libraries for user {user.id}")
    
    for access in user_access_records:
        try:
            # Get libraries from database instead of making API calls
            from app.models_media_services import MediaLibrary
            db_libraries = MediaLibrary.query.filter_by(server_id=access.server.id).all()
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Processing server {access.server.server_nickname} (type: {access.server.service_type.value})")
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: User access record allowed_library_ids: {access.allowed_library_ids}")
            current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Server libraries from DB: {[{lib.external_id: lib.name} for lib in db_libraries]}")
            
            for lib in db_libraries:
                lib_id = lib.external_id
                lib_name = lib.name
                if lib_id:
                    # For Kavita, create compound IDs to match the format used in user access records
                    if access.server.service_type.value == 'kavita':
                        compound_lib_id = f"{lib_id}_{lib_name}"
                        available_libraries[compound_lib_id] = lib_name
                        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Added Kavita library: {compound_lib_id} -> {lib_name}")
                    else:
                        available_libraries[str(lib_id)] = lib_name
                        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Added non-Kavita library: {lib_id} -> {lib_name}")
            
            # Collect current library IDs from this server
            current_library_ids.extend(access.allowed_library_ids or [])
        except Exception as e:
            current_app.logger.error(f"Error getting libraries from {access.server.server_nickname}: {e}")
    
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Final available_libraries: {available_libraries}")
    form.libraries.choices = [(lib_id, name) for lib_id, name in available_libraries.items()]
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Form choices set to: {form.libraries.choices}")
    
    # Pre-populate the fields with the user's current settings from all their servers
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Current library IDs from access records: {current_library_ids}")
    current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Available library keys: {list(available_libraries.keys())}")
    
    # Handle special case for Jellyfin users with '*' (all libraries access)
    if current_library_ids == ['*']:
        # If user has "All Libraries" access, check all available library checkboxes
        form.libraries.data = list(available_libraries.keys())
        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: User has '*' access, setting all libraries: {form.libraries.data}")
    else:
        # Set the current library selections
        form.libraries.data = [str(lib_id) for lib_id in current_library_ids if str(lib_id) in available_libraries]
        current_app.logger.info(f"DEBUG KAVITA QUICK EDIT: Set form.libraries.data to: {form.libraries.data}")
    
    # Get allow_downloads and allow_4k_transcode from UserMediaAccess records
    # Use the first access record's values, or default to False if no access records
    if user_access_records:
        first_access = user_access_records[0]
        form.allow_downloads.data = first_access.allow_downloads
        form.allow_4k_transcode.data = first_access.allow_4k_transcode
    else:
        form.allow_downloads.data = False
        form.allow_4k_transcode.data = True  # Default to True for 4K transcode
    
    # Set discord and purge whitelist flags (service users don't have these)
    if hasattr(user, 'is_discord_bot_whitelisted'):
        form.is_discord_bot_whitelisted.data = user.is_discord_bot_whitelisted
    else:
        form.is_discord_bot_whitelisted.data = False
        
    if hasattr(user, 'is_purge_whitelisted'):
        form.is_purge_whitelisted.data = user.is_purge_whitelisted
    else:
        form.is_purge_whitelisted.data = False
    
    # Updated logic for DateField - the form will automatically populate access_expires_at 
    # from the user object via obj=user, but we can explicitly set it if needed
    if user.access_expires_at:
        # Convert datetime to date for the DateField
        form.access_expires_at.data = user.access_expires_at.date()
    
    # Build user_server_names for the template
    user_server_names = {}
    user_server_names[actual_id] = []
    for access in user_access_records:
        if access.server.server_nickname not in user_server_names[actual_id]:
            user_server_names[actual_id].append(access.server.server_nickname)
    
    # We pass the _settings_tab partial, which contains the form we need.
    return render_template(
        'user/partials/settings_tab.html',
        form=form,
        user=user,
        user_server_names=user_server_names
    )