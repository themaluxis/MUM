# File: app/services/kavita_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
import time
import hashlib
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

# Global cache for JWT tokens across all Kavita instances
_JWT_TOKEN_CACHE = {}

class KavitaMediaService(BaseMediaService):
    """Kavita implementation of BaseMediaService"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._jwt_token = None
        # Create a unique cache key for this server instance
        self._cache_key = self._generate_cache_key()
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.KAVITA
    
    def _generate_cache_key(self) -> str:
        """Generate a unique cache key based on server URL and API key."""
        if not self.url or not self.api_key:
            return ""
        
        # Create a hash of the URL and API key for the cache key
        cache_string = f"{self.url}:{self.api_key}"
        return hashlib.md5(cache_string.encode()).hexdigest()
    
    def _get_cached_token(self) -> str:
        """Get cached JWT token if it's still valid."""
        if not self._cache_key:
            return None
        
        cached_data = _JWT_TOKEN_CACHE.get(self._cache_key)
        if not cached_data:
            return None
        
        jwt_token, expiry_time = cached_data
        
        # Check if token has expired (with 30 second buffer)
        if time.time() >= expiry_time - 30:
            # Token expired, remove from cache
            _JWT_TOKEN_CACHE.pop(self._cache_key, None)
            return None
        
        return jwt_token
    
    def _cache_token(self, jwt_token: str, expires_in: int = 3600) -> None:
        """Cache the JWT token with expiry time."""
        if not self._cache_key or not jwt_token:
            return
        
        expiry_time = time.time() + expires_in
        _JWT_TOKEN_CACHE[self._cache_key] = (jwt_token, expiry_time)
        self.log_info(f"Cached Kavita JWT token for {expires_in} seconds")
    
    def _authenticate_with_api_key(self):
        """Authenticate with Kavita using API key to get JWT token"""
        # Try to get cached token first
        cached_token = self._get_cached_token()
        if cached_token:
            return cached_token
            
        url = f"{self.url.rstrip('/')}/api/Plugin/authenticate"
        headers = {
            'accept': 'text/plain'
        }
        params = {
            'apiKey': self.api_key,
            'pluginName': 'MUM'  # Using MUM as the plugin name
        }
        
        try:
            self.log_info(f"Authenticating with Kavita API (cache miss): {url}")
            response = requests.post(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            
            # Try to parse as JSON first (Kavita returns JSON with token field)
            try:
                response_data = response.json()
                jwt_token = response_data.get('token', '').strip()
            except ValueError:
                # Fallback to plain text if not JSON
                jwt_token = response.text.strip()
            
            if jwt_token:
                # Cache the token for 1 hour (JWT tokens typically expire after some time)
                self._cache_token(jwt_token, expires_in=3600)
                self.log_info(f"Successfully authenticated with Kavita API key for plugin 'MUM'")
                return jwt_token
            else:
                self.log_error("No JWT token returned from Kavita authentication")
                raise ValueError("Empty JWT token received")
            
        except Exception as e:
            self.log_error(f"Failed to authenticate with API key: {e}")
            raise
    
    def _get_headers(self):
        """Get headers for Kavita API requests"""
        # First authenticate to get JWT token
        jwt_token = self._authenticate_with_api_key()
        
        return {
            'Authorization': f'Bearer {jwt_token}',
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
            
            # Debug logging for Health, Users, and server info endpoints
            server_info_endpoints = ['health', 'users', 'server/server-info', 'server/server-info-slim', 
                                   'server-info', 'server', 'Server/server-info', 'Server/server-info-slim']
            if endpoint.lower() in [ep.lower() for ep in server_info_endpoints]:
                self.log_info(f"{endpoint} endpoint request headers: {headers}")
                self.log_info(f"{endpoint} endpoint response status: {response.status_code}")
                self.log_info(f"{endpoint} endpoint response headers: {dict(response.headers)}")
                if endpoint.lower() != 'health':  # Don't log content for health, it's just "Ok"
                    self.log_info(f"{endpoint} endpoint response content: '{response.text[:200]}...'")
                self.log_info(f"{endpoint} endpoint response content length: {len(response.content)}")
            
            # Handle empty responses or non-JSON responses
            if not response.content:
                return {}
            
            # For Health endpoint, it might return plain text instead of JSON
            if endpoint.lower() == 'health':
                # Try to parse as JSON first, if it fails, treat as successful if status is 200
                try:
                    return response.json()
                except ValueError:
                    # Health endpoint might return plain text like "OK" or empty response
                    if response.status_code == 200:
                        return {"status": "healthy", "raw_response": response.text}
                    else:
                        raise ValueError(f"Health endpoint returned non-200 status: {response.status_code}")
            # For Server/server-info endpoint, handle potential non-JSON responses
            elif endpoint.lower() == 'server/server-info':
                try:
                    return response.json()
                except ValueError:
                    # If JSON parsing fails, return the raw response for debugging
                    self.log_warning(f"Server/server-info endpoint returned non-JSON response: '{response.text[:200]}...'")
                    return {
                        "error": "Non-JSON response from server-info endpoint",
                        "raw_response": response.text,
                        "content_type": response.headers.get('Content-Type', 'Unknown'),
                        "status_code": response.status_code
                    }
            else:
                return response.json()
                
        except requests.exceptions.RequestException as e:
            self.log_error(f"API request failed: {e}")
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Kavita server"""
        try:
            health = self._make_request('Health')
            # Health endpoint typically returns simple status, try to get version from another endpoint if needed
            return True, f"Connected to Kavita server"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Kavita libraries"""
        try:
            libraries = self._make_request('Library/libraries')
            result = []
            
            for lib in libraries:
                # Handle library type - it might be an integer or string
                lib_type = lib.get('type', 'book')
                if isinstance(lib_type, int):
                    # Kavita uses integer types: 0=Manga, 1=Comic, 2=Book
                    type_map = {0: 'manga', 1: 'comic', 2: 'book'}
                    lib_type = type_map.get(lib_type, 'book')
                else:
                    lib_type = str(lib_type).lower()
                
                result.append({
                    'id': str(lib.get('id', '')),
                    'name': lib.get('name', 'Unknown'),
                    'type': lib_type,
                    'item_count': lib.get('seriesCount', 0),
                    'external_id': str(lib.get('id', '')),
                    'raw_data': lib  # Store raw data for debugging
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Kavita users"""
        try:
            # Use the correct endpoint that your friend's code uses
            users = self._make_request('Users')
            result = []
            
            if not isinstance(users, list):
                self.log_error("Unexpected response format from /api/Users – expected list")
                return []
            
            for user in users:
                if not isinstance(user, dict):
                    continue
                    
                user_id = user.get('id')
                if not user_id:
                    continue
                
                # Skip the owner account (ID 1) - this is the admin account and shouldn't be synced as a regular user
                if user_id == 1:
                    self.log_info(f"Skipping Kavita owner account (ID: {user_id}, username: {user.get('username', 'Unknown')})")
                    continue
                
                # Extract library access from user data
                library_ids = []
                library_names = []
                
                if 'libraries' in user and isinstance(user['libraries'], list):
                    for lib in user['libraries']:
                        if isinstance(lib, dict) and 'id' in lib:
                            # For Kavita, create unique IDs by combining ID and name since IDs can be duplicated
                            lib_id = f"{lib['id']}_{lib.get('name', 'Unknown')}"
                            lib_name = lib.get('name', f"Library {lib['id']}")
                            library_ids.append(lib_id)
                            library_names.append(lib_name)
                
                # Extract join date from created field
                join_date = None
                if 'created' in user:
                    try:
                        from datetime import datetime
                        # Parse the ISO format datetime string
                        join_date_str = user['created']
                        if join_date_str:
                            # Remove timezone info and parse
                            if '.' in join_date_str:
                                join_date_str = join_date_str.split('.')[0]  # Remove microseconds
                            join_date = datetime.fromisoformat(join_date_str.replace('Z', ''))
                    except (ValueError, TypeError) as e:
                        self.log_warning(f"Could not parse created date '{user.get('created')}' for user {user.get('username')}: {e}")
                
                result.append({
                    'id': str(user_id),
                    'uuid': str(user_id),
                    'username': user.get('username') or user.get('userName', 'Unknown'),
                    'email': user.get('email', ''),
                    'thumb': None,  # Kavita doesn't provide avatars
                    'is_home_user': False,
                    'library_ids': library_ids,
                    'library_names': library_names,  # Include library names from Kavita
                    'join_date': join_date,  # Include join date from created field
                    'is_admin': user.get('isAdmin', False),
                    'raw_data': user  # Store raw data for debugging
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
        """Update Kavita user's library access using /api/Account/update"""
        try:
            if library_ids is not None:
                # First, get the current user data to preserve other settings
                users = self._make_request('Users')
                target_user = None
                for user in users:
                    if str(user.get('id')) == str(user_id):
                        target_user = user
                        break
                
                if not target_user:
                    self.log_error(f"User with ID {user_id} not found")
                    return False
                
                # Extract library IDs from compound format (e.g., "2_Books" -> 2)
                numeric_library_ids = []
                for lib_id in library_ids:
                    if '_' in str(lib_id):
                        # Extract numeric ID from compound format
                        numeric_id = str(lib_id).split('_')[0]
                        try:
                            numeric_library_ids.append(int(numeric_id))
                        except ValueError:
                            self.log_warning(f"Could not extract numeric ID from: {lib_id}")
                    else:
                        try:
                            numeric_library_ids.append(int(lib_id))
                        except ValueError:
                            self.log_warning(f"Invalid library ID format: {lib_id}")
                
                # Prepare the update payload
                update_data = {
                    "userId": int(user_id),
                    "username": target_user.get('username', ''),
                    "roles": target_user.get('roles', []),
                    "libraries": numeric_library_ids,
                    "ageRestriction": target_user.get('ageRestriction', {
                        "ageRating": 0,
                        "includeUnknowns": True
                    }),
                    "email": target_user.get('email', ''),
                    "identityProvider": target_user.get('identityProvider', 0)
                }
                
                self.log_info(f"Updating Kavita user {user_id} with library access: {numeric_library_ids}")
                self.log_info(f"Update payload: {update_data}")
                
                # Update the user
                self._make_request('Account/update', method='POST', data=update_data)
                self.log_info(f"Successfully updated library access for user {user_id}")
            
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
        """Get active Kavita sessions - Kavita doesn't have real-time session tracking"""
        return []
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate session - Not applicable for Kavita"""
        return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Kavita server information"""
        try:
            server_info = self._make_request('Server/server-info-slim')
            actual_server_name = server_info.get('installId', self.name)
            version = server_info.get('kavitaVersion', 'Unknown')
            
            return {
                'name': f"Kavita ({actual_server_name})",
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
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
        """Get active Kavita sessions formatted for display - Kavita doesn't have real-time sessions"""
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