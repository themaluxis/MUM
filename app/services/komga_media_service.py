# File: app/services/komga_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
import base64
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType
from app.utils.timeout_helper import get_api_timeout

class KomgaMediaService(BaseMediaService):
    """Komga implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.KOMGA
    
    def _get_headers(self):
        """Get headers for Komga API requests"""
        if self.username and self.password:
            # Komga uses basic auth
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
        elif self.api_key:
            return {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
        else:
            return {'Content-Type': 'application/json'}
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to Komga server"""
        url = f"{self.url.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
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
        """Test connection to Komga server"""
        try:
            # Try to get server info
            self._make_request('users/me')
            return True, "Connected to Komga successfully"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Komga libraries"""
        try:
            libraries = self._make_request('libraries')
            result = []
            
            for lib in libraries.get('content', []):
                result.append({
                    'id': lib.get('id', ''),
                    'name': lib.get('name', 'Unknown'),
                    'type': 'comic',  # Komga is primarily for comics/manga
                    'item_count': lib.get('seriesCount', 0),
                    'external_id': lib.get('id', '')
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Komga users"""
        try:
            users = self._make_request('users')
            result = []
            
            for user in users.get('content', []):
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # Get user's library access
                try:
                    shared_libs = self._make_request(f'users/{user_id}/shared-libraries')
                    library_ids = [lib.get('id') for lib in shared_libs.get('content', [])]
                except:
                    library_ids = []
                
                result.append({
                    'id': user_id,
                    'uuid': user_id,
                    'username': user.get('email', 'Unknown'),  # Komga uses email as username
                    'email': user.get('email'),
                    'thumb': None,  # Komga doesn't provide avatars
                    'is_home_user': False,
                    'library_ids': library_ids,
                    'is_admin': 'ADMIN' in user.get('roles', [])
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new Komga user"""
        try:
            user_data = {
                'email': email or username,
                'password': password or 'changeme123',
                'roles': ['USER']
            }
            
            result = self._make_request('users', method='POST', data=user_data)
            user_id = result.get('id')
            
            # Set library access if specified
            library_ids = kwargs.get('library_ids', [])
            if library_ids and user_id:
                for lib_id in library_ids:
                    self._make_request(f'users/{user_id}/shared-libraries/{lib_id}', method='POST')
            
            return {
                'success': True,
                'user_id': user_id,
                'username': username,
                'email': email
            }
        except Exception as e:
            self.log_error(f"Error creating user: {e}")
            raise
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update Komga user's library access"""
        try:
            if library_ids is not None:
                # Get current shared libraries
                current_libs = self._make_request(f'users/{user_id}/shared-libraries')
                current_lib_ids = [lib.get('id') for lib in current_libs.get('content', [])]
                
                # Remove libraries not in new list
                for lib_id in current_lib_ids:
                    if lib_id not in library_ids:
                        self._make_request(f'users/{user_id}/shared-libraries/{lib_id}', method='DELETE')
                
                # Add new libraries
                for lib_id in library_ids:
                    if lib_id not in current_lib_ids:
                        self._make_request(f'users/{user_id}/shared-libraries/{lib_id}', method='POST')
            
            return True
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete Komga user"""
        try:
            self._make_request(f'users/{user_id}', method='DELETE')
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active Komga sessions - Komga doesn't have real-time sessions"""
        # Komga doesn't have active session tracking like media servers
        # We could potentially get recent reading progress instead
        return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate session - Not applicable for Komga"""
        return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Komga server information"""
        try:
            # Komga doesn't have a specific version endpoint, so we'll use user info
            self._make_request('users/me')
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
                'version': 'Unknown'
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
        """Get active Komga sessions formatted for display"""
        # Komga doesn't have real-time sessions like media servers
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