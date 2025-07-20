# File: app/services/kavita_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

class KavitaMediaService(BaseMediaService):
    """Kavita implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.KAVITA
    
    def _get_headers(self):
        """Get headers for Kavita API requests"""
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to Kavita server"""
        url = f"{self.url.rstrip('/')}/api/{endpoint.lstrip('/')}"
        headers = self._get_headers()
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            self.log_error(f"API request failed: {e}")
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Kavita server"""
        try:
            info = self._make_request('Server/version')
            version = info.get('version', 'Unknown')
            return True, f"Connected to Kavita (v{version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Kavita libraries"""
        try:
            libraries = self._make_request('Library')
            result = []
            
            for lib in libraries:
                result.append({
                    'id': str(lib.get('id', '')),
                    'name': lib.get('name', 'Unknown'),
                    'type': lib.get('type', 'book').lower(),
                    'item_count': lib.get('seriesCount', 0),
                    'external_id': str(lib.get('id', ''))
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Kavita users"""
        try:
            users = self._make_request('Account/users')
            result = []
            
            for user in users:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # Get user's library access
                try:
                    libraries = self._make_request(f'Account/user/{user_id}/libraries')
                    library_ids = [str(lib.get('id')) for lib in libraries]
                except:
                    library_ids = []
                
                result.append({
                    'id': str(user_id),
                    'uuid': str(user_id),
                    'username': user.get('username', 'Unknown'),
                    'email': user.get('email'),
                    'thumb': None,  # Kavita doesn't provide avatars
                    'is_home_user': False,
                    'library_ids': library_ids,
                    'is_admin': user.get('roles', []).get('Admin', False)
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new Kavita user"""
        try:
            user_data = {
                'username': username,
                'email': email or '',
                'password': password or 'changeme123',
                'roles': ['Pleb']  # Default role
            }
            
            result = self._make_request('Account/register', method='POST', data=user_data)
            user_id = result.get('id')
            
            # Set library access if specified
            library_ids = kwargs.get('library_ids', [])
            if library_ids and user_id:
                for lib_id in library_ids:
                    self._make_request(f'Account/grant-library-access', method='POST', 
                                     data={'userId': user_id, 'libraryId': int(lib_id)})
            
            return {
                'success': True,
                'user_id': str(user_id),
                'username': username,
                'email': email
            }
        except Exception as e:
            self.log_error(f"Error creating user: {e}")
            raise
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update Kavita user's library access"""
        try:
            if library_ids is not None:
                # Remove all current access
                self._make_request(f'Account/revoke-all-library-access', method='POST', 
                                 data={'userId': int(user_id)})
                
                # Grant new access
                for lib_id in library_ids:
                    self._make_request(f'Account/grant-library-access', method='POST',
                                     data={'userId': int(user_id), 'libraryId': int(lib_id)})
            
            return True
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete Kavita user"""
        try:
            self._make_request(f'Account/delete-user', method='POST', 
                             data={'userId': int(user_id)})
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active Kavita sessions - Kavita doesn't have real-time sessions"""
        # Kavita doesn't have active session tracking like media servers
        # We could potentially get recent reading activity instead
        return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate session - Not applicable for Kavita"""
        return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Kavita server information"""
        try:
            info = self._make_request('Server/version')
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
                'version': info.get('version', 'Unknown')
            }
        except:
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': False,
                'version': 'Unknown'
            }