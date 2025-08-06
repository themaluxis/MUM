# File: app/services/romm_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

class RomMMediaService(BaseMediaService):
    """RomM implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.ROMM
    
    def _get_headers(self):
        """Get headers for RomM API requests"""
        if self.username and self.password:
            # RomM might use session-based auth, but we'll try basic auth first
            import base64
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
        """Make API request to RomM server"""
        url = f"{self.url.rstrip('/')}/api/{endpoint.lstrip('/')}"
        headers = self._get_headers()
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, json=data, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            self.log_error(f"API request failed: {e}")
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to RomM server"""
        try:
            # Try to get platforms (which should be available)
            platforms = self._make_request('platforms')
            return True, f"Connected to RomM successfully"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all RomM platforms (equivalent to libraries)"""
        try:
            platforms = self._make_request('platforms')
            result = []
            
            for platform in platforms:
                result.append({
                    'id': str(platform.get('id', '')),
                    'name': platform.get('name', 'Unknown'),
                    'type': 'game',  # RomM is for games
                    'item_count': platform.get('rom_count', 0),
                    'external_id': str(platform.get('id', ''))
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching platforms: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all RomM users"""
        try:
            users = self._make_request('users')
            result = []
            
            for user in users:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # RomM might not have granular library access control
                # We'll assume all users have access to all platforms for now
                result.append({
                    'id': str(user_id),
                    'uuid': str(user_id),
                    'username': user.get('username', 'Unknown'),
                    'email': user.get('email'),
                    'thumb': None,  # RomM doesn't provide avatars
                    'is_home_user': False,
                    'library_ids': [],  # Will be populated if RomM supports this
                    'is_admin': user.get('role') == 'admin'
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new RomM user"""
        try:
            user_data = {
                'username': username,
                'email': email or '',
                'password': password or 'changeme123',
                'role': 'viewer'  # Default role
            }
            
            result = self._make_request('users', method='POST', data=user_data)
            
            return {
                'success': True,
                'user_id': str(result.get('id')),
                'username': username,
                'email': email
            }
        except Exception as e:
            self.log_error(f"Error creating user: {e}")
            raise
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update RomM user's access - RomM might not support granular library access"""
        try:
            # RomM might not have granular library access control
            # This would depend on the specific RomM API implementation
            self.log_info(f"Library access update requested for user {user_id}, but RomM might not support granular access control")
            return True
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete RomM user"""
        try:
            self._make_request(f'users/{user_id}', method='DELETE')
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active RomM sessions - RomM doesn't have real-time sessions"""
        # RomM doesn't have active session tracking like media servers
        # Games are typically downloaded and played locally
        return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate session - Not applicable for RomM"""
        return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get RomM server information"""
        try:
            # Try to get some basic info
            self._make_request('platforms')
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
        """Get active RomM sessions formatted for display"""
        # RomM doesn't have real-time sessions like media servers
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