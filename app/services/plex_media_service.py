# File: app/services/plex_media_service.py
import json
from typing import List, Dict, Any, Optional, Tuple
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount
from plexapi.exceptions import Unauthorized, NotFound, BadRequest
import requests
import xml.etree.ElementTree as ET
import xmltodict
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType
from app.models import Setting, EventType
from app.utils.helpers import log_event

class PlexMediaService(BaseMediaService):
    """Plex implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.PLEX
    
    def __init__(self, server_config: Dict[str, Any]):
        super().__init__(server_config)
        self._server_instance = None
        self._admin_account = None
    
    def _get_server_instance(self, force_reconnect=False):
        """Get PlexServer instance with caching"""
        if not force_reconnect and self._server_instance:
            try:
                _ = self._server_instance.friendlyName
                return self._server_instance
            except:
                self._server_instance = None
        
        try:
            timeout = int(self.config.get('timeout', 10))
            session = requests.Session()
            session.timeout = timeout
            self._server_instance = PlexServer(baseurl=self.url, token=self.api_key, session=session)
            return self._server_instance
        except Exception as e:
            self.log_error(f"Failed to connect to Plex server: {e}")
            return None
    
    def _get_admin_account(self):
        """Get MyPlexAccount instance with caching"""
        if not self._admin_account:
            try:
                self._admin_account = MyPlexAccount(token=self.api_key)
            except Exception as e:
                self.log_error(f"Failed to get Plex admin account: {e}")
                return None
        return self._admin_account
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Plex server"""
        server = self._get_server_instance(force_reconnect=True)
        if server:
            try:
                name = server.friendlyName
                version = server.version
                return True, f"Connected to {name} (v{version})"
            except Exception as e:
                return False, f"Connection failed: {str(e)}"
        return False, "Could not establish connection"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Plex libraries"""
        server = self._get_server_instance()
        if not server:
            return []
        
        libraries = []
        try:
            for lib in server.library.sections():
                libraries.append({
                    'id': str(lib.key),
                    'name': lib.title,
                    'type': lib.type,
                    'item_count': lib.totalSize,
                    'external_id': str(lib.key)
                })
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
        
        return libraries
    
    def _get_user_ids_sharing_servers_with_admin(self):
        admin_account = self._get_admin_account()
        if not admin_account: return set()
        owner_ids_sharing_with_admin = set()
        try:
            for resource in admin_account.resources():
                if resource.product == "Plex Media Server" and getattr(resource, 'owned', True) is False:
                    owner_id_str = getattr(resource, 'ownerId', None)
                    if owner_id_str:
                        try: owner_ids_sharing_with_admin.add(int(owner_id_str))
                        except ValueError: self.log_warning(f"Invalid ownerId '{owner_id_str}' for resource '{resource.name}'.")
            self.log_info(f"Found {len(owner_ids_sharing_with_admin)} users sharing their servers with admin.")
        except Exception as e:
            self.log_error(f"Error fetching resources shared with admin: {e}")
        return owner_ids_sharing_with_admin

    def get_users(self, users_sharing_back_ids=None) -> List[Dict[str, Any]]:
        if users_sharing_back_ids is None:
            users_sharing_back_ids = self._get_user_ids_sharing_servers_with_admin()

        admin_account = self._get_admin_account() 
        plex_server = self._get_server_instance()   

        if not admin_account:
            self.log_error("get_users(): Admin MyPlexAccount connection failed.")
            return []
        if not plex_server:
            self.log_error("get_users(): PlexServer instance connection failed.")
            return []
            
        server_machine_id = plex_server.machineIdentifier
        admin_plex_id = getattr(admin_account, 'id', None)
        
        all_my_server_library_ids_as_strings = []
        try:
            all_my_server_library_ids_as_strings = [str(lib_section.key) for lib_section in plex_server.library.sections()]
            self.log_info(f"get_users(): All available library IDs on this server: {all_my_server_library_ids_as_strings}")
        except Exception as e_all_libs:
            self.log_error(f"get_users(): Could not fetch all library IDs from server: {e_all_libs}.")

        detailed_shares_by_userid = {} 
        try:
            if hasattr(admin_account, '_session') and admin_account._session is not None and \
            hasattr(admin_account, '_token') and admin_account._token is not None:
                base_plextv_url = "https://plex.tv"
                shared_servers_url = f"{base_plextv_url}/api/servers/{server_machine_id}/shared_servers"
                self.log_info(f"get_users(): Fetching detailed shares from: {shared_servers_url}")
                headers = {'X-Plex-Token': admin_account._token, 'Accept': 'application/xml'}
                resp = admin_account._session.get(shared_servers_url, headers=headers, timeout=10)
                resp.raise_for_status()
                self.log_info(f"get_users(): Raw XML from /shared_servers: {resp.text[:500]}...")
                shared_servers_xml_root = ET.fromstring(resp.content)
                for shared_server_elem in shared_servers_xml_root.findall('SharedServer'):
                    user_id_str = shared_server_elem.get('userID')
                    if not user_id_str:
                        self.log_warning(f"get_users(): Found SharedServer element with no userID.")
                        continue
                    try:
                        user_id_int_key = int(user_id_str)
                    except ValueError:
                        self.log_warning(f"get_users(): Found SharedServer element with non-integer userID: '{user_id_str}'.")
                        continue
                    
                    all_libs = (shared_server_elem.get('allLibraries', "0") == "1")
                    accepted_at_timestamp = shared_server_elem.get('acceptedAt')
                    
                    shared_section_keys_for_user = []
                    if not all_libs: 
                        for section_elem in shared_server_elem.findall('Section'):
                            if section_elem.get('shared') == "1" and section_elem.get('key'):
                                shared_section_keys_for_user.append(str(section_elem.get('key')))
                    
                    detailed_shares_by_userid[user_id_int_key] = {
                        'allLibraries': all_libs,
                        'sharedSectionKeys': shared_section_keys_for_user,
                        'acceptedAt': accepted_at_timestamp
                    }
        except Exception as e_shared_servers:
            self.log_error(f"Error fetching or parsing detailed /shared_servers data: {type(e_shared_servers).__name__} - {e_shared_servers}", exc_info=True)

        processed_users_data = []
        try:
            all_associated_users = admin_account.users()
            for plex_user_obj in all_associated_users:
                plex_user_id_int = getattr(plex_user_obj, 'id', None)
                if plex_user_id_int is None: continue
                if admin_plex_id and plex_user_id_int == admin_plex_id: continue
                
                plex_user_uuid_str = None
                plex_thumb_url = getattr(plex_user_obj, 'thumb', None)
                
                if plex_thumb_url and "/users/" in plex_thumb_url and "/avatar" in plex_thumb_url:
                    try:
                        plex_user_uuid_str = plex_thumb_url.split('/users/')[1].split('/avatar')[0]
                    except IndexError:
                        plex_user_uuid_str = None

                if not plex_user_uuid_str:
                    self.log_warning(f"Could not parse alphanumeric UUID for user '{plex_user_obj.username}' (ID: {plex_user_id_int}). They will be matched by integer ID only.")

                user_share_details = detailed_shares_by_userid.get(plex_user_id_int)
                accepted_at_val = user_share_details.get('acceptedAt') if user_share_details else None

                user_data_basic = {
                    'id': str(plex_user_id_int),
                    'uuid': plex_user_uuid_str,
                    'username': getattr(plex_user_obj, 'username', None) or getattr(plex_user_obj, 'title', 'Unknown'),
                    'email': getattr(plex_user_obj, 'email', None), 
                    'thumb': plex_thumb_url,
                    'is_home_user': getattr(plex_user_obj, 'home', False),
                    'shares_back': plex_user_id_int in users_sharing_back_ids,
                    'library_ids': [],
                    'accepted_at': accepted_at_val,
                }

                user_share_details = detailed_shares_by_userid.get(plex_user_id_int)
                add_user_to_MUM_list = False
                effective_library_ids = []

                if user_share_details:
                    if user_share_details.get('allLibraries'):
                        effective_library_ids = all_my_server_library_ids_as_strings[:] 
                        add_user_to_MUM_list = True
                    else: 
                        specific_keys = user_share_details.get('sharedSectionKeys', [])
                        effective_library_ids = specific_keys[:] 
                        if effective_library_ids: add_user_to_MUM_list = True
                
                elif user_data_basic['is_home_user']:
                    effective_library_ids = all_my_server_library_ids_as_strings[:] 
                    add_user_to_MUM_list = True
                else: 
                    server_resource_for_this_user = None
                    for res in getattr(plex_user_obj, 'servers', []):
                        if getattr(res, 'machineIdentifier', None) == server_machine_id:
                            server_resource_for_this_user = res
                            break
                    if server_resource_for_this_user:
                        if not getattr(server_resource_for_this_user, 'pending', False):
                            add_user_to_MUM_list = True
                
                if add_user_to_MUM_list:
                    user_data_basic['library_ids'] = effective_library_ids
                    processed_users_data.append(user_data_basic)

            return processed_users_data

        except Exception as e_main_loop:
            self.log_error(f"get_users(): General error in main user processing loop: {type(e_main_loop).__name__} - {e_main_loop}", exc_info=True)
            return []

    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create/invite user to Plex server"""
        admin_account = self._get_admin_account()
        server = self._get_server_instance()
        
        if not admin_account or not server:
            raise Exception("Plex admin or server connection failed")
        
        try:
            library_ids = kwargs.get('library_ids', [])
            allow_sync = kwargs.get('allow_downloads', False)
            
            # Prepare sections to share
            sections_to_share = None
            if library_ids:
                all_libs = {str(lib.key): lib for lib in server.library.sections()}
                sections_to_share = []
                for lib_id in library_ids:
                    if str(lib_id) in all_libs:
                        sections_to_share.append(all_libs[str(lib_id)])
            
            # Invite user
            admin_account.inviteFriend(
                user=email or username,
                server=server,
                sections=sections_to_share,
                allowSync=allow_sync
            )
            
            return {
                'success': True,
                'user_id': None,  # Plex doesn't return user ID immediately
                'username': username,
                'email': email
            }
            
        except Exception as e:
            self.log_error(f"Error creating user: {e}")
            raise
    
    def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
        """Update Plex user's library access"""
        admin_account = self._get_admin_account()
        server = self._get_server_instance()
        
        if not admin_account or not server:
            return False
        
        try:
            user = admin_account.user(user_id)
            if not user:
                return False
            
            update_kwargs = {
                'user': user,
                'server': server
            }
            
            if library_ids is not None:
                if library_ids:
                    all_libs = {str(lib.key): lib for lib in server.library.sections()}
                    sections = [all_libs[str(lib_id)] for lib_id in library_ids if str(lib_id) in all_libs]
                    update_kwargs['sections'] = sections
                else:
                    update_kwargs['sections'] = []
            
            # Note: allowSync parameter causes issues, so we skip it for now
            # if 'allow_downloads' in kwargs:
            #     update_kwargs['allowSync'] = kwargs['allow_downloads']
            
            admin_account.updateFriend(**update_kwargs)
            return True
            
        except Exception as e:
            self.log_error(f"Error updating user access: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Remove user from Plex server"""
        admin_account = self._get_admin_account()
        
        if not admin_account:
            return False
        
        try:
            user = admin_account.user(user_id)
            if user:
                admin_account.removeFriend(user)
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Any]:
        """Get active Plex sessions - returns full session objects with all technical details"""
        server = self._get_server_instance()
        if not server:
            return []
        
        sessions = []
        try:
            # --- Start of added debug logging ---
            try:
                # Access the raw XML data from the server by calling the endpoint directly
                raw_data = server.query("/status/sessions")
                if raw_data is not None:
                    import xmltodict
                    import json

                    # Convert XML to a dictionary
                    sessions_dict = xmltodict.parse(ET.tostring(raw_data))
                    self.log_info(f"RAW_PLEX_SESSIONS_DATA: {json.dumps(sessions_dict, indent=2)}")

            except Exception as log_e:
                self.log_warning(f"Could not log raw session data: {log_e}")
            # --- End of added debug logging ---

            for session in server.sessions():
                # Add server context to session object for identification
                session.server_name = self.name
                session.server_id = self.server_id
                session.service_type = self.service_type.value
                sessions.append(session)
                
        except Exception as e:
            self.log_error(f"Error fetching active sessions: {e}")
        
        return sessions
    
    def terminate_session(self, session_id: str, reason: str = None) -> bool:
        """Terminate a Plex session"""
        server = self._get_server_instance()
        if not server:
            return False
        
        try:
            for session in server.sessions():
                if str(getattr(session, 'sessionKey', '')) == str(session_id):
                    session.stop(reason=reason)
                    return True
            return False
        except Exception as e:
            self.log_error(f"Error terminating session: {e}")
            return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Plex server information"""
        server = self._get_server_instance()
        if server:
            try:
                return {
                    'name': server.friendlyName,
                    'url': self.url,
                    'service_type': self.service_type.value,
                    'online': True,
                    'version': server.version,
                    'machine_id': server.machineIdentifier
                }
            except:
                pass
        
        return {
            'name': self.name,
            'url': self.url,
            'service_type': self.service_type.value,
            'online': False,
            'version': 'Unknown'
        }

    def get_geoip_info(self, ip_address: str) -> Dict[str, Any]:
        """Get GeoIP information for a given IP address using Plex's API."""
        if not ip_address or ip_address in ['127.0.0.1', 'localhost']:
            return {"status": "local", "message": "This is a local address."}

        if not self.api_key:
            self.log_error("Plex API key is missing, cannot perform GeoIP lookup.")
            return {"status": "error", "message": "Plex API key is not configured."}

        try:
            headers = {'X-Plex-Token': self.api_key}
            url = f"https://plex.tv/api/v2/geoip?ip_address={ip_address}"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Parse the XML response
            parsed_data = xmltodict.parse(response.content)
            # The root element is 'Response', let's return its content
            return parsed_data.get('Response', {})
            
        except requests.exceptions.RequestException as e:
            self.log_error(f"Failed to get GeoIP info from Plex API for {ip_address}: {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            self.log_error(f"An unexpected error occurred during GeoIP lookup: {e}", exc_info=True)
            return {"status": "error", "message": "An unexpected error occurred."}