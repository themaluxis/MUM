"""
RomM Media Service Implementation
Provides integration with RomM (Rom Manager) servers for retro gaming content management.
"""

import requests
import json
from typing import List, Dict, Any, Optional, Tuple
from flask import current_app
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType


class RommMediaService(BaseMediaService):
    """RomM media service implementation"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.ROMM
    
    def __init__(self, server_config: Dict[str, Any]):
        super().__init__(server_config)
        self.session = requests.Session()
        self.session.timeout = 30
        self._token = None
        
    def _authenticate(self) -> bool:
        """Authenticate with RomM server and get access token"""
        try:
            if not self.username or not self.password:
                self.log_error("Username and password are required for RomM authentication")
                return False
                
            auth_url = f"{self.url.rstrip('/')}/api/token"
            
            response = self.session.post(
                auth_url,
                data={
                    "username": self.username,
                    "password": self.password
                },
                timeout=10
            )
            response.raise_for_status()
            
            auth_data = response.json()
            self._token = auth_data.get('access_token')
            
            if self._token:
                self.session.headers.update({
                    'Authorization': f'Bearer {self._token}'
                })
                self.log_info("Successfully authenticated with RomM server")
                return True
            else:
                self.log_error("No access token received from RomM server")
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_error(f"Authentication failed: {e}")
            return False
        except Exception as e:
            self.log_error(f"Unexpected error during authentication: {e}")
            return False
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to RomM server"""
        try:
            # First authenticate
            if not self._authenticate():
                return False, "Authentication failed. Check username and password."
            
            # Test authenticated request
            response = self.session.get(f"{self.url.rstrip('/')}/api/users/me")
            response.raise_for_status()
            
            user_info = response.json()
            username = user_info.get('username', self.username)
            
            return True, f"Successfully connected to RomM server as user '{username}'"
            
        except requests.exceptions.ConnectTimeout:
            return False, "Connection to RomM timed out. Check if the server is running and accessible."
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to RomM. Check the URL and network connectivity."
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return False, "Authentication failed. Check username and password."
            elif e.response.status_code == 403:
                return False, "Access denied. User may not have sufficient permissions."
            else:
                return False, f"RomM returned an error: {e.response.status_code} - {e.response.reason}"
        except requests.exceptions.Timeout:
            return False, "Request to RomM timed out. The server may be slow to respond."
        except Exception as e:
            return False, f"Unexpected error connecting to RomM: {str(e)}"
    
    def get_libraries_raw(self) -> List[Dict[str, Any]]:
        """Get raw, unmodified platform data from RomM API"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for raw library retrieval")
                return []
            
            response = self.session.get(f"{self.url.rstrip('/')}/api/platforms")
            response.raise_for_status()
            
            # Return the raw API response without any modifications
            platforms = response.json()
            self.log_info(f"Retrieved {len(platforms)} raw platforms from RomM")
            return platforms
            
        except Exception as e:
            self.log_error(f"Error retrieving raw platforms: {e}")
            return []
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all platforms (libraries) from RomM (processed for internal use)"""
        try:
            # Get raw data first
            platforms = self.get_libraries_raw()
            libraries = []
            
            # Process the raw data for internal use
            for platform in platforms:
                libraries.append({
                    'id': str(platform.get('id', '')),
                    'name': platform.get('name', 'Unknown Platform'),
                    'slug': platform.get('slug', ''),
                    'rom_count': platform.get('rom_count', 0),
                    'external_id': str(platform.get('id', ''))  # Add external_id for compatibility
                })
            
            self.log_info(f"Processed {len(libraries)} platforms from RomM")
            return libraries
            
        except Exception as e:
            self.log_error(f"Error retrieving platforms: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all users from RomM"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for user retrieval")
                return []
            
            response = self.session.get(f"{self.url.rstrip('/')}/api/users")
            response.raise_for_status()
            
            users_data = response.json()
            users = []
            
            for user in users_data:
                users.append({
                    'id': str(user.get('id', '')),
                    'username': user.get('username', ''),
                    'email': user.get('email', ''),
                    'enabled': user.get('enabled', True),
                    'role': user.get('role', 'viewer'),
                    'created_at': user.get('created_at', ''),
                    'last_active_at': user.get('last_active_at', '')
                })
            
            self.log_info(f"Retrieved {len(users)} users from RomM")
            return users
            
        except Exception as e:
            self.log_error(f"Error retrieving users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create a new user in RomM"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for user creation")
                return {}
            
            user_data = {
                'username': username,
                'password': password or 'defaultpassword123',  # RomM requires a password
                'role': kwargs.get('role', 'viewer'),
                'enabled': kwargs.get('enabled', True)
            }
            
            if email:
                user_data['email'] = email
            
            response = self.session.post(
                f"{self.url.rstrip('/')}/api/users",
                json=user_data
            )
            response.raise_for_status()
            
            created_user = response.json()
            self.log_info(f"Created user '{username}' in RomM")
            
            return {
                'id': str(created_user.get('id', '')),
                'username': created_user.get('username', ''),
                'email': created_user.get('email', ''),
                'role': created_user.get('role', ''),
                'enabled': created_user.get('enabled', True)
            }
            
        except Exception as e:
            self.log_error(f"Error creating user '{username}': {e}")
            return {}
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update user's platform access in RomM"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for user access update")
                return False
            
            # RomM doesn't have granular library access control like other services
            # Users typically have access to all platforms based on their role
            # This method could be used to update user role or other permissions
            
            user_data = {}
            if 'role' in kwargs:
                user_data['role'] = kwargs['role']
            if 'enabled' in kwargs:
                user_data['enabled'] = kwargs['enabled']
            
            if user_data:
                response = self.session.patch(
                    f"{self.url.rstrip('/')}/api/users/{user_id}",
                    json=user_data
                )
                response.raise_for_status()
                
                self.log_info(f"Updated user {user_id} access in RomM")
                return True
            
            return True  # No changes needed
            
        except Exception as e:
            self.log_error(f"Error updating user access for {user_id}: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete/remove user from RomM"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for user deletion")
                return False
            
            response = self.session.delete(f"{self.url.rstrip('/')}/api/users/{user_id}")
            response.raise_for_status()
            
            self.log_info(f"Deleted user {user_id} from RomM")
            return True
            
        except Exception as e:
            self.log_error(f"Error deleting user {user_id}: {e}")
            return False
    
    def check_username_exists(self, username: str) -> bool:
        """Check if a username already exists in Romm"""
        try:
            users = self.get_users()
            for user in users:
                if user.get('username', '').lower() == username.lower():
                    return True
            return False
        except Exception as e:
            self.log_error(f"Error checking username '{username}': {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get currently active gaming sessions from RomM"""
        try:
            if not self._authenticate():
                self.log_error("Failed to authenticate for session retrieval")
                return []
            
            # RomM doesn't have traditional "streaming sessions" like media servers
            # But we can get recent activity or currently playing games
            response = self.session.get(f"{self.url.rstrip('/')}/api/stats/recent-activity")
            response.raise_for_status()
            
            activity_data = response.json()
            sessions = []
            
            # Convert recent activity to session-like format
            for activity in activity_data.get('recent_plays', []):
                sessions.append({
                    'session_id': f"romm_{activity.get('id', '')}",
                    'user_id': str(activity.get('user_id', '')),
                    'username': activity.get('username', 'Unknown'),
                    'game_title': activity.get('rom_name', 'Unknown Game'),
                    'platform': activity.get('platform_name', 'Unknown Platform'),
                    'started_at': activity.get('played_at', ''),
                    'state': 'playing' if activity.get('is_active', False) else 'recent'
                })
            
            return sessions
            
        except Exception as e:
            self.log_error(f"Error retrieving active sessions: {e}")
            return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate an active gaming session"""
        try:
            # RomM doesn't support terminating sessions remotely
            # This is a limitation of the platform
            self.log_warning(f"Session termination not supported by RomM for session {session_id}")
            return False
            
        except Exception as e:
            self.log_error(f"Error terminating session {session_id}: {e}")
            return False
    
    def get_formatted_sessions(self) -> List[Dict[str, Any]]:
        """Get active sessions formatted for display"""
        sessions = self.get_active_sessions()
        formatted_sessions = []
        
        for session in sessions:
            formatted_sessions.append({
                'session_id': session.get('session_id', ''),
                'user_name': session.get('username', 'Unknown'),
                'user_id': session.get('user_id', ''),
                'media_title': session.get('game_title', 'Unknown Game'),
                'media_type': 'game',
                'platform': session.get('platform', 'Unknown Platform'),
                'state': session.get('state', 'unknown'),
                'started_at': session.get('started_at', ''),
                'server_name': self.name,
                'server_id': self.server_id,
                'service_type': 'romm',
                'can_terminate': False,  # RomM doesn't support session termination
                'progress_percent': 0,  # Not applicable for games
                'bandwidth': 0,  # Not applicable for games
                'location': 'Unknown'
            })
        
        return formatted_sessions
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get RomM server information"""
        try:
            if not self._authenticate():
                return {
                    'name': self.name,
                    'url': self.url,
                    'service_type': 'romm',
                    'online': False,
                    'version': 'Unknown'
                }
            
            response = self.session.get(f"{self.url.rstrip('/')}/api/heartbeat")
            response.raise_for_status()
            
            heartbeat_data = response.json()
            
            return {
                'name': self.name,
                'url': self.url,
                'service_type': 'romm',
                'online': True,
                'version': heartbeat_data.get('version', 'Unknown'),
                'platforms_count': heartbeat_data.get('platforms_count', 0),
                'roms_count': heartbeat_data.get('roms_count', 0),
                'users_count': heartbeat_data.get('users_count', 0)
            }
            
        except Exception as e:
            self.log_error(f"Error getting server info: {e}")
            return {
                'name': self.name,
                'url': self.url,
                'service_type': 'romm',
                'online': False,
                'version': 'Unknown'
            }
    
    def supports_feature(self, feature: str) -> bool:
        """Check if RomM supports a specific feature"""
        romm_features = [
            'user_management',
            'library_access',
            'downloads',  # ROMs can be downloaded
            'active_sessions'  # Can view recent activity
        ]
        
        # Features RomM doesn't support
        unsupported_features = [
            'transcoding',  # Not applicable for ROMs
            'sharing',  # No sharing mechanism
            'session_termination'  # Can't terminate remote sessions
        ]
        
        if feature in unsupported_features:
            return False
        
        return feature in romm_features
    
    def get_geoip_info(self, ip_address: str) -> Dict[str, Any]:
        """Get GeoIP information for a given IP address"""
        # Use the base class implementation
        return super().get_geoip_info(ip_address)