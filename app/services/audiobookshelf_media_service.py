# File: app/services/audiobookshelf_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

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
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            elif method == 'PATCH':
                response = requests.patch(url, headers=headers, json=data, timeout=10)
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
            info = self._make_request('status')
            version = info.get('serverVersion', 'Unknown')
            return True, f"Connected to AudiobookShelf (v{version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all AudiobookShelf libraries"""
        try:
            libraries = self._make_request('libraries')
            result = []
            
            for lib in libraries.get('libraries', []):
                result.append({
                    'id': lib.get('id', ''),
                    'name': lib.get('name', 'Unknown'),
                    'type': lib.get('mediaType', 'book').lower(),
                    'item_count': lib.get('stats', {}).get('totalItems', 0),
                    'external_id': lib.get('id', '')
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all AudiobookShelf users"""
        try:
            users = self._make_request('users')
            result = []
            
            for user in users.get('users', []):
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # Get user's library access
                permissions = user.get('permissions', {})
                library_ids = permissions.get('librariesAccessible', [])
                
                result.append({
                    'id': user_id,
                    'uuid': user_id,
                    'username': user.get('username', 'Unknown'),
                    'email': user.get('email'),
                    'thumb': None,  # AudiobookShelf doesn't provide avatars in user list
                    'is_home_user': False,
                    'library_ids': library_ids,
                    'is_admin': user.get('type') == 'admin'
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new AudiobookShelf user"""
        try:
            user_data = {
                'username': username,
                'email': email or '',
                'password': password or 'changeme123',
                'type': 'user',
                'isActive': True
            }
            
            # Set library access if specified
            library_ids = kwargs.get('library_ids', [])
            if library_ids:
                user_data['permissions'] = {
                    'librariesAccessible': library_ids,
                    'accessAllLibraries': False
                }
            
            result = self._make_request('users', method='POST', data=user_data)
            
            return {
                'success': True,
                'user_id': result.get('id'),
                'username': username,
                'email': email
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
            info = self._make_request('status')
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
                'version': info.get('serverVersion', 'Unknown')
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