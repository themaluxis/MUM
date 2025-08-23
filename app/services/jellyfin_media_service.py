# File: app/services/jellyfin_media_service.py
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse
import requests
from jellyfin_apiclient_python import JellyfinClient
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType
from app.utils.timeout_helper import get_api_timeout

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
            
            # Authenticate with API key or username/password
            try:
                self.log_info("Attempting to connect to Jellyfin server...")
                self._client.auth.connect_to_address(server_url)
                self.log_info("Connection established, attempting authentication...")
                
                # Check if we have API key or username/password
                has_api_key = self.api_key and self.api_key.strip() != ''
                has_credentials = self.username and self.password and self.username.strip() != '' and self.password.strip() != ''
                
                if has_api_key:
                    self.log_info("Using API key authentication")
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
                elif has_credentials:
                    self.log_info(f"Using username/password authentication for user: {self.username}")
                    # Authenticate with username and password
                    if hasattr(self._client.auth, 'authenticate_by_name'):
                        self.log_info("Using authenticate_by_name method with username/password")
                        self._client.auth.authenticate_by_name(self.username, self.password, save_credentials=False)
                    else:
                        # Fallback: try to get token via login API
                        self.log_info("Fallback: attempting to get token via login API")
                        login_data = {
                            'Username': self.username,
                            'Pw': self.password
                        }
                        # Make direct login request to get token
                        import requests
                        login_url = f"{server_url}/Users/authenticatebyname"
                        response = requests.post(login_url, json=login_data, timeout=10)
                        response.raise_for_status()
                        auth_result = response.json()
                        access_token = auth_result.get('AccessToken')
                        if access_token:
                            self._client.config.data['auth.token'] = access_token
                            self.log_info("Successfully obtained access token via username/password")
                        else:
                            raise Exception("Failed to obtain access token from login response")
                else:
                    raise Exception("No valid authentication method available")
                    
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
                # Ensure the session has the authentication token
                auth_token = None
                if hasattr(client.config, 'data') and client.config.data.get('auth.token'):
                    auth_token = client.config.data.get('auth.token')
                elif self.api_key and self.api_key.strip() != '':
                    auth_token = self.api_key
                
                if auth_token and 'X-Emby-Token' not in session.headers:
                    session.headers['X-Emby-Token'] = auth_token
                    self.log_info("Added authentication token to official client session")
            else:
                # Fallback to requests if client structure is different or session is None
                self.log_info("Falling back to requests session with manual headers (client session is None or unavailable)")
                session = requests.Session()
                
                # Determine authentication token
                auth_token = None
                if hasattr(client, 'config') and hasattr(client.config, 'data') and client.config.data.get('auth.token'):
                    auth_token = client.config.data.get('auth.token')
                elif self.api_key and self.api_key.strip() != '':
                    auth_token = self.api_key
                
                headers = {'Content-Type': 'application/json'}
                if auth_token:
                    headers['X-Emby-Token'] = auth_token
                
                session.headers.update(headers)
            
            url = f"{self.url.rstrip('/')}/{endpoint.lstrip('/')}"
            self.log_info(f"Full request URL: {url}")
            
            # Log headers (but mask the API key for security)
            headers_to_log = dict(session.headers)
            if 'X-Emby-Token' in headers_to_log:
                headers_to_log['X-Emby-Token'] = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
            self.log_info(f"Request headers: {headers_to_log}")
            
            timeout = get_api_timeout()
            if method == 'GET':
                response = session.get(url, timeout=timeout)
            elif method == 'POST':
                self.log_info(f"POST data: {data}")
                response = session.post(url, json=data, timeout=timeout)
            elif method == 'DELETE':
                response = session.delete(url, timeout=timeout)
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
            
            timeout = get_api_timeout()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                self.log_info(f"Direct POST data: {data}")
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=timeout)
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
            # Check if we have either API key or username/password
            has_api_key = self.api_key and self.api_key.strip() != ''
            has_credentials = self.username and self.password and self.username.strip() != '' and self.password.strip() != ''
            
            if not has_api_key and not has_credentials:
                return False, "Either API key or username and password are required for Jellyfin connection"
            
            self.log_info(f"Testing Jellyfin connection to: {self.url}")
            if has_api_key:
                self.log_info(f"Using API key: {self.api_key[:8]}..." if len(self.api_key) > 8 else "***")
            else:
                self.log_info(f"Using username/password authentication for user: {self.username}")
            
            # Get system info from System/Info endpoint (this contains the version)
            info = self._make_request('System/Info')
            
            # Use the actual Jellyfin server name from System/Info
            server_name = info.get('ServerName', self.name)
            
            # Get version from System/Info - try different possible field names
            version = (info.get('Version') or 
                      info.get('ServerVersion') or 
                      info.get('ApplicationVersion') or 
                      info.get('ProductVersion') or 
                      'Unknown')
            
            # Debug logging to see what's available
            self.log_info(f"Jellyfin system info keys: {list(info.keys())}")
            self.log_info(f"Jellyfin server name: '{server_name}', version: '{version}'")
            
            auth_method = "API key" if has_api_key else "username/password"
            return True, f"Connected to {server_name} (v{version}) using {auth_method}"
        except Exception as e:
            error_msg = str(e)
            self.log_error(f"Jellyfin connection test failed: {error_msg}")
            
            # Provide more helpful error messages
            if "401" in error_msg or "Unauthorized" in error_msg:
                auth_method = "API key" if (self.api_key and self.api_key.strip() != '') else "username/password"
                return False, f"Connection failed: Invalid {auth_method} or insufficient permissions"
            elif "404" in error_msg or "Not Found" in error_msg:
                return False, "Connection failed: Server URL not found or invalid endpoint"
            elif "timeout" in error_msg.lower():
                return False, "Connection failed: Request timed out - check server URL and network connectivity"
            elif "connection" in error_msg.lower():
                return False, f"Connection failed: Unable to reach server - check URL and network connectivity"
            else:
                return False, f"Connection failed: {error_msg}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Jellyfin libraries"""
        self.log_info("Starting get_libraries() - fetching libraries from Jellyfin server")
        
        try:
            libraries = self._make_request('Library/VirtualFolders')
            # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Raw libraries response: {libraries}")
            
            result = []
            
            for i, lib in enumerate(libraries):
                # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Processing library {i+1}/{len(libraries)}: {lib}")
                # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Library keys: {list(lib.keys()) if isinstance(lib, dict) else 'Not a dict'}")
                
                # Extract library information
                lib_name = lib.get('Name', 'Unknown')
                lib_id = lib.get('ItemId')  # This might be None for VirtualFolders
                collection_type = lib.get('CollectionType', 'mixed')
                
                # For VirtualFolders, we might need to get the actual library ID differently
                # Let's check what fields are available
                self.log_info(f"Library '{lib_name}': ItemId='{lib_id}', CollectionType='{collection_type}'")
                
                # If no ItemId, try to use the Name as a fallback identifier
                external_id = lib_id if lib_id else lib_name
                
                # Try to get actual item count for this library using multiple approaches
                item_count = 0
                try:
                    # Method 1: Try using the ItemId from VirtualFolders if available
                    if lib_id:
                        try:
                            items_response = self._make_request(f'Items?ParentId={lib_id}&Recursive=true&Fields=BasicSyncInfo&Limit=1')
                            item_count = items_response.get('TotalRecordCount', 0)
                            self.log_info(f"Library '{lib_name}' (Method 1 - VirtualFolder ID): {item_count} items")
                        except Exception as method1_error:
                            self.log_warning(f"Method 1 failed for library '{lib_name}': {method1_error}")
                    
                    # Method 2: If Method 1 failed or no ItemId, try MediaFolders approach
                    if item_count == 0:
                        try:
                            libraries_endpoint = self._make_request('Library/MediaFolders')
                            matching_folder = None
                            for folder in libraries_endpoint:
                                if folder.get('Name') == lib_name:
                                    matching_folder = folder
                                    break
                            
                            if matching_folder and matching_folder.get('Id'):
                                folder_id = matching_folder.get('Id')
                                items_response = self._make_request(f'Items?ParentId={folder_id}&Recursive=true&Fields=BasicSyncInfo&Limit=1')
                                item_count = items_response.get('TotalRecordCount', 0)
                                self.log_info(f"Library '{lib_name}' (Method 2 - MediaFolder): {item_count} items")
                            else:
                                self.log_warning(f"Could not find matching MediaFolder for library '{lib_name}'")
                        except Exception as method2_error:
                            self.log_warning(f"Method 2 failed for library '{lib_name}': {method2_error}")
                    
                    # Method 3: If both failed, try the Views endpoint which sometimes has different IDs
                    if item_count == 0:
                        try:
                            views_response = self._make_request('UserViews')
                            matching_view = None
                            for view in views_response.get('Items', []):
                                if view.get('Name') == lib_name:
                                    matching_view = view
                                    break
                            
                            if matching_view and matching_view.get('Id'):
                                view_id = matching_view.get('Id')
                                items_response = self._make_request(f'Items?ParentId={view_id}&Recursive=true&Fields=BasicSyncInfo&Limit=1')
                                item_count = items_response.get('TotalRecordCount', 0)
                                self.log_info(f"Library '{lib_name}' (Method 3 - UserViews): {item_count} items")
                            else:
                                self.log_warning(f"Could not find matching UserView for library '{lib_name}'")
                        except Exception as method3_error:
                            self.log_warning(f"Method 3 failed for library '{lib_name}': {method3_error}")
                    
                    if item_count == 0:
                        self.log_warning(f"All methods failed to get item count for library '{lib_name}'")
                        
                except Exception as count_error:
                    self.log_error(f"Error getting item count for library '{lib_name}': {count_error}")
                    item_count = 0

                library_data = {
                    'id': external_id,
                    'name': lib_name,
                    'type': collection_type.lower() if collection_type else 'mixed',
                    'item_count': item_count,
                    'external_id': external_id,
                    'raw_data': lib  # Store the complete VirtualFolders response for the info modal
                }
                
                # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Processed library data: {library_data}")
                # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Library data keys: {list(library_data.keys())}")
                # DONT DELETE, USED FOR DEBUGGING. self.log_info(f"Raw data present: {'raw_data' in library_data}")
                if 'raw_data' in library_data:
                    self.log_info(f"Raw data type: {type(library_data['raw_data'])}")
                    self.log_info(f"Raw data keys: {list(library_data['raw_data'].keys()) if isinstance(library_data['raw_data'], dict) else 'Not a dict'}")
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
            
            # Library access will be set separately via update_user_access method
            # This follows the same pattern as the manual library access update process
            
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
                # Get the current user data which includes the policy
                self.log_info(f"Getting current user data for user {user_id}")
                user_data = self._make_request(f'Users/{user_id}')
                current_policy = user_data.get('Policy', {})
                self.log_info(f"Current policy retrieved from user data: {current_policy}")
                
                # Update only the library access fields
                if library_ids == ['*']:
                    current_policy['EnabledFolders'] = []
                    current_policy['EnableAllFolders'] = True
                    self.log_info(f"Setting user {user_id} to have access to ALL libraries")
                else:
                    current_policy['EnabledFolders'] = library_ids
                    current_policy['EnableAllFolders'] = False
                    self.log_info(f"Setting user {user_id} to have access to specific libraries: {library_ids}")
                
                # Send the complete policy back to Jellyfin
                self.log_info(f"Sending updated policy: {current_policy}")
                self._make_request(f'Users/{user_id}/Policy', method='POST', data=current_policy)
                self.log_info(f"Successfully updated Jellyfin user {user_id} library access")
            
            return True
        except Exception as e:
            self.log_error(f"Error updating user access for user {user_id}: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete Jellyfin user"""
        try:
            self.log_info(f"Attempting to delete Jellyfin user with ID: {user_id}")
            response = self._make_request(f'Users/{user_id}', method='DELETE')
            self.log_info(f"Successfully deleted Jellyfin user {user_id}")
            return True
        except Exception as e:
            self.log_error(f"Error deleting Jellyfin user {user_id}: {e}")
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
                    self.log_info(f"Skipping inactive session: {session.get('Id', 'Unknown')}")
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

    def get_formatted_sessions(self) -> List[Dict[str, Any]]:
        """Get active Jellyfin sessions formatted for display"""
        from app.models import UserAppAccess
        from flask import url_for
        import json
        
        raw_sessions = self.get_active_sessions()
        if not raw_sessions:
            return []
        
        # Get user mapping for Jellyfin users
        jellyfin_usernames = {session.get('UserName') for session in raw_sessions if session.get('UserName')}
        # Get UserAppAccess via UserMediaAccess for Jellyfin usernames
        from app.models_media_services import UserMediaAccess
        if jellyfin_usernames:
            jellyfin_accesses = UserMediaAccess.query.filter(
                UserMediaAccess.server_id == self.server_id,
                UserMediaAccess.external_username.in_(list(jellyfin_usernames))
            ).all()
            mum_users_map_by_username = {access.external_username: access.user_app_access for access in jellyfin_accesses}
        else:
            mum_users_map_by_username = {}
        
        formatted_sessions = []
        
        def get_standard_resolution(height_str):
            if not height_str: return "SD"
            try:
                height = int(height_str)
                if height <= 240: return "240p"
                if height <= 360: return "360p"
                if height <= 480: return "480p"
                if height <= 576: return "576p"
                if height <= 720: return "720p"
                if height <= 1080: return "1080p"
                if height <= 1440: return "1440p"
                if height <= 2160: return "4K"
                return f"{height}p"
            except (ValueError, TypeError):
                return "SD"
        
        for raw_session in raw_sessions:
            try:
                # Basic session info
                user_name = raw_session.get('UserName', 'Unknown User')
                now_playing = raw_session.get('NowPlayingItem', {})
                play_state = raw_session.get('PlayState', {})
                
                player_title = raw_session.get('DeviceName', 'Unknown Device')
                player_platform = raw_session.get('Client', '')
                product = raw_session.get('ApplicationVersion', 'N/A')
                media_title = now_playing.get('Name', "Unknown Title")
                media_type = now_playing.get('Type', 'unknown').capitalize()
                year = now_playing.get('ProductionYear', None)
                library_name = "Library"  # Generic library name for Jellyfin
                
                # Calculate progress for Jellyfin
                position_ticks = play_state.get('PositionTicks', 0)
                runtime_ticks = now_playing.get('RunTimeTicks', 0)
                progress = (position_ticks / runtime_ticks) * 100 if runtime_ticks else 0
                
                # Handle Jellyfin thumbnails
                thumb_url = None
                item_id = now_playing.get('Id')
                if item_id:
                    # For episodes, prefer series poster; for movies, use primary image
                    if media_type == 'Episode' and now_playing.get('SeriesId'):
                        thumb_url = url_for('api.jellyfin_image_proxy', item_id=now_playing.get('SeriesId'), image_type='Primary')
                    else:
                        thumb_url = url_for('api.jellyfin_image_proxy', item_id=item_id, image_type='Primary')
                
                is_transcoding = play_state.get('PlayMethod') == 'Transcode'
                
                location_ip = raw_session.get('RemoteEndPoint', 'N/A')
                is_local = raw_session.get('IsLocal', True)  # Jellyfin's IsLocal field
                location_lan_wan = "LAN" if is_local else "WAN"
                
                # Find MUM user by username for Jellyfin
                mum_user = mum_users_map_by_username.get(user_name)
                mum_user_id = mum_user.id if mum_user else None
                session_key = raw_session.get('Id', '')
                
                # Generate Jellyfin user avatar URL if available
                user_avatar_url = None
                jellyfin_user_id = raw_session.get('UserId')
                if jellyfin_user_id:
                    try:
                        # Check if user has an avatar before generating URL
                        user_info = self._get_user_info(jellyfin_user_id)
                        if user_info and user_info.get('PrimaryImageTag'):
                            user_avatar_url = url_for('api.jellyfin_user_avatar_proxy', user_id=jellyfin_user_id)
                    except Exception:
                        user_avatar_url = None
                
                # Initialize details
                quality_detail = ""
                stream_details = ""
                video_detail = ""
                audio_detail = ""
                subtitle_detail = "None"
                container_detail = ""
                
                # Handle session details for Jellyfin
                transcoding_info = raw_session.get('TranscodingInfo', {})
                media_streams = now_playing.get('MediaStreams', [])
                
                # Find original video and audio streams
                original_video_stream = next((s for s in media_streams if s.get('Type') == 'Video'), None)
                original_audio_stream = next((s for s in media_streams if s.get('Type') == 'Audio' and s.get('IsDefault', False)), None)
                
                if is_transcoding and transcoding_info:
                    # Enhanced Jellyfin transcode details
                    hardware_accel = transcoding_info.get('HardwareAccelerationType', 'none')
                    if hardware_accel and hardware_accel != 'none':
                        stream_details = f"Transcode (HW: {hardware_accel.upper()})"
                    else:
                        stream_details = "Transcode"
                    
                    # Container details
                    original_container = now_playing.get('Container', 'Unknown').upper()
                    transcoded_container = transcoding_info.get('Container', 'Unknown').upper()
                    if original_container != transcoded_container:
                        container_detail = f"Converting ({original_container} -> {transcoded_container})"
                    else:
                        container_detail = f"Container: {transcoded_container}"
                    
                    # Video details
                    is_video_direct = transcoding_info.get('IsVideoDirect', False)
                    if is_video_direct and original_video_stream:
                        # Video is direct stream
                        original_height = original_video_stream.get('Height', 0)
                        original_res = get_standard_resolution(original_height)
                        original_codec = original_video_stream.get('Codec', 'Unknown').upper()
                        video_detail = f"Direct Stream ({original_codec} {original_res})"
                    else:
                        # Video is being transcoded
                        original_height = original_video_stream.get('Height', 0) if original_video_stream else 0
                        original_res = get_standard_resolution(original_height)
                        original_codec = original_video_stream.get('Codec', 'Unknown').upper() if original_video_stream else 'Unknown'
                        
                        transcoded_height = transcoding_info.get('Height', 0)
                        transcoded_res = get_standard_resolution(transcoded_height)
                        transcoded_codec = transcoding_info.get('VideoCodec', 'Unknown').upper()
                        
                        if original_video_stream:
                            video_detail = f"Transcode ({original_codec} {original_res} -> {transcoded_codec} {transcoded_res})"
                        else:
                            video_detail = f"Transcode (-> {transcoded_codec} {transcoded_res})"
                    
                    # Audio details
                    is_audio_direct = transcoding_info.get('IsAudioDirect', False)
                    if is_audio_direct and original_audio_stream:
                        # Audio is direct stream
                        audio_display = original_audio_stream.get('DisplayTitle', 'Unknown Audio')
                        audio_detail = f"Direct Stream ({audio_display})"
                    else:
                        # Audio is being transcoded
                        original_audio_display = original_audio_stream.get('DisplayTitle', 'Unknown Audio') if original_audio_stream else 'Unknown Audio'
                        transcoded_codec = transcoding_info.get('AudioCodec', 'Unknown').upper()
                        transcoded_channels = transcoding_info.get('AudioChannels', 0)
                        
                        # Map channel count to layout
                        channel_layout_map = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
                        transcoded_layout = channel_layout_map.get(transcoded_channels, f"{transcoded_channels}ch")
                        transcoded_audio_display = f"{transcoded_codec} {transcoded_layout}"
                        
                        if original_audio_stream:
                            audio_detail = f"Transcode ({original_audio_display} -> {transcoded_audio_display})"
                        else:
                            audio_detail = f"Transcode (-> {transcoded_audio_display})"
                    
                    # Quality details with bitrate
                    transcoded_height = transcoding_info.get('Height', 0)
                    transcoded_res = get_standard_resolution(transcoded_height)
                    transcoded_bitrate = transcoding_info.get('Bitrate', 0)
                    if transcoded_bitrate > 0:
                        bitrate_mbps = transcoded_bitrate / 1000000  # Convert from bps to Mbps
                        quality_detail = f"{transcoded_res} ({bitrate_mbps:.1f} Mbps)"
                    else:
                        quality_detail = f"{transcoded_res} (Transcoding)"
                        
                else:
                    # Direct Play for Jellyfin
                    stream_details = "Direct Play"
                    container_detail = now_playing.get('Container', 'Unknown').upper()
                    
                    if original_video_stream:
                        original_height = original_video_stream.get('Height', 0)
                        original_res = get_standard_resolution(original_height)
                        original_codec = original_video_stream.get('Codec', 'Unknown').upper()
                        video_detail = f"Direct Play ({original_codec} {original_res})"
                    else:
                        video_detail = "Direct Play (Unknown Video)"
                    
                    if original_audio_stream:
                        audio_display = original_audio_stream.get('DisplayTitle', 'Unknown Audio')
                        audio_detail = f"Direct Play ({audio_display})"
                    else:
                        audio_detail = "Direct Play (Unknown Audio)"
                    
                    # Quality for direct play
                    if original_video_stream:
                        original_height = original_video_stream.get('Height', 0)
                        original_res = get_standard_resolution(original_height)
                        original_bitrate = original_video_stream.get('BitRate', 0)
                        if original_bitrate > 0:
                            bitrate_mbps = original_bitrate / 1000000  # Convert from bps to Mbps
                            quality_detail = f"Original ({original_res}, {bitrate_mbps:.1f} Mbps)"
                        else:
                            quality_detail = f"Original ({original_res})"
                    else:
                        quality_detail = "Direct Play"

                # Raw data for modal (Jellyfin sessions are already dict format)
                raw_json_string = json.dumps(raw_session, indent=2)

                # Additional details
                grandparent_title = now_playing.get('SeriesName', None)
                parent_title = now_playing.get('SeasonName', None)
                player_state = 'Playing' if not play_state.get('IsPaused', False) else 'Paused'
                
                # Enhanced Jellyfin bitrate calculation for display
                if transcoding_info and transcoding_info.get('Bitrate'):
                    bitrate_calc = transcoding_info.get('Bitrate', 0) / 1000  # Convert from bps to kbps for consistency with Plex
                elif original_video_stream and original_video_stream.get('BitRate'):
                    bitrate_calc = original_video_stream.get('BitRate', 0) / 1000  # Convert from bps to kbps
                else:
                    bitrate_calc = 0

                session_details = {
                    'user': user_name,
                    'mum_user_id': mum_user_id,
                    'player_title': player_title,
                    'player_platform': player_platform,
                    'product': product,
                    'media_title': media_title,
                    'grandparent_title': grandparent_title,
                    'parent_title': parent_title,
                    'media_type': media_type,
                    'library_name': library_name,
                    'year': year,
                    'state': player_state,
                    'progress': round(progress, 1),
                    'thumb_url': thumb_url,
                    'session_key': session_key,
                    'user_avatar_url': user_avatar_url,
                    'quality_detail': quality_detail,
                    'stream_detail': stream_details,
                    'container_detail': container_detail,
                    'video_detail': video_detail,
                    'audio_detail': audio_detail,
                    'subtitle_detail': subtitle_detail,
                    'location_detail': f"{location_lan_wan}: {location_ip}",
                    'is_public_ip': not is_local,
                    'location_ip': location_ip,
                    'bandwidth_detail': f"Streaming via {location_lan_wan}",
                    'bitrate_calc': bitrate_calc,
                    'location_type_calc': location_lan_wan,
                    'is_transcode_calc': is_transcoding,
                    'raw_data_json': raw_json_string,
                    'raw_data_json_lines': raw_json_string.splitlines(),
                    'service_type': 'jellyfin',
                    'server_name': self.name
                }
                formatted_sessions.append(session_details)
                
            except Exception as e:
                self.log_error(f"Error formatting Jellyfin session: {e}")
                continue
        
        return formatted_sessions
    
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
            # Use the same method as plugin management for consistency
            info = self._make_request('System/Info')
            
            # Extract actual server name from System/Info (same as plugin management)
            actual_server_name = info.get('ServerName', self.name)
            
            # Get version from System/Info - try different possible field names
            version = (info.get('Version') or 
                      info.get('ServerVersion') or 
                      info.get('ApplicationVersion') or 
                      info.get('ProductVersion') or 
                      'Unknown')
            
            self.log_info(f"Jellyfin server info - ServerName: '{actual_server_name}', Version: '{version}'")
            
            return {
                'name': actual_server_name,  # Use actual server name from API
                'url': self.url,
                'service_type': self.service_type.value,
                'online': True,
                'version': version,
                'server_id': info.get('Id', ''),
                'actual_server_name': actual_server_name,  # Also provide as separate field for consistency
                'error_message': None
            }
        except Exception as e:
            self.log_error(f"Error getting server info: {e}")
            return {
                'name': self.name,
                'url': self.url,
                'service_type': self.service_type.value,
                'online': False,
                'version': 'Unknown',
                'actual_server_name': self.name,
                'error_message': str(e)
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