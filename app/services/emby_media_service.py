# File: app/services/emby_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType
from app.utils.timeout_helper import get_api_timeout

class EmbyMediaService(BaseMediaService):
    """Emby implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.EMBY
    
    def _get_headers(self):
        """Get headers for Emby API requests"""
        return {
            'X-Emby-Token': self.api_key,
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to Emby server"""
        url = f"{self.url.rstrip('/')}/emby/{endpoint.lstrip('/')}"
        headers = self._get_headers()
        
        try:
            timeout = get_api_timeout()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            self.log_error(f"API request failed: {e}")
            raise
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Emby server"""
        try:
            info = self._make_request('System/Info')
            server_name = info.get('ServerName', 'Emby Server')
            version = info.get('Version', 'Unknown')
            return True, f"Connected to {server_name} (v{version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries_raw(self) -> List[Dict[str, Any]]:
        """Get raw, unmodified library data from Emby API"""
        try:
            # Return the raw API response without any modifications
            libraries = self._make_request('Library/VirtualFolders')
            self.log_info(f"Retrieved {len(libraries)} raw libraries from Emby")
            return libraries
        except Exception as e:
            self.log_error(f"Error fetching raw libraries: {e}")
            return []
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Emby libraries (processed for internal use)"""
        try:
            # Get raw data first
            libraries = self.get_libraries_raw()
            result = []
            
            # Process the raw data for internal use
            for lib in libraries:
                result.append({
                    'id': lib.get('ItemId', lib.get('Name', '')),
                    'name': lib.get('Name', 'Unknown'),
                    'type': lib.get('CollectionType', 'mixed').lower(),
                    'item_count': 0,  # Emby doesn't provide this in VirtualFolders
                    'external_id': lib.get('ItemId', lib.get('Name', ''))
                })
            
            self.log_info(f"Processed {len(result)} libraries from Emby")
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Emby users"""
        try:
            users = self._make_request('Users')
            result = []
            
            for user in users:
                user_id = user.get('Id')
                if not user_id:
                    continue
                
                # Get user's library access
                try:
                    policy = self._make_request(f'Users/{user_id}/Policy')
                    library_ids = policy.get('EnabledFolders', [])
                except:
                    library_ids = []
                
                result.append({
                    'id': user_id,
                    'uuid': user_id,  # Emby uses GUID as ID
                    'username': user.get('Name', 'Unknown'),
                    'email': user.get('Email'),
                    'thumb': None,  # Emby doesn't provide avatar URLs in user list
                    'is_home_user': False,  # Emby doesn't have this concept
                    'library_ids': library_ids,
                    'is_admin': user.get('Policy', {}).get('IsAdministrator', False)
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
            return []
    
    def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
        """Create new Emby user"""
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
        """Update Emby user's library access"""
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
        """Delete Emby user"""
        try:
            self._make_request(f'Users/{user_id}', method='DELETE')
            return True
        except Exception as e:
            self.log_error(f"Error deleting user: {e}")
            return False
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get active Emby sessions"""
        try:
            sessions = self._make_request('Sessions')
            result = []
            
            for session in sessions:
                if not session.get('NowPlayingItem'):
                    continue  # Skip inactive sessions
                
                now_playing = session.get('NowPlayingItem', {})
                user_info = session.get('UserName', 'Unknown')
                
                result.append({
                    'session_id': session.get('Id', ''),
                    'user_id': session.get('UserId', ''),
                    'username': user_info,
                    'media_title': now_playing.get('Name', 'Unknown'),
                    'media_type': now_playing.get('Type', 'unknown'),
                    'player': session.get('Client', 'Unknown'),
                    'platform': session.get('ApplicationVersion', ''),
                    'state': 'playing' if session.get('PlayState', {}).get('IsPaused') == False else 'paused',
                    'progress_percent': self._calculate_progress(session.get('PlayState', {}), now_playing),
                    'ip_address': session.get('RemoteEndPoint', ''),
                    'is_lan': session.get('IsLocal', False)
                })
            
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
        """Terminate an Emby session"""
        try:
            data = {'Reason': reason or 'Terminated by administrator'}
            self._make_request(f'Sessions/{session_id}/Playing/Stop', method='POST', data=data)
            return True
        except Exception as e:
            self.log_error(f"Error terminating session: {e}")
            return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get Emby server information"""
        try:
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

    def get_formatted_sessions(self) -> List[Dict[str, Any]]:
        """Get active Emby sessions formatted for display"""
        # TODO: Implement Emby session formatting
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
        """Check if a username already exists in Emby"""
        try:
            users = self.get_users()
            for user in users:
                if user.get('Name', '').lower() == username.lower():
                    return True
            return False
        except Exception as e:
            self.log_error(f"Error checking username '{username}': {e}")
            return False  # Assume username doesn't exist if we can't check