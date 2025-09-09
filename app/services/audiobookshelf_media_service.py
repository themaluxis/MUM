# File: app/services/audiobookshelf_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType
from app.utils.timeout_helper import get_api_timeout

class AudiobookShelfMediaService(BaseMediaService):
    """AudiobookShelf implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.AUDIOBOOKSHELF
    
    def _get_headers(self):
        """Get headers for AudiobookShelf API requests"""
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to AudiobookShelf server"""
        url = f"{self.url.rstrip('/')}/api/{endpoint.lstrip('/')}"
        headers = self._get_headers()
        
        try:
            timeout = get_api_timeout()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=timeout)
            elif method == 'PATCH':
                response = requests.patch(url, headers=headers, json=data, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            self.log_error(f"API request failed: {e}")
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to AudiobookShelf server"""
        try:
            # Test authentication with /api/me endpoint
            user_info = self._make_request('me')
            username = user_info.get('username', 'Unknown')
            
            # Get server version - try different endpoints
            version = 'Unknown'
            try:
                # Try /api/status first
                status_info = self._make_request('status')
                version = status_info.get('serverVersion', status_info.get('version', 'Unknown'))
            except Exception as e:
                self.log_info(f"Status endpoint failed: {e}, trying alternatives")
                try:
                    # Try /api/ping
                    ping_info = self._make_request('ping')
                    version = ping_info.get('serverVersion', ping_info.get('version', 'Unknown'))
                except Exception as e2:
                    self.log_info(f"Ping endpoint also failed: {e2}")
                    # Don't fail the whole test if we can't get version info
            
            if version != 'Unknown':
                return True, f"Connected to AudiobookShelf (v{version}) as user '{username}'"
            else:
                return True, f"Connected to AudiobookShelf as user '{username}'"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries_raw(self) -> List[Dict[str, Any]]:
        """Get raw, unmodified library data from AudiobookShelf API"""
        try:
            # Return the raw API response without any modifications
            libraries = self._make_request('libraries')
            self.log_info(f"Retrieved raw libraries from AudiobookShelf: {type(libraries)} with {len(libraries) if isinstance(libraries, list) else 'unknown'} items")
            self.log_info(f"Raw libraries response structure: {libraries}")
            return libraries
        except Exception as e:
            self.log_error(f"Error fetching raw libraries: {e}")
            return []
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all AudiobookShelf libraries (processed for internal use)"""
        try:
            # Get raw data first
            libraries_response = self.get_libraries_raw()
            result = []
            
            # Handle different response formats
            if isinstance(libraries_response, list):
                # Direct array response
                libraries_list = libraries_response
                self.log_info(f"AudiobookShelf returned libraries as direct array with {len(libraries_list)} items")
            elif isinstance(libraries_response, dict):
                # Wrapped in object
                libraries_list = libraries_response.get('libraries', [])
                self.log_info(f"AudiobookShelf returned libraries wrapped in object with {len(libraries_list)} items")
            else:
                self.log_error(f"Unexpected libraries response format: {type(libraries_response)}")
                return []
            
            # Process the raw data for internal use
            for lib in libraries_list:
                lib_id = lib.get('id', '')
                
                # Get actual item count using /api/libraries/{id}/items?limit=0
                item_count = 0
                try:
                    if lib_id:
                        items_response = self._make_request(f'libraries/{lib_id}/items?limit=0')
                        self.log_info(f"AudioBookshelf items API response for '{lib.get('name')}': {items_response}")
                        item_count = items_response.get('total', 0)
                        self.log_info(f"AudioBookshelf library '{lib.get('name')}' has {item_count} items")
                except Exception as e:
                    self.log_info(f"Could not get item count for library '{lib.get('name')}': {e}")
                    # Fallback to stats if available
                    item_count = lib.get('stats', {}).get('totalItems', 0)
                
                result.append({
                    'id': lib_id,
                    'name': lib.get('name', 'Unknown'),
                    'type': lib.get('mediaType', 'book').lower(),
                    'item_count': item_count,
                    'external_id': lib_id
                })
            
            self.log_info(f"Processed {len(result)} libraries from AudiobookShelf")
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all AudiobookShelf users"""
        try:
            users_response = self._make_request('users')
            result = []
            
            # Handle different response formats
            if isinstance(users_response, dict) and 'users' in users_response:
                users_list = users_response.get('users', [])
            elif isinstance(users_response, list):
                users_list = users_response
            else:
                self.log_error(f"Unexpected users response format: {type(users_response)}")
                return []
            
            for user in users_list:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # Get user's library access
                permissions = user.get('permissions', {})
                library_ids = permissions.get('librariesAccessible', [])
                
                # Debug logging for raw data
                self.log_info(f"AudioBookshelf user {user.get('username', 'Unknown')} raw data keys: {list(user.keys()) if isinstance(user, dict) else 'not a dict'}")
                self.log_info(f"AudioBookshelf user {user.get('username', 'Unknown')} raw data size: {len(str(user))}")
                
                result.append({
                    'id': user_id,
                    'uuid': user_id,
                    'username': user.get('username', 'Unknown'),
                    'email': user.get('email'),
                    'thumb': None,  # AudiobookShelf doesn't provide avatars in user list
                    'is_home_user': False,
                    'library_ids': library_ids,
                    'is_admin': user.get('type') == 'admin',
                    'raw_data': user  # Store individual user's raw data (not the full response)
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new AudiobookShelf user"""
        try:
            # Required fields according to API docs
            user_data = {
                'username': username,
                'password': password or 'changeme123',
                'type': 'user',  # Required: guest, user, or admin
                'isActive': True,
                'isLocked': False
            }
            
            # Add email if provided (not required by API)
            if email:
                user_data['email'] = email
            
            # Set up permissions object with all required fields
            library_ids = kwargs.get('library_ids', [])
            user_data['permissions'] = {
                'download': True,
                'update': True,
                'delete': False,
                'upload': False,
                'accessAllLibraries': len(library_ids) == 0,  # True if no specific libraries
                'accessAllTags': True,
                'accessExplicitContent': True
            }
            
            # Set library access if specified
            if library_ids:
                user_data['librariesAccessible'] = library_ids
            else:
                user_data['librariesAccessible'] = []  # Empty array means all libraries
            
            # Optional fields with defaults
            user_data['mediaProgress'] = []
            user_data['bookmarks'] = []
            user_data['seriesHideFromContinueListening'] = []
            user_data['itemTagsAccessible'] = []  # Empty array means all tags
            
            result = self._make_request('users', method='POST', data=user_data)
            
            return {
                'success': True,
                'user_id': result.get('id'),
                'username': username,
                'email': email or ''
            }
        except Exception as e:
            self.log_error(f"Error creating user: {e}")
            raise
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update AudiobookShelf user's library access"""
        try:
            if library_ids is not None:
                update_data = {
                    'permissions': {
                        'librariesAccessible': library_ids,
                        'accessAllLibraries': len(library_ids) == 0
                    }
                }
                self._make_request(f'users/{user_id}', method='PATCH', data=update_data)
            
            return True
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete AudiobookShelf user"""
        try:
            self._make_request(f'users/{user_id}', method='DELETE')
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active AudiobookShelf sessions"""
        try:
            sessions = self._make_request('sessions')
            result = []
            
            for session in sessions.get('sessions', []):
                if not session.get('mediaPlayer'):
                    continue  # Skip inactive sessions
                
                media_player = session.get('mediaPlayer', {})
                library_item = session.get('libraryItem', {})
                media = library_item.get('media', {})
                
                result.append({
                    'session_id': session.get('id', ''),
                    'user_id': session.get('userId', ''),
                    'username': session.get('user', {}).get('username', 'Unknown'),
                    'media_title': media.get('metadata', {}).get('title', 'Unknown'),
                    'media_type': library_item.get('mediaType', 'unknown'),
                    'player': session.get('playMethod', 'Unknown'),
                    'platform': '',
                    'state': 'playing' if media_player.get('playing') else 'paused',
                    'progress_percent': round((media_player.get('currentTime', 0) / media_player.get('duration', 1)) * 100, 1),
                    'ip_address': '',
                    'is_lan': False
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching active sessions: {e}")
            return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate an AudiobookShelf session"""
        try:
            self._make_request(f'sessions/{session_id}/close', method='POST')
            return True
        except Exception as e:
            self.log_error(f"Error terminating session: {e}")
            return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get AudiobookShelf server information"""
        try:
            # Try to get server info, with fallback for missing endpoints
            version = 'Unknown'
            online = True
            
            try:
                info = self._make_request('status')
                version = info.get('serverVersion', info.get('version', 'Unknown'))
            except Exception as e:
                self.log_info(f"Status endpoint failed in get_server_info: {e}, trying alternatives")
                try:
                    ping_info = self._make_request('ping')
                    version = ping_info.get('serverVersion', ping_info.get('version', 'Unknown'))
                except Exception as e2:
                    self.log_info(f"Ping endpoint also failed in get_server_info: {e2}")
                    # Still mark as online if we can reach the server at all
                    try:
                        self._make_request('me')  # Test basic connectivity
                    except:
                        online = False
            
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': online,
                'version': version
            }
        except:
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': False,
                'version': 'Unknown'
            }

    def get_formatted_sessions(self) -> List[Dict[str, Any]]:
        """Get active AudiobookShelf sessions formatted for display"""
        # TODO: Implement AudiobookShelf session formatting
        return []

    def get_geoip_info(self, ip_address: str) -> Dict[str, Any]:
        """Get GeoIP information for a given IP address."""
        if not ip_address or ip_address in ['127.0.0.1', 'localhost']:
            return {"status": "local", "message": "This is a local address."}
        
        try:
            response = requests.get(f"http://ip-api.com/json/{ip_address}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.log_error(f"Failed to get GeoIP info for {ip_address}: {e}")
            return {"status": "error", "message": str(e)}
    
    def check_username_exists(self, username: str) -> bool:
        """Check if a username already exists in AudiobookShelf"""
        try:
            users = self.get_users()
            for user in users:
                if user.get('username', '').lower() == username.lower():
                    return True
            return False
        except Exception as e:
            self.log_error(f"Error checking username '{username}': {e}")
            return False