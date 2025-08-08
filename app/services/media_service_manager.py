# File: app/services/media_service_manager.py
from typing import List, Dict, Any, Optional
from flask import current_app
from app.models_media_services import MediaServer, MediaLibrary, UserMediaAccess, ServiceType
from app.models import User, Setting
from app.services.media_service_factory import MediaServiceFactory
from app.extensions import db
from datetime import datetime

class MediaServiceManager:
    """Centralized manager for all media services"""
    
    @staticmethod
    def get_all_servers(active_only: bool = True) -> List[MediaServer]:
        """Get all configured media servers"""
        query = MediaServer.query
        if active_only:
            query = query.filter_by(is_active=True)
        return query.all()
    
    @staticmethod
    def get_servers_by_type(service_type: ServiceType, active_only: bool = True) -> List[MediaServer]:
        """Get servers of a specific type"""
        query = MediaServer.query.filter_by(service_type=service_type)
        if active_only:
            query = query.filter_by(is_active=True)
        return query.all()
    
    @staticmethod
    def get_server_by_id(server_id: int) -> Optional[MediaServer]:
        """Get a server by ID"""
        return MediaServer.query.get(server_id)
    
    @staticmethod
    def test_server_connection(server_id: int) -> Dict[str, Any]:
        """Test connection to a specific server"""
        server = MediaServiceManager.get_server_by_id(server_id)
        if not server:
            return {'success': False, 'message': 'Server not found'}
        
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {'success': False, 'message': 'Service type not supported'}
        
        try:
            success, message = service.test_connection()
            return {'success': success, 'message': message}
        except Exception as e:
            return {'success': False, 'message': f'Connection test failed: {str(e)}'}
    
    @staticmethod
    def sync_server_libraries(server_id: int) -> Dict[str, Any]:
        """Sync libraries for a specific server"""
        server = MediaServiceManager.get_server_by_id(server_id)
        if not server:
            return {'success': False, 'message': 'Server not found'}
        
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {'success': False, 'message': 'Service type not supported'}
        
        try:
            current_app.logger.info(f"Starting library sync for server {server_id} ({server.name})")
            libraries_data = service.get_libraries()
            current_app.logger.info(f"Retrieved {len(libraries_data)} libraries from {server.name}")
            
            # Update database
            existing_libs = {lib.external_id: lib for lib in server.libraries}
            current_app.logger.info(f"Found {len(existing_libs)} existing libraries in database")
            updated_count = 0
            added_count = 0
            
            for lib_data in libraries_data:
                external_id = lib_data['external_id']
                current_app.logger.debug(f"Processing library: {lib_data['name']} (ID: {external_id})")
                
                if external_id in existing_libs:
                    # Update existing library
                    lib = existing_libs[external_id]
                    lib.name = lib_data['name']
                    lib.library_type = lib_data.get('type')
                    lib.item_count = lib_data.get('item_count')
                    lib.updated_at = datetime.utcnow()
                    updated_count += 1
                    current_app.logger.debug(f"Updated existing library: {lib_data['name']}")
                else:
                    # Add new library
                    lib = MediaLibrary(
                        server_id=server_id,
                        external_id=external_id,
                        name=lib_data['name'],
                        library_type=lib_data.get('type'),
                        item_count=lib_data.get('item_count')
                    )
                    db.session.add(lib)
                    added_count += 1
                    current_app.logger.debug(f"Added new library: {lib_data['name']}")
            
            server.last_sync_at = datetime.utcnow()
            db.session.commit()
            current_app.logger.info(f"Library sync completed: {added_count} added, {updated_count} updated")
            
            return {
                'success': True,
                'message': f'Synced {len(libraries_data)} libraries',
                'added': added_count,
                'updated': updated_count
            }
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error syncing libraries for server {server_id} ({server.name}): {e}", exc_info=True)
            return {'success': False, 'message': f'Sync failed: {str(e)}'}
    
    @staticmethod
    def sync_server_users(server_id: int) -> Dict[str, Any]:
        """Sync users for a specific server, tracking detailed changes."""
        server = MediaServiceManager.get_server_by_id(server_id)
        if not server:
            return {'success': False, 'message': 'Server not found'}

        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return {'success': False, 'message': 'Service type not supported'}

        try:
            users_data = service.get_users()
            
            added_count = 0
            updated_count = 0
            removed_count = 0
            updated_details = []

            # For enriching library change details
            server_libraries = {lib.external_id: lib.name for lib in server.libraries}
            external_user_ids_from_service = {str(u.get('id')) for u in users_data if u.get('id')}

            for user_data in users_data:
                user = MediaServiceManager._find_or_create_user(user_data, server)
                if not user:
                    continue

                access = UserMediaAccess.query.filter_by(user_id=user.id, server_id=server_id).first()
                
                if not access:
                    access = UserMediaAccess(
                        user_id=user.id,
                        server_id=server_id,
                        external_user_id=user_data.get('id'),
                        external_username=user_data.get('username'),
                        external_email=user_data.get('email'),
                        allowed_library_ids=user_data.get('library_ids', []),
                        is_active=True
                    )
                    db.session.add(access)
                    added_count += 1
                else:
                    changes = []
                    if access.external_username != user_data.get('username'):
                        changes.append(f"Username changed from '{access.external_username}' to '{user_data.get('username')}'")
                        access.external_username = user_data.get('username')
                    if access.external_email != user_data.get('email'):
                        changes.append(f"Email changed from '{access.external_email}' to '{user_data.get('email')}'")
                        access.external_email = user_data.get('email')
                    
                    old_library_ids = set(access.allowed_library_ids or [])
                    new_library_ids = set(user_data.get('library_ids', []))
                    if old_library_ids != new_library_ids:
                        added_ids = new_library_ids - old_library_ids
                        removed_ids = old_library_ids - new_library_ids

                        if added_ids:
                            added_names = [server_libraries.get(id, f"Unknown Library (ID: {id})") for id in added_ids]
                            changes.append(f"Gained access to: {', '.join(added_names)}")
                        
                        if removed_ids:
                            removed_names = [server_libraries.get(id, f"Unknown Library (ID: {id})") for id in removed_ids]
                            changes.append(f"Lost access to: {', '.join(removed_names)}")

                        access.allowed_library_ids = user_data.get('library_ids', [])
                    
                    if changes:
                        updated_count += 1
                        updated_details.append({'username': user.get_display_name(), 'changes': changes})
                        access.updated_at = datetime.utcnow()

            # Process removals
            access_records_to_check = UserMediaAccess.query.filter_by(server_id=server_id).all()
            for access in access_records_to_check:
                if str(access.external_user_id) not in external_user_ids_from_service:
                    user_to_check = User.query.get(access.user_id)
                    db.session.delete(access)
                    removed_count += 1
                    if user_to_check and not user_to_check.media_accesses.count():
                        db.session.delete(user_to_check)

            db.session.commit()
            
            return {
                'success': True,
                'message': f'Synced {len(users_data)} users',
                'added': added_count,
                'updated': updated_count,
                'removed': removed_count,
                'updated_details': updated_details
            }
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error syncing users for server {server_id}: {e}", exc_info=True)
            return {'success': False, 'message': f'Sync failed: {str(e)}'}
    
    @staticmethod
    def _find_or_create_user(user_data: Dict[str, Any], server: MediaServer) -> User:
        """Find existing user or create new one based on user data"""
        username = user_data.get('username')
        email = user_data.get('email')
        
        # Try to find existing user
        user = None
        
        # For Plex, try to match by UUID first, then by username
        if server.service_type == ServiceType.PLEX:
            uuid = user_data.get('uuid')
            if uuid:
                user = User.query.filter_by(plex_uuid=uuid).first()
            if not user and username:
                user = User.query.filter_by(plex_username=username).first()
        
        # Try to match by primary username or email
        if not user and username:
            user = User.query.filter_by(primary_username=username).first()
        if not user and email:
            user = User.query.filter_by(primary_email=email).first()
        
        if not user:
            # Create new user
            primary_username_value = username or email or f"user_{user_data.get('id', 'unknown')}"
            current_app.logger.info(f"Creating new user with primary_username='{primary_username_value}', username='{username}', email='{email}'")
            
            user = User(
                primary_username=primary_username_value,
                primary_email=email,
                avatar_url=user_data.get('thumb'),
                shares_back=user_data.get('shares_back', False)
            )
            
            current_app.logger.info(f"Created user object: primary_username='{user.primary_username}', display_name='{user.get_display_name()}'")
            
            # Set service-specific fields based on server type
            if server.service_type == ServiceType.JELLYFIN:
                current_app.logger.info(f"Setting Jellyfin-specific fields for user '{username}'")
                # Store raw Jellyfin data for debugging purposes
                if user_data.get('raw_data'):
                    import json
                    user.raw_service_data = json.dumps(user_data.get('raw_data'))  # Convert dict to JSON string
                    current_app.logger.info(f"Stored raw Jellyfin data for user '{username}'")
            
            # Set Kavita-specific fields if this is a Kavita server
            elif server.service_type == ServiceType.KAVITA:
                current_app.logger.info(f"Setting Kavita-specific fields for user '{username}'")
                # Store raw Kavita data for debugging purposes
                if user_data.get('raw_data'):
                    import json
                    user.raw_service_data = json.dumps(user_data.get('raw_data'))  # Convert dict to JSON string
                    current_app.logger.info(f"Stored raw Kavita data for user '{username}'")
            
            # Set Plex-specific fields if this is a Plex server
            elif server.service_type == ServiceType.PLEX:
                user.plex_user_id = user_data.get('id')
                user.plex_username = username
                user.plex_email = email
                user.plex_uuid = user_data.get('uuid')
                user.plex_thumb_url = user_data.get('thumb')
                user.is_home_user = user_data.get('is_home_user', False)
                user.raw_service_data = user_data.get('raw_data')  # Store raw data for new users
                
                # Parse and set plex_join_date from acceptedAt timestamp
                accepted_at_str = user_data.get('accepted_at')
                if accepted_at_str and str(accepted_at_str).isdigit():
                    try:
                        from datetime import timezone
                        plex_join_date_dt = datetime.fromtimestamp(int(accepted_at_str), tz=timezone.utc)
                        user.plex_join_date = plex_join_date_dt.replace(tzinfo=None)
                        current_app.logger.debug(f"Set plex_join_date for new user {username}: {user.plex_join_date}")
                    except (ValueError, TypeError) as e:
                        current_app.logger.warning(f"Failed to parse acceptedAt '{accepted_at_str}' for user {username}: {e}")
            
            db.session.add(user)
            db.session.flush()  # Get the ID
            current_app.logger.info(f"User added to session and flushed: ID={user.id}, primary_username='{user.primary_username}', display_name='{user.get_display_name()}'")
            current_app.logger.info(f"User object after flush: {user.__dict__}")
        else:
            # Update existing user
            user.shares_back = user_data.get('shares_back', False)
            
            # Update raw data based on server type
            if server.service_type == ServiceType.PLEX and user_data.get('raw_data'):
                user.raw_service_data = user_data.get('raw_data')
                
                # Update plex_join_date if we have acceptedAt data
                accepted_at_str = user_data.get('accepted_at')
                if accepted_at_str and str(accepted_at_str).isdigit():
                    try:
                        from datetime import timezone
                        plex_join_date_dt = datetime.fromtimestamp(int(accepted_at_str), tz=timezone.utc)
                        new_join_date = plex_join_date_dt.replace(tzinfo=None)
                        if user.plex_join_date != new_join_date:
                            user.plex_join_date = new_join_date
                            current_app.logger.debug(f"Updated plex_join_date for user {user.get_display_name()}: {user.plex_join_date}")
                    except (ValueError, TypeError) as e:
                        current_app.logger.warning(f"Failed to parse acceptedAt '{accepted_at_str}' for user {user.get_display_name()}: {e}")
            
            elif server.service_type == ServiceType.JELLYFIN and user_data.get('raw_data'):
                # Update raw Jellyfin data for existing users
                import json
                user.raw_service_data = json.dumps(user_data.get('raw_data'))  # Convert dict to JSON string
                current_app.logger.info(f"Updated raw Jellyfin data for existing user '{user.get_display_name()}'")
            
            elif server.service_type == ServiceType.KAVITA and user_data.get('raw_data'):
                # Update raw Kavita data for existing users
                import json
                user.raw_service_data = json.dumps(user_data.get('raw_data'))  # Convert dict to JSON string
                current_app.logger.info(f"Updated raw Kavita data for existing user '{user.get_display_name()}'")

        return user
    
    @staticmethod
    def get_all_active_sessions() -> List[Dict[str, Any]]:
        """Get active sessions from all servers"""
        all_sessions = []
        
        for server in MediaServiceManager.get_all_servers():
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                try:
                    sessions = service.get_active_sessions()
                    for session in sessions:
                        if isinstance(session, dict):
                            session['server_name'] = server.name
                            session['server_id'] = server.id
                            session['service_type'] = server.service_type.value
                        else:
                            setattr(session, 'server_name', server.name)
                            setattr(session, 'server_id', server.id)
                            setattr(session, 'service_type', server.service_type.value)
                    all_sessions.extend(sessions)
                except Exception as e:
                    current_app.logger.error(f"Error getting sessions from {server.name}: {e}")
        
        return all_sessions
    
    @staticmethod
    def terminate_session(server_id: int, session_id: str, reason: str = None) -> bool:
        """Terminate a session on a specific server"""
        server = MediaServiceManager.get_server_by_id(server_id)
        if not server:
            return False
        
        service = MediaServiceFactory.create_service_from_db(server)
        if not service:
            return False
        
        try:
            return service.terminate_session(session_id, reason)
        except Exception as e:
            current_app.logger.error(f"Error terminating session on {server.name}: {e}")
            return False
    
    @staticmethod
    def create_default_plex_server() -> Optional[MediaServer]:
        """Create a default Plex server from existing settings"""
        plex_url = Setting.get('PLEX_URL')
        plex_token = Setting.get('PLEX_TOKEN')
        
        if not plex_url or not plex_token:
            return None
        
        # Check if Plex server already exists
        existing = MediaServer.query.filter_by(
            service_type=ServiceType.PLEX,
            url=plex_url
        ).first()
        
        if existing:
            return existing
        
        server = MediaServer(
            name="Plex Media Server",
            service_type=ServiceType.PLEX,
            url=plex_url,
            api_key=plex_token,
            is_active=True
        )
        
        db.session.add(server)
        db.session.commit()
        
        return server