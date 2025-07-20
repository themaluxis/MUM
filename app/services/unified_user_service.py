# File: app/services/unified_user_service.py
from typing import List, Dict, Any, Optional
from flask import current_app
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from app.models import User
from app.models_media_services import MediaServer, UserMediaAccess
from app.services.media_service_manager import MediaServiceManager
from app.services.media_service_factory import MediaServiceFactory
from app.extensions import db
from app.utils.helpers import log_event
from app.models import EventType

class UnifiedUserService:
    """Service for managing users across all media services"""
    
    @staticmethod
    def sync_all_users() -> Dict[str, Any]:
        """Sync users from all active media servers"""
        servers = MediaServiceManager.get_all_servers()
        total_added = 0
        total_updated = 0
        total_removed = 0
        total_errors = 0
        error_messages = []
        all_updated_details = []

        for server in servers:
            try:
                result = MediaServiceManager.sync_server_users(server.id)
                if result['success']:
                    total_added += result.get('added', 0)
                    total_updated += result.get('updated', 0)
                    total_removed += result.get('removed', 0)
                    if result.get('updated_details'):
                        all_updated_details.extend(result['updated_details'])
                else:
                    total_errors += 1
                    error_messages.append(f"{server.name}: {result['message']}")
            except Exception as e:
                total_errors += 1
                error_messages.append(f"{server.name}: {str(e)}")
                current_app.logger.error(f"Error syncing users from {server.name}: {e}")

        return {
            'success': total_errors == 0,
            'added': total_added,
            'updated': total_updated,
            'removed': total_removed,
            'errors': total_errors,
            'error_messages': error_messages,
            'servers_synced': len(servers),
            'updated_details': all_updated_details
        }
    
    @staticmethod
    def get_user_with_access_info(user_id: int) -> Optional[Dict[str, Any]]:
        """Get user with their access information across all servers"""
        user = User.query.get(user_id)
        if not user:
            return None
        
        # Get access information for all servers
        access_info = []
        for access in user.media_accesses:
            server = access.server
            service = MediaServiceFactory.create_service_from_db(server)
            
            # Get library names
            library_names = []
            if access.allowed_library_ids:
                for lib in server.libraries:
                    if lib.external_id in access.allowed_library_ids:
                        library_names.append(lib.name)
            
            access_info.append({
                'server_id': server.id,
                'server_name': server.name,
                'service_type': server.service_type.value,
                'external_user_id': access.external_user_id,
                'external_username': access.external_username,
                'allowed_library_ids': access.allowed_library_ids,
                'library_names': library_names,
                'allow_downloads': access.allow_downloads,
                'is_active': access.is_active,
                'last_activity': access.last_activity_at,
                'supports_downloads': service.supports_feature('downloads') if service else False,
                'supports_transcoding': service.supports_feature('transcoding') if service else False
            })
        
        return {
            'user': user,
            'access_info': access_info,
            'total_servers': len(access_info),
            'active_servers': len([a for a in access_info if a['is_active']])
        }
    
    @staticmethod
    def update_user_access_on_server(user_id: int, server_id: int, 
                                   library_ids: List[str] = None, 
                                   allow_downloads: bool = None,
                                   admin_id: int = None) -> bool:
        """Update user's access on a specific server"""
        user = User.query.get(user_id)
        server = MediaServiceManager.get_server_by_id(server_id)
        
        if not user or not server:
            return False
        
        # Get or create UserMediaAccess
        access = UserMediaAccess.query.filter_by(
            user_id=user_id,
            server_id=server_id
        ).first()
        
        if not access:
            current_app.logger.warning(f"No access record found for user {user_id} on server {server_id}")
            return False
        
        # Update local database first
        changes_made = False
        
        if library_ids is not None:
            access.allowed_library_ids = library_ids
            changes_made = True
        
        if allow_downloads is not None:
            access.allow_downloads = allow_downloads
            changes_made = True
        
        if changes_made:
            access.updated_at = datetime.utcnow()
        
        # Update on the actual service
        service = MediaServiceFactory.create_service_from_db(server)
        if service and access.external_user_id:
            try:
                service_success = service.update_user_access(
                    access.external_user_id,
                    library_ids=library_ids,
                    allow_downloads=allow_downloads
                )
                
                if not service_success:
                    current_app.logger.warning(f"Service update failed for user {user_id} on {server.name}")
                    # Don't return False here - local update might still be valuable
                
            except Exception as e:
                current_app.logger.error(f"Error updating user access on {server.name}: {e}")
                # Continue with local update even if service update fails
        
        if changes_made:
            try:
                db.session.commit()
                log_event(
                    EventType.MUM_USER_LIBRARIES_EDITED,
                    f"Updated access for '{user.get_display_name()}' on {server.name}",
                    user_id=user_id,
                    admin_id=admin_id
                )
                return True
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error saving user access changes: {e}")
                return False
        
        return True
    
    @staticmethod
    def remove_user_from_server(user_id: int, server_id: int, admin_id: int = None) -> bool:
        """Remove user's access from a specific server"""
        user = User.query.get(user_id)
        server = MediaServiceManager.get_server_by_id(server_id)
        
        if not user or not server:
            return False
        
        # Get access record
        access = UserMediaAccess.query.filter_by(
            user_id=user_id,
            server_id=server_id
        ).first()
        
        if not access:
            return True  # Already removed
        
        # Remove from service first
        service = MediaServiceFactory.create_service_from_db(server)
        if service and access.external_user_id:
            try:
                service.delete_user(access.external_user_id)
            except Exception as e:
                current_app.logger.error(f"Error removing user from {server.name}: {e}")
                # Continue with local removal even if service removal fails
        
        # Remove from local database
        try:
            db.session.delete(access)
            db.session.commit()
            
            log_event(
                EventType.PLEX_USER_REMOVED,
                f"Removed '{user.get_display_name()}' from {server.name}",
                user_id=user_id,
                admin_id=admin_id
            )
            
            return True
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error removing user access from database: {e}")
            return False
    
    @staticmethod
    def delete_user_completely(user_id: int, admin_id: int = None) -> bool:
        """Delete user from all services and MUM database"""
        user = User.query.get(user_id)
        if not user:
            return False
        
        username = user.get_display_name()
        
        # Remove from all services
        for access in user.media_accesses:
            try:
                UnifiedUserService.remove_user_from_server(user_id, access.server_id, admin_id)
            except Exception as e:
                current_app.logger.error(f"Error removing user from {access.server.name}: {e}")
        
        # Delete user from MUM database
        try:
            db.session.delete(user)
            db.session.commit()
            
            log_event(
                EventType.MUM_USER_DELETED_FROM_MUM,
                f"Deleted user '{username}' completely from MUM",
                admin_id=admin_id
            )
            
            return True
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting user from database: {e}")
            return False
    
    @staticmethod
    def get_users_with_pagination(page: int = 1, per_page: int = 12, 
                                search: str = None) -> Dict[str, Any]:
        """Get users with pagination and search"""
        query = User.query
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                db.or_(
                    User.primary_username.ilike(search_term),
                    User.primary_email.ilike(search_term),
                    User.plex_username.ilike(search_term),
                    User.plex_email.ilike(search_term)
                )
            )
        
        pagination = query.paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )
        
        # Add access info for each user
        users_with_access = []
        for user in pagination.items:
            access_count = user.media_accesses.filter_by(is_active=True).count()
            users_with_access.append({
                'user': user,
                'active_servers': access_count,
                'last_activity': user.last_activity_at or user.last_streamed_at
            })
        
        return {
            'users': users_with_access,
            'pagination': pagination,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page,
            'per_page': per_page,
            'has_prev': pagination.has_prev,
            'has_next': pagination.has_next
        }
