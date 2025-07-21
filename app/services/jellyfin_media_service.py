# File: app/services/jellyfin_media_service.py
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse
import requests
from jellyfin_apiclient_python import JellyfinClient
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

class JellyfinMediaService(BaseMediaService):
    """Jellyfin implementation of BaseMediaService using official jellyfin-apiclient-python"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = None
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.JELLYFIN
    
    def _get_client(self) -> JellyfinClient:
        """Get authenticated Jellyfin client"""
        if self._client is None:
            self.log_info("Initializing new Jellyfin client")
            self._client = JellyfinClient()
            
            # Parse server URL
            parsed_url = urlparse(self.url)
            server_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            self.log_info(f"Connecting to Jellyfin server: {server_url}")
            
            # Configure client
            self._client.config.app('MUM', '1.0.0', 'MUM Media Manager', 'unique-device-id')
            self._client.config.data['auth.ssl'] = parsed_url.scheme == 'https'
            self.log_info(f"Client configured - SSL: {parsed_url.scheme == 'https'}")
            
            # Authenticate with API key
            try:
                self.log_info("Attempting to connect to Jellyfin server...")
                self._client.auth.connect_to_address(server_url)
                self.log_info("Connection established, attempting authentication...")
                
                # Try different authentication methods based on the library version
                if hasattr(self._client.auth, 'login_manual'):
                    self.log_info("Using login_manual method")
                    self._client.auth.login_manual(server_url, self.api_key)
                elif hasattr(self._client.auth, 'authenticate_by_name'):
                    self.log_info("Using authenticate_by_name method with API key")
                    # For API key authentication, we need to set it directly
                    self._client.config.data['auth.token'] = self.api_key
                    self._client.auth.authenticate_by_name('', '', save_credentials=False)
                else:
                    self.log_info("Setting API token directly in client configuration")
                    # Direct token setting for newer versions
                    self._client.config.data['auth.token'] = self.api_key
                    
                self.log_info("Authentication successful")
            except Exception as auth_error:
                self.log_error(f"Authentication failed: {auth_error}")
                raise
            
        return self._client
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to Jellyfin server using official client or fallback to requests"""
        self.log_info(f"Making {method} request to endpoint: {endpoint}")
        
        try:
            client = self._get_client()
            
            # Use the official client's HTTP session for consistency
            if hasattr(client, 'http') and hasattr(client.http, 'session') and client.http.session is not None:
                self.log_info("Using official client's HTTP session")
                session = client.http.session
                # Ensure the session has the API key header
                if 'X-Emby-Token' not in session.headers:
                    session.headers['X-Emby-Token'] = self.api_key
                    self.log_info("Added API key to official client session")
            else:
                # Fallback to requests if client structure is different or session is None
                self.log_info("Falling back to requests session with manual headers (client session is None or unavailable)")
                session = requests.Session()
                session.headers.update({
                    'X-Emby-Token': self.api_key,
                    'Content-Type': 'application/json'
                })
            
            url = f"{self.url.rstrip('/')}/{endpoint.lstrip('/')}"
            self.log_info(f"Full request URL: {url}")
            
            # Log headers (but mask the API key for security)
            headers_to_log = dict(session.headers)
            if 'X-Emby-Token' in headers_to_log:
                headers_to_log['X-Emby-Token'] = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
            self.log_info(f"Request headers: {headers_to_log}")
            
            if method == 'GET':
                response = session.get(url, timeout=10)
            elif method == 'POST':
                self.log_info(f"POST data: {data}")
                response = session.post(url, json=data, timeout=10)
            elif method == 'DELETE':
                response = session.delete(url, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            self.log_info(f"Response status: {response.status_code}")
            self.log_info(f"Response headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            response_data = response.json() if response.content else {}
            self.log_info(f"Response data type: {type(response_data)}, length: {len(response_data) if isinstance(response_data, (list, dict)) else 'N/A'}")
            
            return response_data
            
        except requests.exceptions.HTTPError as http_err:
            self.log_error(f"HTTP error for {method} {endpoint}: {http_err}")
            self.log_error(f"Response status: {http_err.response.status_code}")
            self.log_error(f"Response text: {http_err.response.text}")
            raise
        except requests.exceptions.RequestException as req_err:
            self.log_error(f"Request error for {method} {endpoint}: {req_err}")
            raise
        except Exception as e:
            self.log_error(f"API request failed for {method} {endpoint}: {e}", exc_info=True)
            raise
    
    def _make_request_direct(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make direct API request to Jellyfin server without using the official client"""
        self.log_info(f"Making direct {method} request to endpoint: {endpoint}")
        
        try:
            url = f"{self.url.rstrip('/')}/{endpoint.lstrip('/')}"
            headers = {
                'X-Emby-Token': self.api_key,
                'Content-Type': 'application/json'
            }
            
            self.log_info(f"Direct request URL: {url}")
            headers_to_log = dict(headers)
            headers_to_log['X-Emby-Token'] = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
            self.log_info(f"Direct request headers: {headers_to_log}")
            
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                self.log_info(f"Direct POST data: {data}")
                response = requests.post(url, headers=headers, json=data, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            self.log_info(f"Direct response status: {response.status_code}")
            self.log_info(f"Direct response headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            response_data = response.json() if response.content else {}
            self.log_info(f"Direct response data type: {type(response_data)}, length: {len(response_data) if isinstance(response_data, (list, dict)) else 'N/A'}")
            
            return response_data
            
        except requests.exceptions.HTTPError as http_err:
            self.log_error(f"Direct HTTP error for {method} {endpoint}: {http_err}")
            self.log_error(f"Direct response status: {http_err.response.status_code}")
            self.log_error(f"Direct response text: {http_err.response.text}")
            raise
        except requests.exceptions.RequestException as req_err:
            self.log_error(f"Direct request error for {method} {endpoint}: {req_err}")
            raise
        except Exception as e:
            self.log_error(f"Direct API request failed for {method} {endpoint}: {e}", exc_info=True)
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Jellyfin server"""
        try:
            # Use the official client's system info method if available
            client = self._get_client()
            if hasattr(client, 'jellyfin') and hasattr(client.jellyfin, 'get_system_info'):
                info = client.jellyfin.get_system_info()
            else:
                # Fallback to direct API call
                info = self._make_request('System/Info')
            
            server_name = info.get('ServerName', 'Jellyfin Server')
            version = info.get('Version', 'Unknown')
            return True, f"Connected to {server_name} (v{version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Jellyfin libraries"""
        self.log_info("Starting get_libraries() - fetching libraries from Jellyfin server")
        
        try:
            libraries = self._make_request('Library/VirtualFolders')
            self.log_info(f"Raw libraries response: {libraries}")
            
            result = []
            
            for i, lib in enumerate(libraries):
                self.log_info(f"Processing library {i+1}/{len(libraries)}: {lib}")
                self.log_info(f"Library keys: {list(lib.keys()) if isinstance(lib, dict) else 'Not a dict'}")
                
                # Extract library information
                lib_name = lib.get('Name', 'Unknown')
                lib_id = lib.get('ItemId')  # This might be None for VirtualFolders
                collection_type = lib.get('CollectionType', 'mixed')
                
                # For VirtualFolders, we might need to get the actual library ID differently
                # Let's check what fields are available
                self.log_info(f"Library '{lib_name}': ItemId='{lib_id}', CollectionType='{collection_type}'")
                
                # If no ItemId, try to use the Name as a fallback identifier
                external_id = lib_id if lib_id else lib_name
                
                library_data = {
                    'id': external_id,
                    'name': lib_name,
                    'type': collection_type.lower() if collection_type else 'mixed',
                    'item_count': 0,  # Jellyfin VirtualFolders doesn't provide this
                    'external_id': external_id
                }
                
                self.log_info(f"Processed library data: {library_data}")
                result.append(library_data)
            
            self.log_info(f"Successfully processed {len(result)} libraries from Jellyfin")
            return result
            
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}", exc_info=True)
            return []
    
    def _get_library_id_to_name_mapping(self) -> Dict[str, str]:
        """Get a mapping of library IDs to library names"""
        self.log_info("Creating library ID to name mapping")
        
        try:
            libraries = self.get_libraries()
            mapping = {}
            
            for lib in libraries:
                lib_id = lib.get('external_id')
                lib_name = lib.get('name')
                if lib_id and lib_name:
                    mapping[lib_id] = lib_name
                    self.log_info(f"Library mapping: '{lib_id}' -> '{lib_name}'")
            
            self.log_info(f"Created library mapping with {len(mapping)} entries: {mapping}")
            return mapping
            
        except Exception as e:
            self.log_error(f"Error creating library mapping: {e}")
            return {}
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Jellyfin users"""
        self.log_info("Starting get_users() - fetching users from Jellyfin server")
        
        try:
            # Get library mapping for resolving library IDs to names
            library_mapping = self._get_library_id_to_name_mapping()
            
            # Try to use the official client first
            try:
                client = self._get_client()
                self.log_info("Jellyfin client initialized successfully")
                
                # Try to use official client methods if available
                if hasattr(client, 'jellyfin') and hasattr(client.jellyfin, 'get_users'):
                    self.log_info("Using official client get_users() method")
                    users = client.jellyfin.get_users()
                else:
                    self.log_info("Falling back to direct API call for Users endpoint")
                    users = self._make_request('Users')
                    
            except Exception as client_error:
                self.log_warning(f"Official client failed: {client_error}")
                self.log_info("Falling back to direct HTTP requests without official client")
                users = self._make_request_direct('Users')
            
            self.log_info(f"Raw API response received: {len(users) if users else 0} users found")
            if users:
                self.log_info(f"Raw users data: {users}")
            else:
                self.log_warning("No users returned from Jellyfin API - this might indicate an authentication or permission issue")
            
            result = []
            
            for i, user in enumerate(users):
                self.log_info(f"Processing user {i+1}/{len(users)}")
                self.log_info(f"Raw user data keys: {list(user.keys()) if isinstance(user, dict) else 'Not a dict'}")
                self.log_info(f"Full raw user data: {user}")
                
                user_id = user.get('Id')
                username = user.get('Name')
                self.log_info(f"Extracted user_id: '{user_id}' (type: {type(user_id)})")
                self.log_info(f"Extracted username: '{username}' (type: {type(username)})")
                
                if not user_id:
                    self.log_warning(f"Skipping user - no ID found. user_id='{user_id}', username='{username}'")
                    self.log_warning(f"Full user data for debugging: {user}")
                    continue
                
                if not username:
                    self.log_warning(f"User has ID '{user_id}' but no Name field!")
                    self.log_warning(f"Available user fields: {list(user.keys())}")
                    username = f"User_{user_id[:8]}"  # Fallback username
                    self.log_info(f"Using fallback username: '{username}'")
                
                self.log_info(f"Processing user '{username}' (ID: {user_id})")
                
                # Get user's library access from the Policy object in the user data
                policy = user.get('Policy', {})
                self.log_info(f"Policy data for user '{username}': {policy}")
                self.log_info(f"Policy type: {type(policy)}, Policy keys: {list(policy.keys()) if isinstance(policy, dict) else 'Not a dict'}")
                
                library_ids = []
                
                if policy:
                    enable_all_folders = policy.get('EnableAllFolders', False)
                    enabled_folders = policy.get('EnabledFolders', [])
                    is_admin = policy.get('IsAdministrator', False)
                    is_disabled = policy.get('IsDisabled', False)
                    is_hidden = policy.get('IsHidden', False)
                    
                    self.log_info(f"Policy details for '{username}': EnableAllFolders={enable_all_folders}, EnabledFolders={enabled_folders}, IsAdmin={is_admin}, IsDisabled={is_disabled}, IsHidden={is_hidden}")
                    
                    # Check if user has access to all folders or specific folders
                    if enable_all_folders:
                        self.log_info(f"User '{username}' has access to ALL libraries (EnableAllFolders=true)")
                        # We could fetch all library IDs here, but for now we'll indicate full access
                        library_ids = ['*']  # Special indicator for full access
                    else:
                        library_ids = enabled_folders
                        self.log_info(f"User '{username}' has access to {len(library_ids)} specific libraries: {library_ids}")
                        
                        # Log library names for debugging
                        if library_ids and library_mapping:
                            library_names = []
                            for lib_id in library_ids:
                                lib_name = library_mapping.get(lib_id, f"Unknown Library (ID: {lib_id})")
                                library_names.append(lib_name)
                                self.log_info(f"  Library ID '{lib_id}' -> '{lib_name}'")
                            self.log_info(f"User '{username}' library names: {library_names}")
                        else:
                            self.log_info(f"No library mapping available or no specific libraries for user '{username}'")
                else:
                    self.log_warning(f"No policy found for user '{username}' in user data")
                    library_ids = []
                
                # Extract all user fields with debugging
                email = user.get('Email')
                server_id = user.get('ServerId')
                server_name = user.get('ServerName')
                primary_image_tag = user.get('PrimaryImageTag')
                has_password = user.get('HasPassword')
                last_login_date = user.get('LastLoginDate')
                last_activity_date = user.get('LastActivityDate')
                
                self.log_info(f"Additional user fields for '{username}':")
                self.log_info(f"  Email: '{email}'")
                self.log_info(f"  ServerId: '{server_id}'")
                self.log_info(f"  ServerName: '{server_name}'")
                self.log_info(f"  PrimaryImageTag: '{primary_image_tag}'")
                self.log_info(f"  HasPassword: {has_password}")
                self.log_info(f"  LastLoginDate: '{last_login_date}'")
                self.log_info(f"  LastActivityDate: '{last_activity_date}'")
                
                user_data = {
                    'id': user_id,
                    'uuid': user_id,  # Jellyfin uses GUID as ID
                    'username': username,
                    'email': email,
                    'thumb': None,  # Jellyfin doesn't provide avatar URLs in user list
                    'is_home_user': False,  # Jellyfin doesn't have this concept
                    'library_ids': library_ids,
                    'is_admin': policy.get('IsAdministrator', False),
                    'is_disabled': policy.get('IsDisabled', False),
                    'is_hidden': policy.get('IsHidden', False),
                    'raw_data': user  # Store the complete raw user data from Jellyfin API
                }
                
                self.log_info(f"Final processed user data for '{username}': {user_data}")
                result.append(user_data)
            
            self.log_info(f"Successfully processed {len(result)} users from Jellyfin")
            return result
            
        except Exception as e:
            self.log_error(f"Error fetching users: {e}", exc_info=True)
            self.log_error(f"Exception details - Type: {type(e).__name__}, Args: {e.args}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new Jellyfin user"""
        try:
            user_data = {
                'Name': username,
                'Email': email or '',
                'Password': password or ''
            }
            
            result = self._make_request('Users/New', method='POST', data=user_data)
            user_id = result.get('Id')
            
            # Set library access if specified
            library_ids = kwargs.get('library_ids', [])
            if library_ids and user_id:
                policy_data = {
                    'EnabledFolders': library_ids,
                    'EnableAllFolders': False
                }
                self._make_request(f'Users/{user_id}/Policy', method='POST', data=policy_data)
            
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
        """Update Jellyfin user's library access"""
        try:
            if library_ids is not None:
                policy_data = {
                    'EnabledFolders': library_ids,
                    'EnableAllFolders': len(library_ids) == 0
                }
                self._make_request(f'Users/{user_id}/Policy', method='POST', data=policy_data)
            
            return True
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete Jellyfin user"""
        try:
            self._make_request(f'Users/{user_id}', method='DELETE')
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active Jellyfin sessions"""
        try:
            # Use direct API call to Sessions endpoint (no parameters needed)
            sessions = self._make_request('Sessions')
            
            self.log_info(f"Retrieved {len(sessions)} total sessions from Jellyfin")
            
            result = []
            
            for session in sessions:
                # Only include sessions that are actively playing something
                if not session.get('NowPlayingItem'):
                    self.log_debug(f"Skipping inactive session: {session.get('Id', 'Unknown')}")
                    continue
                
                now_playing = session.get('NowPlayingItem', {})
                play_state = session.get('PlayState', {})
                user_info = session.get('UserName', 'Unknown')
                
                self.log_info(f"Processing active session for user '{user_info}' playing '{now_playing.get('Name', 'Unknown')}'")
                
                # Return the raw session data for compatibility with the streaming page
                result.append(session)
            
            self.log_info(f"Returning {len(result)} active sessions from Jellyfin")
            return result
            
        except Exception as e:
            self.log_error(f"Error fetching active sessions: {e}")
            return []
    
    def _calculate_progress(self, play_state: Dict, item: Dict) -> float:
        """Calculate playback progress percentage"""
        position = play_state.get('PositionTicks', 0)
        duration = item.get('RunTimeTicks', 0)
        
        if duration > 0:
            return round((position / duration) * 100, 1)
        return 0.0
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate a Jellyfin session"""
        try:
            data = {'Reason': reason or 'Terminated by administrator'}
            self._make_request(f'Sessions/{session_id}/Playing/Stop', method='POST', data=data)
            return True
        except Exception as e:
            self.log_error(f"Error terminating session: {e}")
            return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Jellyfin server information"""
        try:
            client = self._get_client()
            
            # Try to use official client methods if available
            if hasattr(client, 'jellyfin') and hasattr(client.jellyfin, 'get_system_info'):
                info = client.jellyfin.get_system_info()
            else:
                info = self._make_request('System/Info')
            
            return {
                'name': info.get('ServerName', self.name),
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
                'version': info.get('Version', 'Unknown'),
                'server_id': info.get('Id', '')
            }
        except:
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': False,
                'version': 'Unknown'
            }
    
    def get_jellyfin_client(self) -> JellyfinClient:
        """Get the underlying Jellyfin client for advanced operations"""
        return self._get_client()
    
    def is_authenticated(self) -> bool:
        """Check if the client is properly authenticated"""
        try:
            client = self._get_client()
            # Try a simple API call to verify authentication
            if hasattr(client, 'jellyfin') and hasattr(client.jellyfin, 'get_system_info'):
                client.jellyfin.get_system_info()
            else:
                self._make_request('System/Info')
            return True
        except Exception as e:
            self.log_error(f"Authentication check failed: {e}")
            return False
    
    def refresh_client(self):
        """Force refresh of the Jellyfin client connection"""
        self._client = None
        self.log_info("Jellyfin client connection refreshed")