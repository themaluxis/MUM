# File: app/services/user_service.py
from flask import current_app
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case, or_
from app.models import UserAppAccess, EventType
from app.models_media_services import ServiceType, MediaServer, UserMediaAccess, MediaStreamHistory
from app.extensions import db
from app.utils.helpers import log_event, format_duration
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory

def parse_user_id(user_id):
    """Parse prefixed user ID and return (user_type, actual_id)"""
    if ":" not in str(user_id):
        raise ValueError(f"Invalid user ID format: {user_id}. Must be 'type:id'")
    
    user_type, actual_id = str(user_id).split(":", 1)
    if user_type not in ["user_app_access", "user_media_access"]:
        raise ValueError(f"Invalid user type: {user_type}")
    
    return user_type, int(actual_id)

def sync_users_from_plex():
    current_app.logger.info("User_Service.py - sync_users_from_plex(): Starting Plex user synchronization.")
    
    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        current_app.logger.error("User_Service.py - sync_users_from_plex(): Plex server not configured.")
        return {'added': [], 'updated': [], 'removed': [], 'errors': 1, 'error_messages': ["Plex server not configured."]}
    
    server = plex_servers[0]
    plex_service = MediaServiceFactory.create_service_from_db(server)
    if not plex_service:
        current_app.logger.error("User_Service.py - sync_users_from_plex(): Plex service not found.")
        return {'added': [], 'updated': [], 'removed': [], 'errors': 1, 'error_messages': ["Plex service not found."]}

    raw_plex_users_with_access = plex_service.get_users()

    if raw_plex_users_with_access is None:
        current_app.logger.error("User_Service.py - sync_users_from_plex(): Plex user sync failed: Could not retrieve users from Plex service (returned None).")
        # Return a structure indicating failure for the route to handle
        return {'added': [], 'updated': [], 'removed': [], 'errors': 1, 'error_messages': ["Failed to retrieve users from Plex service."]}

    # Get all UserAppAccess records and their media accesses
    mum_users_all = UserAppAccess.query.all()
    
    # Get Plex user mappings from UserMediaAccess instead of ServiceAccount
    from app.models_media_services import UserMediaAccess, ServiceType
    plex_server = MediaServer.query.filter_by(service_type=ServiceType.PLEX).first()
    if plex_server:
        plex_accesses = UserMediaAccess.query.filter_by(server_id=plex_server.id).all()
        mum_users_map_by_plex_id = {access.external_user_id: access.service_account for access in plex_accesses if access.external_user_id}
        mum_users_map_by_plex_uuid = {access.external_user_alt_id: access.service_account for access in plex_accesses if access.external_user_alt_id}
    else:
        mum_users_map_by_plex_id = {}
        mum_users_map_by_plex_uuid = {}
    
    # --- Initialize lists to store details of changes ---
    added_users_details = []
    updated_users_details = [] # List of dicts: {'username': str, 'changes': [str_description_of_change]}
    removed_users_details = [] # List of dicts: {'username': str, 'plex_id': int/str}
    error_count = 0
    error_messages = []
    # --- End init lists ---

    current_plex_user_ids_on_server = set()
    for plex_user_data_item in raw_plex_users_with_access:
        if plex_user_data_item.get('id') is not None:
            current_plex_user_ids_on_server.add(plex_user_data_item['id'])

    for plex_user_data in raw_plex_users_with_access:
        plex_id = plex_user_data.get('id')
        plex_uuid_from_sync = plex_user_data.get('uuid')
        plex_username_from_sync = plex_user_data.get('username')

        if plex_id is None and plex_uuid_from_sync is None:
            msg = f"Plex user data missing 'id' and 'uuid' for potential user: {plex_username_from_sync or 'Unknown'}. Skipping."
            current_app.logger.warning(msg)
            error_count += 1; error_messages.append(msg)
            continue

        mum_user = None
        if plex_id is not None: mum_user = mum_users_map_by_plex_id.get(str(plex_id))  # Convert to string for consistency
        if not mum_user and plex_uuid_from_sync: mum_user = mum_users_map_by_plex_uuid.get(plex_uuid_from_sync)
        
        new_library_ids_from_plex_list = list(plex_user_data.get('allowed_library_ids_on_server', []))
        plex_email_from_sync = plex_user_data.get('email')
        plex_thumb_from_sync = plex_user_data.get('thumb')
        is_home_user_from_sync = plex_user_data.get('is_home_user', False)
        shares_back_from_sync = plex_user_data.get('shares_back', False)
        is_friend_from_sync = plex_user_data.get('is_friend', False)

        if mum_user: # Existing user
            changes_for_this_user = []
            original_username = mum_user.get_display_name() # For logging if username itself changes

            # Note: ServiceAccount model doesn't have plex_user_id or plex_uuid fields
            # if plex_id is not None and mum_user.plex_user_id != plex_id:
            #     changes_for_this_user.append(f"Plex User ID corrected from {mum_user.plex_user_id} to {plex_id}")
            #     mum_user.plex_user_id = plex_id
            # if plex_uuid_from_sync and mum_user.plex_uuid != plex_uuid_from_sync:
            #     changes_for_this_user.append(f"Plex UUID updated from {mum_user.plex_uuid} to {plex_uuid_from_sync}")
            #     mum_user.plex_uuid = plex_uuid_from_sync
            if mum_user.username != plex_username_from_sync:
                changes_for_this_user.append(f"Username changed from '{mum_user.username}' to '{plex_username_from_sync}'")
                mum_user.username = plex_username_from_sync
            if mum_user.plex_email != plex_email_from_sync:
                changes_for_this_user.append(f"Email updated") # Don't log old/new email for privacy
                mum_user.plex_email = plex_email_from_sync
            if mum_user.plex_thumb_url != plex_thumb_from_sync:
                changes_for_this_user.append("Thumbnail updated")
                mum_user.plex_thumb_url = plex_thumb_from_sync
            
            current_mum_libs = mum_user.allowed_library_ids if mum_user.allowed_library_ids is not None else []
            if set(current_mum_libs) != set(new_library_ids_from_plex_list):
                changes_for_this_user.append(f"Libraries updated (Old: {len(current_mum_libs)}, New: {len(new_library_ids_from_plex_list)})")
                mum_user.allowed_library_ids = new_library_ids_from_plex_list
            if mum_user.is_home_user != is_home_user_from_sync:
                changes_for_this_user.append(f"Home User status changed to {is_home_user_from_sync}")
                mum_user.is_home_user = is_home_user_from_sync
            if mum_user.shares_back != shares_back_from_sync:
                changes_for_this_user.append(f"Shares Back status changed to {shares_back_from_sync}")
                mum_user.shares_back = shares_back_from_sync
            if hasattr(mum_user, 'is_plex_friend') and mum_user.is_plex_friend != is_friend_from_sync:
                changes_for_this_user.append(f"Plex Friend status changed to {is_friend_from_sync}")
                mum_user.is_plex_friend = is_friend_from_sync

            if changes_for_this_user:
                mum_user.last_synced_with_plex = datetime.utcnow()
                mum_user.updated_at = datetime.utcnow()
                updated_users_details.append({'username': plex_username_from_sync or original_username, 'changes': changes_for_this_user})
        else: # New user
            try:
                # Note: ServiceAccount model doesn't have plex_user_id or plex_uuid fields
                new_user_obj = User( # Renamed to avoid conflict with User model
                    # plex_user_id=plex_id, plex_uuid=plex_uuid_from_sync, 
                    username=plex_username_from_sync, plex_email=plex_email_from_sync,
                    plex_thumb_url=plex_thumb_from_sync, allowed_library_ids=new_library_ids_from_plex_list, 
                    is_home_user=is_home_user_from_sync, shares_back=shares_back_from_sync,
                    is_plex_friend=is_friend_from_sync, last_synced_with_plex=datetime.utcnow()
                )
                db.session.add(new_user_obj)
                added_users_details.append({'username': plex_username_from_sync, 'plex_id': plex_id})
            except IntegrityError as ie: 
                db.session.rollback()
                msg = f"Integrity error adding {plex_username_from_sync}: {ie}."
                current_app.logger.error(msg)
                error_count += 1; error_messages.append(msg)
            except Exception as e:
                db.session.rollback()
                msg = f"Error creating user {plex_username_from_sync}: {e}"
                current_app.logger.error(msg, exc_info=True)
                error_count += 1; error_messages.append(msg)

    # Check for users to remove based on UserMediaAccess data
    if plex_server:
        for access in plex_accesses:
            mum_user_obj = access.service_account
            is_on_server = False
            
            # Check if user is still on the Plex server
            if access.external_user_id and access.external_user_id in {str(uid) for uid in current_plex_user_ids_on_server}:
                is_on_server = True
            elif access.external_user_alt_id and access.external_user_alt_id in {str(uid) for uid in current_plex_user_ids_on_server}:
                is_on_server = True

            if not is_on_server:
                removed_users_details.append({
                    'username': mum_user_obj.get_display_name(), 
                    'mum_id': mum_user_obj.id, 
                    'plex_id': access.external_user_id
                })
                db.session.delete(mum_user_obj)
    
    if added_users_details or updated_users_details or removed_users_details or error_count > 0:
        try:
            db.session.commit()
            current_app.logger.info(f"DB commit successful for sync. Added: {len(added_users_details)}, Updated: {len(updated_users_details)}, Removed: {len(removed_users_details)}")
            # Log summary event
            log_event(EventType.PLEX_SYNC_USERS_COMPLETE, 
                      f"Plex user sync complete. Added: {len(added_users_details)}, Updated: {len(updated_users_details)}, Removed: {len(removed_users_details)}, Errors: {error_count}.",
                      details={
                          "added_count": len(added_users_details),
                          "updated_count": len(updated_users_details),
                          "removed_count": len(removed_users_details),
                          "errors": error_count
                      })
        except Exception as e_commit:
            db.session.rollback()
            msg = f"DB commit error during sync: {e_commit}"
            current_app.logger.error(msg, exc_info=True)
            error_count += (len(added_users_details) + len(updated_users_details) + len(removed_users_details)) # Count all attempts as errors if commit fails
            error_messages.append(msg)
            # Clear details lists as the changes were rolled back
            added_users_details = []
            updated_users_details = []
            removed_users_details = []
    
    return {
        'added': added_users_details, 
        'updated': updated_users_details, 
        'removed': removed_users_details, 
        'errors': error_count,
        'error_messages': error_messages
    }

def update_user_details(user_id, notes=None, new_library_ids=None,
                        is_discord_bot_whitelisted: bool = None,
                        is_purge_whitelisted: bool = None,
                        allow_downloads: bool = None,
                        allow_4k_transcode: bool = None,
                        admin_id: int = None):
    """
    Updates a user's details in the MUM database and syncs relevant changes to the Plex server.
    This function now correctly handles partial updates by sending the full final state to Plex.
    """
    # Parse prefixed user ID
    user_type, actual_id = parse_user_id(user_id)
    
    if user_type == "user_app_access":
        user = UserAppAccess.query.get_or_404(actual_id)
    else:
        # For user_media_access, get the associated UserAppAccess if it exists
        media_access = UserMediaAccess.query.get_or_404(actual_id)
        if media_access.user_app_access_id:
            user = UserAppAccess.query.get_or_404(media_access.user_app_access_id)
        else:
            raise Exception(f"Cannot update details for standalone service user {user_id}")
    changes_made_to_mum = False
    
    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        raise Exception("Plex server not found in media_servers table.")
    server = plex_servers[0]
    plex_service = MediaServiceFactory.create_service_from_db(server)
    if not plex_service:
        raise Exception("Failed to create Plex service from server configuration.")

    # --- MUM-only fields ---
    if notes is not None and user.notes != notes:
        user.notes = notes
        changes_made_to_mum = True

    if is_discord_bot_whitelisted is not None and user.is_discord_bot_whitelisted != is_discord_bot_whitelisted:
        user.is_discord_bot_whitelisted = is_discord_bot_whitelisted
        changes_made_to_mum = True
        log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' Discord Bot Whitelist set to {is_discord_bot_whitelisted}", user_id=user.id, admin_id=admin_id)

    if is_purge_whitelisted is not None and user.is_purge_whitelisted != is_purge_whitelisted:
        user.is_purge_whitelisted = is_purge_whitelisted
        changes_made_to_mum = True
        log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' Purge Whitelist set to {is_purge_whitelisted}", user_id=user.id, admin_id=admin_id)
        
    if allow_4k_transcode is not None and user.allow_4k_transcode != allow_4k_transcode:
        user.allow_4k_transcode = allow_4k_transcode
        changes_made_to_mum = True
        log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' Allow 4K Transcode set to {allow_4k_transcode}", user_id=user.id, admin_id=admin_id)


    # --- Plex-related settings ---
    libraries_changed = False
    if new_library_ids is not None:
        current_mum_libs_set = set(user.allowed_library_ids or [])
        new_library_ids_set = set(new_library_ids)
        if current_mum_libs_set != new_library_ids_set:
            libraries_changed = True
            user.allowed_library_ids = new_library_ids # Update MUM record
            changes_made_to_mum = True
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Manually updated libraries for '{user.get_display_name()}'.", user_id=user.id, admin_id=admin_id)

    downloads_changed = False
    if allow_downloads is not None and user.allow_downloads != allow_downloads:
        downloads_changed = True
        user.allow_downloads = allow_downloads # Update MUM record
        changes_made_to_mum = True
        log_event(EventType.SETTING_CHANGE, f"User '{user.get_display_name()}' Allow Downloads set to {allow_downloads}", user_id=user.id, admin_id=admin_id)
        
    # --- Make the API call to Plex ONLY IF a Plex-related setting changed ---
    if libraries_changed or downloads_changed:
        try:
            current_app.logger.info(f"[DEBUG-USER_SVC] Preparing to call plex_service.update_user_access for user '{user.get_display_name()}'. State to send -> library_ids_to_share: {user.allowed_library_ids}, allow_sync: {user.allow_downloads}")
            # **THE FIX**: We now pass the user's complete, final desired library and download state to the service.
            # Get Plex user ID from UserMediaAccess
            plex_access = user.get_server_access(plex_server_id)
            plex_user_id = plex_access.external_user_id if plex_access else None
            
            if plex_user_id:
                plex_service.update_user_access(
                    user_id=plex_user_id,
                    library_ids=user.allowed_library_ids,  # Always send the user's full library list
                    allow_downloads=user.allow_downloads                 # Always send the user's full download permission
                )
            else:
                current_app.logger.warning(f"Cannot update Plex user access - no plex_user_id found for {user.get_display_name()}")
        except Exception as e:
            # Re-raise the exception to be handled by the route, which can flash an error
            raise Exception(f"Failed to update Plex permissions for {user.get_display_name()}: {e}")

    # If any MUM-only field changed, mark the record as updated
    if changes_made_to_mum:
        user.updated_at = datetime.utcnow()
    
    # The calling route is responsible for db.session.commit()
    return user

def delete_user_from_mum_and_plex(user_id, admin_id: int = None):
    """Universal user deletion function that works with all media services"""
    # Parse prefixed user ID
    user_type, actual_id = parse_user_id(user_id)
    
    if user_type == "user_app_access":
        user = UserAppAccess.query.get_or_404(actual_id)
        username = user.get_display_name()
    else:
        # For user_media_access, handle standalone service user
        media_access = UserMediaAccess.query.get_or_404(actual_id)
        username = media_access.external_username or f"Service User {actual_id}"
        user = None  # No UserAppAccess for standalone users
    
    # Determine which service this user belongs to based on their data
    service_type = None
    user_service_id = None
    
    # Check if user has Plex access via UserMediaAccess
    from app.models_media_services import UserMediaAccess, ServiceType
    
    if user_type == "user_app_access" and user:
        # Local user - check their media accesses
        plex_access = UserMediaAccess.query.filter_by(
            user_app_access_id=user.id,
            server_id=MediaServer.query.filter_by(service_type=ServiceType.PLEX).first().id if MediaServer.query.filter_by(service_type=ServiceType.PLEX).first() else None
        ).first()
        
        if plex_access and plex_access.external_user_id:
            service_type = ServiceType.PLEX
            user_service_id = plex_access.external_user_id
        # Check for other service types via UserMediaAccess
        if not service_type:
            for access in user.media_accesses:
                if access.server and access.external_user_id:
                    service_type = access.server.service_type
                    user_service_id = access.external_user_id
                    break
    else:
        # Standalone service user - use the media_access directly
        if media_access.server and media_access.external_user_id:
            service_type = media_access.server.service_type
            user_service_id = media_access.external_user_id
    
    if not service_type:
        # Fallback: try to find any available service
        current_app.logger.warning(f"Could not determine service type for user {username}, trying available services...")
        for stype in [ServiceType.PLEX, ServiceType.JELLYFIN, ServiceType.EMBY]:
            servers = MediaServiceManager.get_servers_by_type(stype)
            if servers:
                service_type = stype
                break
        
        if not service_type:
            raise Exception("No media servers found for user deletion.")
    
    # Get the appropriate service
    servers = MediaServiceManager.get_servers_by_type(service_type)
    if not servers:
        raise Exception(f"{service_type.value} server not found in media_servers table.")
    
    server = servers[0]
    service = MediaServiceFactory.create_service_from_db(server)
    if not service:
        raise Exception(f"Failed to create {service_type.value} service from server configuration.")
    
    try:
        current_app.logger.info(f"Attempting to delete user '{username}' from {service_type.value} server using ID: {user_service_id}")
        
        # Delete from the media service
        if user_service_id:
            success = service.delete_user(user_service_id)
            if not success:
                raise Exception(f"Service returned failure for user deletion")
        else:
            current_app.logger.warning(f"No service user ID found for {username}, skipping service deletion")
        
        # Delete from MUM database
        if user_type == "user_app_access" and user:
            # Delete UserAppAccess (cascades to UserMediaAccess)
            db.session.delete(user)
        else:
            # Delete standalone UserMediaAccess
            db.session.delete(media_access)
        
        db.session.commit()
        
        log_event(EventType.MUM_USER_DELETED_FROM_MUM, 
                 f"User '{username}' removed from MUM and {service_type.value} server.", 
                 admin_id=admin_id, 
                 details={
                     'deleted_username': username, 
                     'deleted_user_id_in_mum': user_id, 
                     'service_type': service_type.value,
                     'service_user_id': user_service_id
                 })
        return True
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to fully delete user {username}: {e}", exc_info=True)
        # Log event for the failure as well
        log_event(EventType.ERROR_GENERAL, f"Failed to delete user {username}: {e}", admin_id=admin_id, user_id=user_id)
        raise Exception(f"Failed to remove user {username} from MUM: {e}")

def mass_update_user_libraries(user_ids: list, new_library_ids: list, admin_id: int = None):
    processed_count = 0; error_count = 0;
    
    # Parse prefixed user IDs and separate by type
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
            # Note: mass library updates only apply to local users with UserAppAccess
        except ValueError as e:
            current_app.logger.error(f"Mass Update Error: Invalid user ID format {user_id}: {e}")
            error_count += 1
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    db_library_value_to_set = list(new_library_ids) if new_library_ids is not None else [] # Ensure it's a list for DB
    plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX)
    if not plex_servers:
        raise Exception("Plex server not found in media_servers table.")
    server = plex_servers[0]
    plex_service = MediaServiceFactory.create_service_from_db(server)
    if not plex_service:
        raise Exception("Failed to create Plex service from server configuration.")

    for user in users_to_update:
        try:
            current_mum_libs = user.allowed_library_ids if user.allowed_library_ids is not None else []
            needs_plex_update = (set(current_mum_libs) != set(db_library_value_to_set))
            
            if needs_plex_update:
                # Get Plex user ID from UserMediaAccess
                plex_server = MediaServer.query.filter_by(service_type=ServiceType.PLEX).first()
                if plex_server:
                    plex_access = user.get_server_access(plex_server.id)
                    plex_user_id = plex_access.external_user_id if plex_access else None
                    
                    if plex_user_id:
                        plex_service.update_user_access(plex_user_id, db_library_value_to_set)
                    else:
                        current_app.logger.warning(f"Cannot update Plex user access - no plex_user_id found for {user.get_display_name()}")
                else:
                    current_app.logger.warning(f"Cannot update Plex user access - no Plex server configured") 
                user.allowed_library_ids = db_library_value_to_set 
                user.updated_at = datetime.utcnow()
            processed_count += 1
        except Exception as e:
            current_app.logger.error(f"Mass Update Error: User {user.get_display_name()} (ID: {user.id}): {e}");
            error_count += 1
    if processed_count > 0 or error_count > 0: 
        try:
            db.session.commit()
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Mass update: Libs processed for {processed_count} users.", admin_id=admin_id, details={'attempted_count': len(user_ids), 'success_count': processed_count - error_count, 'errors': error_count})
        except Exception as e:
            db.session.rollback(); current_app.logger.error(f"Mass Update: DB commit error: {e}");
            error_count = len(users_to_update); 
            raise Exception(f"Mass Update: DB commit failed: {e}")
    return processed_count, error_count

def mass_update_user_libraries_by_server(user_ids: list, updates_by_server: dict, admin_id: int = None):
    from app.models_media_services import UserMediaAccess

    processed_count = 0
    error_count = 0
    
    # Parse prefixed user IDs and separate by type
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
            # Note: mass library updates by server only apply to local users
        except ValueError as e:
            current_app.logger.error(f"Mass Update by Server Error: Invalid user ID format {user_id}: {e}")
            error_count += 1
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    user_map = {user.id: user for user in users_to_update}

    for server_id, new_library_ids in updates_by_server.items():
        server = MediaServiceManager.get_server_by_id(server_id)
        if not server:
            current_app.logger.error(f"Mass Update: Could not find server with ID {server_id}. Skipping.")
            error_count += len(user_ids) # Or be more specific if you can map users to this server
            continue

        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            current_app.logger.error(f"Mass Update: Could not create service for server {server.name}. Skipping.")
            error_count += len(user_ids)
            continue

        # Find which of the selected users have access to this server
        access_records = UserMediaAccess.query.filter(UserMediaAccess.server_id == server_id, UserMediaAccess.user_app_access_id.in_(user_ids)).all()
        
        for access in access_records:
            user = user_map.get(access.user_app_access_id)
            if not user:
                continue

            try:
                # This is a simplified update. The service method might need to be more generic.
                if hasattr(service, 'update_user_access'):
                    # Get the appropriate user ID from UserMediaAccess for this server
                    access = user.get_server_access(server.id)
                    service_user_id = access.external_user_id if access else None
                    
                    if service_user_id:
                        service.update_user_access(service_user_id, new_library_ids)
                    else:
                        current_app.logger.warning(f"Cannot update user access - no external_user_id found for {user.get_display_name()} on {server.name}")
                
                # Update the UserMediaAccess record
                access.allowed_library_ids = new_library_ids
                user.updated_at = datetime.utcnow()
                processed_count += 1
            except Exception as e:
                current_app.logger.error(f"Mass Update Error for user {user.get_display_name()} on server {server.name}: {e}")
                error_count += 1

    if processed_count > 0 or error_count > 0:
        try:
            db.session.commit()
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Mass library update by server complete. Processed {processed_count} user-server relations with {error_count} errors.", admin_id=admin_id)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Mass Update by Server: DB commit error: {e}")
            raise

    return processed_count, error_count


def mass_update_bot_whitelist(user_ids: list, should_whitelist: bool, admin_id: int = None):
    # Parse prefixed user IDs and get local user IDs
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
        except ValueError:
            continue  # Skip invalid IDs
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    updated_count = 0
    for user in users_to_update:
        if user.is_discord_bot_whitelisted != should_whitelist:
            user.is_discord_bot_whitelisted = should_whitelist
            user.updated_at = datetime.utcnow()
            updated_count +=1
    if updated_count > 0: db.session.commit()
    log_event(EventType.SETTING_CHANGE, f"Mass updated Discord Bot Whitelist for {updated_count} users to {should_whitelist}.", admin_id=admin_id, details={"count": updated_count, "whitelisted": should_whitelist})
    return updated_count

def mass_update_purge_whitelist(user_ids: list, should_whitelist: bool, admin_id: int = None):
    # Parse prefixed user IDs and get local user IDs
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
        except ValueError:
            continue  # Skip invalid IDs
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    updated_count = 0
    for user in users_to_update:
        if user.is_purge_whitelisted != should_whitelist:
            user.is_purge_whitelisted = should_whitelist
            user.updated_at = datetime.utcnow()
            updated_count +=1
    if updated_count > 0: db.session.commit()
    log_event(EventType.SETTING_CHANGE, f"Mass updated Purge Whitelist for {updated_count} users to {should_whitelist}.", admin_id=admin_id, details={"count": updated_count, "whitelisted": should_whitelist})
    return updated_count

def mass_delete_users(user_ids: list, admin_id: int = None):
    processed_count = 0; error_count = 0;
    usernames_for_log_detail = []
    
    # Import required models
    from app.models_media_services import UserMediaAccess
    
    # Parse prefixed user IDs and separate by type
    local_user_ids = []
    standalone_user_ids = []
    
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
            else:  # user_media_access
                standalone_user_ids.append(actual_id)
        except ValueError as e:
            current_app.logger.error(f"Mass Delete Error: Invalid user ID format {user_id}: {e}")
            error_count += 1
    
    # Delete UserAppAccess users (local users)
    if local_user_ids:
        users_to_delete = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
        
        for user in users_to_delete:
            username_for_log = user.get_display_name()
            try:
                # Get all UserMediaAccess records for this user to delete from all services
                user_accesses = UserMediaAccess.query.filter_by(user_app_access_id=user.id).all()
                
                for access in user_accesses:
                    server = access.server
                    if access.external_user_id:
                        try:
                            # Create service instance and delete user from the actual server
                            service = MediaServiceFactory.create_service_from_db(server)
                            if service and hasattr(service, 'delete_user'):
                                service.delete_user(access.external_user_id)
                                current_app.logger.info(f"Deleted user {username_for_log} from {server.service_type.value} server {server.name}")
                            else:
                                current_app.logger.warning(f"Cannot delete user from {server.service_type.value} server {server.name} - service not available or doesn't support deletion")
                        except Exception as e:
                            current_app.logger.error(f"Failed to delete user {username_for_log} from {server.service_type.value} server {server.name}: {e}")
                            # Continue with deletion from MUM even if server deletion fails
                
                # Delete the UserAppAccess from MUM (this will cascade to UserMediaAccess records)
                db.session.delete(user)
                processed_count += 1
                usernames_for_log_detail.append(username_for_log)
                current_app.logger.info(f"Deleted UserAppAccess {username_for_log} from MUM")
                
            except Exception as e:
                current_app.logger.error(f"Mass Delete Error: UserAppAccess {username_for_log} (ID: {user.id}): {e}")
                error_count += 1
    
    # Delete standalone UserMediaAccess users (service users)
    if standalone_user_ids:
        for standalone_user_id in standalone_user_ids:
            try:
                # Get the UserMediaAccess record directly using the actual ID
                access = UserMediaAccess.query.filter(
                    UserMediaAccess.id == standalone_user_id,
                    UserMediaAccess.user_app_access_id.is_(None)
                ).first()
                
                if access:
                    username_for_log = access.external_username or 'Unknown'
                    server = access.server
                    
                    # Delete from the actual server
                    if access.external_user_id and server:
                        try:
                            service = MediaServiceFactory.create_service_from_db(server)
                            if service and hasattr(service, 'delete_user'):
                                service.delete_user(access.external_user_id)
                                current_app.logger.info(f"Deleted standalone user {username_for_log} from {server.service_type.value} server {server.name}")
                            else:
                                current_app.logger.warning(f"Cannot delete user from {server.service_type.value} server {server.name} - service not available or doesn't support deletion")
                        except Exception as e:
                            current_app.logger.error(f"Failed to delete standalone user {username_for_log} from {server.service_type.value} server {server.name}: {e}")
                            # Continue with deletion from MUM even if server deletion fails
                    
                    # Delete the standalone UserMediaAccess record
                    db.session.delete(access)
                    processed_count += 1
                    usernames_for_log_detail.append(username_for_log)
                    current_app.logger.info(f"Deleted standalone UserMediaAccess {username_for_log} from MUM")
                else:
                    current_app.logger.warning(f"Standalone user with ID {standalone_user_id} not found")
                    error_count += 1
                    
            except Exception as e:
                current_app.logger.error(f"Mass Delete Error: Standalone user (ID: {standalone_user_id}): {e}")
                error_count += 1
    
    if processed_count > 0 : # Only commit if there were successful MUM deletions
        try:
            db.session.commit()
            if processed_count > 0: # Log only if actual MUM deletions were committed
                log_event(EventType.MUM_USER_DELETED_FROM_MUM, f"Mass delete: {processed_count} users removed from MUM and media servers.", admin_id=admin_id, details={'deleted_count': processed_count, 'errors': error_count, 'attempted_ids_count': len(user_ids), 'deleted_usernames_sample': usernames_for_log_detail[:10]})
        except Exception as e_commit:
            db.session.rollback(); current_app.logger.error(f"Mass Delete: DB commit error: {e_commit}");
            error_count = len(users_to_delete)
            processed_count = 0
            log_event(EventType.ERROR_GENERAL, f"Mass delete DB commit failed: {e_commit}", admin_id=admin_id, details={'attempted_count': len(user_ids)})
    elif error_count > 0: # No successes, only errors, still log the attempt
         log_event(EventType.ERROR_GENERAL, f"Mass delete attempt failed for all {error_count} users selected.", admin_id=admin_id, details={'attempted_count': len(user_ids), 'errors': error_count})


    return processed_count, error_count

def update_user_last_streamed(plex_user_id_or_uuid, last_streamed_at_datetime: datetime):
    # Find user via UserMediaAccess using Plex ID or UUID
    user = None
    from app.models_media_services import UserMediaAccess, ServiceType
    
    plex_server = MediaServer.query.filter_by(service_type=ServiceType.PLEX).first()
    if not plex_server:
        current_app.logger.warning(f"update_user_last_streamed: No Plex server configured")
        return False
    
    if isinstance(plex_user_id_or_uuid, int) or (isinstance(plex_user_id_or_uuid, str) and plex_user_id_or_uuid.isdigit()):
        # Look up by Plex user ID
        access = UserMediaAccess.query.filter_by(
            server_id=plex_server.id,
            external_user_id=str(plex_user_id_or_uuid)
        ).first()
        if access:
            user = access.service_account
    elif isinstance(plex_user_id_or_uuid, str):
        # Look up by Plex UUID
        access = UserMediaAccess.query.filter_by(
            server_id=plex_server.id,
            external_user_alt_id=plex_user_id_or_uuid
        ).first()
        if access:
            user = access.service_account
    else:
        current_app.logger.warning(f"User_Service.py - update_user_last_streamed(): Unexpected type for plex_user_id_or_uuid: {type(plex_user_id_or_uuid)}")
        return False
    
    if not user:
        current_app.logger.debug(f"update_user_last_streamed: No user found for Plex ID/UUID: {plex_user_id_or_uuid}")
        return False

    if user:
        if last_streamed_at_datetime.tzinfo is None: 
            last_streamed_at_datetime = last_streamed_at_datetime.replace(tzinfo=timezone.utc)
        
        db_last_streamed_at_naive = user.last_streamed_at 
        db_last_streamed_at_aware = None
        if db_last_streamed_at_naive:
            db_last_streamed_at_aware = db_last_streamed_at_naive.replace(tzinfo=timezone.utc)
        
        if db_last_streamed_at_aware is None or last_streamed_at_datetime > db_last_streamed_at_aware:
            user.last_streamed_at = last_streamed_at_datetime.replace(tzinfo=None) 
            user.updated_at = datetime.utcnow().replace(tzinfo=None)
            try: 
                db.session.commit()
                current_app.logger.info(f"User_Service.py - update_user_last_streamed(): Updated last_streamed_at for {user.get_display_name()} to {user.last_streamed_at}")
                return True
            except Exception as e: 
                db.session.rollback()
                current_app.logger.error(f"User_Service.py - update_user_last_streamed(): DB Commit Error for user {user.get_display_name()} (Plex ID/UUID: {plex_user_id_or_uuid}): {e}", exc_info=True)
        # else:
            # current_app.logger.debug(f"User_Service.py - update_user_last_streamed(): No update needed for {user.get_display_name()}. DB: {db_last_streamed_at_aware}, Current: {last_streamed_at_datetime}")
    # else:
        # current_app.logger.warning(f"User_Service.py - update_user_last_streamed(): User not found in MUM with Plex ID/UUID: {plex_user_id_or_uuid}.")
    return False

def update_user_last_streamed_by_id(user_id, last_streamed_at_datetime: datetime):
    """Universal function to update last_streamed_at for any user by their MUM user ID"""
    # Parse prefixed user ID
    try:
        user_type, actual_id = parse_user_id(user_id)
        
        if user_type == "user_app_access":
            user = UserAppAccess.query.get(actual_id)
        else:
            # For user_media_access, get the associated UserAppAccess if it exists
            media_access = UserMediaAccess.query.get(actual_id)
            if media_access and media_access.user_app_access_id:
                user = UserAppAccess.query.get(media_access.user_app_access_id)
            else:
                current_app.logger.warning(f"User_Service.py - update_user_last_streamed_by_id(): Cannot update last streamed for standalone service user {user_id}")
                return False
    except ValueError as e:
        current_app.logger.warning(f"User_Service.py - update_user_last_streamed_by_id(): Invalid user ID format {user_id}: {e}")
        return False
    
    if not user:
        current_app.logger.warning(f"User_Service.py - update_user_last_streamed_by_id(): User not found with ID: {user_id}")
        return False

    if last_streamed_at_datetime.tzinfo is None: 
        last_streamed_at_datetime = last_streamed_at_datetime.replace(tzinfo=timezone.utc)
    
    db_last_streamed_at_naive = user.last_streamed_at 
    db_last_streamed_at_aware = None
    if db_last_streamed_at_naive:
        db_last_streamed_at_aware = db_last_streamed_at_naive.replace(tzinfo=timezone.utc)
    
    if db_last_streamed_at_aware is None or last_streamed_at_datetime > db_last_streamed_at_aware:
        user.last_streamed_at = last_streamed_at_datetime.replace(tzinfo=None) 
        user.updated_at = datetime.utcnow().replace(tzinfo=None)
        try: 
            db.session.commit()
            # Updated user last streamed timestamp
            return True
        except Exception as e: 
            db.session.rollback()
            current_app.logger.error(f"User_Service.py - update_user_last_streamed_by_id(): DB Commit Error for user {user.get_display_name()} (ID: {user_id}): {e}", exc_info=True)
    return False

def purge_inactive_users(user_ids_to_purge: list[int], admin_id: int, inactive_days_threshold: int, exclude_sharers: bool, exclude_whitelisted: bool, ignore_creation_date_for_never_streamed: bool):
    """
    Deletes a specific list of users, but only after re-validating them
    against the provided criteria as a final safety check.
    """
    if not user_ids_to_purge:
        return {"message": "No users were selected for purge.", "purged_count": 0, "errors": 0}

    # Re-run the eligibility check on the provided list of users as a safeguard.
    eligible_users_final = get_users_eligible_for_purge(
        inactive_days_threshold, exclude_sharers, exclude_whitelisted, ignore_creation_date_for_never_streamed
    )
    final_ids_to_delete = {user['id'] for user in eligible_users_final}.intersection(set(user_ids_to_purge))
    
    purged_count = 0
    error_count = 0
    
    users_to_process = UserAppAccess.query.filter(UserAppAccess.id.in_(final_ids_to_delete)).all()

    for user in users_to_process:
        try:
            from app.services.unified_user_service import UnifiedUserService
            UnifiedUserService.delete_user_completely(user.id, admin_id=admin_id)
            purged_count += 1
        except Exception as e:
            error_count += 1
            current_app.logger.error(f"User_Service.py - purge_inactive_users(): Error purging user {user.get_display_name()} (ID: {user.id}): {e}")

    result_message = f"Purge complete: {purged_count} users removed."
    if len(final_ids_to_delete) != len(user_ids_to_purge):
        result_message += f" ({len(user_ids_to_purge) - len(final_ids_to_delete)} skipped by final safety check)."
    if error_count > 0:
        result_message += f" {error_count} errors."

    log_event(EventType.MUM_USER_DELETED_FROM_MUM, result_message, admin_id=admin_id, details={
        "action": "purge_selected_inactive_users", "purged_count": purged_count, "errors": error_count
    })
    
    return {"message": result_message, "purged_count": purged_count, "errors": error_count}

def get_users_eligible_for_purge(inactive_days_threshold: int, exclude_sharers: bool, exclude_whitelisted: bool, ignore_creation_date_for_never_streamed: bool = False):
    if inactive_days_threshold < 1:
        raise ValueError("Inactivity threshold must be at least 1 day.")

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=inactive_days_threshold)
    
    # Base query for UserAppAccess (no home_user concept in new architecture)
    query = UserAppAccess.query

    # --- START OF FIX ---
    # Conditionally add filters based on the checkboxes.
    # Note: home_user and shares_back concepts don't exist in UserAppAccess
    # These were Plex-specific concepts that are now handled differently
    
    if exclude_whitelisted: 
        # Check if any of the user's media accesses are purge whitelisted
        from app.models_media_services import UserMediaAccess
        whitelisted_user_ids = db.session.query(UserMediaAccess.user_app_access_id).filter(
            UserMediaAccess.is_purge_whitelisted == True
        ).distinct().subquery()
        query = query.filter(~UserAppAccess.id.in_(whitelisted_user_ids))
    # --- END OF FIX ---
        
    eligible_users_list = []
    potential_users = query.all()

    for user in potential_users:
        is_eligible_for_purge = False
        
        # UserAppAccess doesn't have last_streamed_at - need to check MediaStreamHistory
        from app.models_media_services import MediaStreamHistory
        last_stream = MediaStreamHistory.query.filter_by(user_app_access_id=user.id).order_by(MediaStreamHistory.started_at.desc()).first()
        last_streamed_at = last_stream.started_at if last_stream else None
        
        if last_streamed_at is None:
            if ignore_creation_date_for_never_streamed:
                is_eligible_for_purge = True
            else:
                created_at_aware = user.created_at.replace(tzinfo=timezone.utc) if user.created_at.tzinfo is None else user.created_at
                if created_at_aware < cutoff_date:
                    is_eligible_for_purge = True
        else: 
            last_streamed_aware = last_streamed_at.replace(tzinfo=timezone.utc) if last_streamed_at.tzinfo is None else last_streamed_at
            if last_streamed_aware < cutoff_date:
                is_eligible_for_purge = True
        
        if is_eligible_for_purge:
            eligible_users_list.append({ 'id': user.id, 'username': user.get_display_name(), 'email': user.email, 'last_streamed_at': last_streamed_at, 'created_at': user.created_at })
            
    return eligible_users_list

def get_user_stream_stats(user_id):
    """Aggregates stream history for a user to produce Tautulli-like stats."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Parse prefixed user ID to determine query filter
    try:
        user_type, actual_id = parse_user_id(user_id)
        current_app.logger.info(f"DEBUG STATS SERVICE: Parsed user_id {user_id} -> type: {user_type}, actual_id: {actual_id}")
    except ValueError as e:
        current_app.logger.error(f"Invalid user ID format in get_user_stream_stats: {user_id}: {e}")
        return {'global': {}, 'players': []}

    # Build query filter based on user type
    if user_type == "user_app_access":
        filter_condition = MediaStreamHistory.user_app_access_id == actual_id
    else:  # user_media_access
        filter_condition = MediaStreamHistory.user_media_access_id == actual_id

    # --- Global Stats ---
    # Perform all aggregations in a single query for efficiency
    stats_query = db.session.query(
        func.count(MediaStreamHistory.id).label('all_time_plays'),
        func.sum(MediaStreamHistory.duration_seconds).label('all_time_duration'),
        func.sum(case((MediaStreamHistory.started_at >= day_ago, MediaStreamHistory.duration_seconds), else_=0)).label('duration_24h'),
        func.count(case((MediaStreamHistory.started_at >= day_ago, 1), else_=None)).label('plays_24h'),
        func.sum(case((MediaStreamHistory.started_at >= week_ago, MediaStreamHistory.duration_seconds), else_=0)).label('duration_7d'),
        func.count(case((MediaStreamHistory.started_at >= week_ago, 1), else_=None)).label('plays_7d'),
        func.count(case((MediaStreamHistory.started_at >= month_ago, 1), else_=None)).label('plays_30d'),
        func.sum(case((MediaStreamHistory.started_at >= month_ago, MediaStreamHistory.duration_seconds), else_=0)).label('duration_30d')
    ).filter(filter_condition).first()

    current_app.logger.info(f"DEBUG STATS SERVICE: Query result - plays: {stats_query.all_time_plays}, duration: {stats_query.all_time_duration}")

    global_stats = {
        'plays_24h': stats_query.plays_24h or 0,
        'duration_24h': format_duration(stats_query.duration_24h or 0),
        'plays_7d': stats_query.plays_7d or 0,
        'duration_7d': format_duration(stats_query.duration_7d or 0),
        'plays_30d': stats_query.plays_30d or 0,
        'duration_30d': format_duration(stats_query.duration_30d or 0),
        'all_time_plays': stats_query.all_time_plays or 0,
        'all_time_duration': format_duration(stats_query.all_time_duration or 0),
        'all_time_duration_seconds': stats_query.all_time_duration or 0  # Add raw seconds for template
    }

    # --- Player Stats ---
    player_stats_query = db.session.query(
        MediaStreamHistory.player,
        func.count(MediaStreamHistory.id).label('play_count')
    ).filter(filter_condition).group_by(MediaStreamHistory.player).order_by(func.count(MediaStreamHistory.id).desc()).all()

    player_stats = [{'name': p.player or 'Unknown', 'plays': p.play_count} for p in player_stats_query]

    return {'global': global_stats, 'players': player_stats}

def get_bulk_user_stream_stats(user_ids: list[int]) -> dict:
    """
    Efficiently gets total plays and duration for a list of user IDs.
    Returns a dictionary mapping user_id to its stats.
    """
    if not user_ids:
        return {}

    results = db.session.query(
        MediaStreamHistory.user_app_access_id,
        func.count(MediaStreamHistory.id).label('total_plays'),
        func.sum(MediaStreamHistory.duration_seconds).label('total_duration')
    ).filter(MediaStreamHistory.user_app_access_id.in_(user_ids)).group_by(MediaStreamHistory.user_app_access_id).all()

    return {
        user_id: {'play_count': plays, 'total_duration': duration or 0}
        for user_id, plays, duration in results
    }

def get_bulk_last_known_ips(user_ids: list[int]) -> dict:
    """
    Efficiently gets the most recent IP address for a list of user IDs.
    Returns a dictionary mapping user_id to the last known IP address.
    """
    if not user_ids:
        return {}

    # Use a subquery to rank history entries by date for each user
    subquery = db.session.query(
        MediaStreamHistory.user_app_access_id,
        MediaStreamHistory.ip_address,
        func.row_number().over(
            partition_by=MediaStreamHistory.user_app_access_id,
            order_by=MediaStreamHistory.started_at.desc()
        ).label('rn')
    ).filter(MediaStreamHistory.user_app_access_id.in_(user_ids)).filter(MediaStreamHistory.ip_address.isnot(None)).subquery()

    # Select only the most recent entry (rank = 1) for each user
    results = db.session.query(subquery.c.user_app_access_id, subquery.c.ip_address).filter(subquery.c.rn == 1).all()

    return {user_id: ip_address for user_id, ip_address in results}

def mass_extend_access(user_ids: list, days_to_extend: int, admin_id: int = None):
    """Extend access expiration for multiple users by a specified number of days"""
    from datetime import datetime, timedelta
    processed_count = 0
    error_count = 0
    
    # Parse prefixed user IDs and get local user IDs
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
        except ValueError:
            error_count += 1
            continue
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    
    for user in users_to_update:
        try:
            # If user has no expiration, set it to today + extension days
            if user.access_expires_at is None:
                user.access_expires_at = datetime.now() + timedelta(days=days_to_extend)
            else:
                # If user already has expiration, extend it by the specified days
                user.access_expires_at = user.access_expires_at + timedelta(days=days_to_extend)
            
            processed_count += 1
        except Exception as e:
            current_app.logger.error(f"Error extending access for user {user.get_display_name()}: {e}")
            error_count += 1
    
    if processed_count > 0:
        try:
            db.session.commit()
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Mass extend access: {processed_count} users extended by {days_to_extend} days.", admin_id=admin_id)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error committing mass extend access: {e}")
            error_count = len(users_to_update)
            processed_count = 0
    
    return processed_count, error_count

def mass_set_expiration(user_ids: list, expiration_date, admin_id: int = None):
    """Set expiration date for multiple users"""
    from datetime import datetime, time
    processed_count = 0
    error_count = 0
    
    # Parse prefixed user IDs and get local user IDs
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
        except ValueError:
            error_count += 1
            continue
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    
    # Convert date to datetime at end of day
    if hasattr(expiration_date, 'date'):
        # If it's already a datetime, extract the date
        expiration_datetime = datetime.combine(expiration_date.date(), time(23, 59, 59))
    else:
        # If it's a date object
        expiration_datetime = datetime.combine(expiration_date, time(23, 59, 59))
    
    for user in users_to_update:
        try:
            user.access_expires_at = expiration_datetime
            processed_count += 1
        except Exception as e:
            current_app.logger.error(f"Error setting expiration for user {user.get_display_name()}: {e}")
            error_count += 1
    
    if processed_count > 0:
        try:
            db.session.commit()
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Mass set expiration: {processed_count} users set to expire on {expiration_date}.", admin_id=admin_id)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error committing mass set expiration: {e}")
            error_count = len(users_to_update)
            processed_count = 0
    
    return processed_count, error_count

def mass_clear_expiration(user_ids: list, admin_id: int = None):
    """Clear expiration date for multiple users (set to never expire)"""
    processed_count = 0
    error_count = 0
    
    # Parse prefixed user IDs and get local user IDs
    local_user_ids = []
    for user_id in user_ids:
        try:
            user_type, actual_id = parse_user_id(user_id)
            if user_type == "user_app_access":
                local_user_ids.append(actual_id)
        except ValueError:
            error_count += 1
            continue
    
    users_to_update = UserAppAccess.query.filter(UserAppAccess.id.in_(local_user_ids)).all()
    
    for user in users_to_update:
        try:
            user.access_expires_at = None
            processed_count += 1
        except Exception as e:
            current_app.logger.error(f"Error clearing expiration for user {user.get_display_name()}: {e}")
            error_count += 1
    
    if processed_count > 0:
        try:
            db.session.commit()
            log_event(EventType.MUM_USER_LIBRARIES_EDITED, f"Mass clear expiration: {processed_count} users set to never expire.", admin_id=admin_id)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error committing mass clear expiration: {e}")
            error_count = len(users_to_update)
            processed_count = 0
    
    return processed_count, error_count