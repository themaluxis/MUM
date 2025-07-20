# File: app/services/jellyfin_media_service.py
from typing import List, Dict, Any, Optional, Tuple
import requests
from app.services.base_media_service import BaseMediaService
from app.models_media_services import ServiceType

class JellyfinMediaService(BaseMediaService):
    """Jellyfin implementation of BaseMediaService"""
    
    @property
    def service_type(self) -> ServiceType:
        return ServiceType.JELLYFIN
    
    def _get_headers(self):
        """Get headers for Jellyfin API requests"""
        return {
            'X-Emby-Token': self.api_key,  # Jellyfin uses same header as Emby
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', data: Dict = None):
        """Make API request to Jellyfin server"""
        url = f"{self.url.rstrip('/')}/{endpoint.lstrip('/')}"
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
        """Test connection to Jellyfin server"""
        try:
            info = self._make_request('System/Info')
            server_name = info.get('ServerName', 'Jellyfin Server')
            version = info.get('Version', 'Unknown')
            return True, f"Connected to {server_name} (v{version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        """Get all Jellyfin libraries"""
        try:
            libraries = self._make_request('Library/VirtualFolders')
            result = []
            
            for lib in libraries:
                result.append({
                    'id': lib.get('ItemId', lib.get('Name', '')),
                    'name': lib.get('Name', 'Unknown'),
                    'type': lib.get('CollectionType', 'mixed').lower(),
                    'item_count': 0,  # Jellyfin doesn't provide this in VirtualFolders
                    'external_id': lib.get('ItemId', lib.get('Name', ''))
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching libraries: {e}")
            return []
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Get all Jellyfin users"""
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
                    'uuid': user_id,  # Jellyfin uses GUID as ID
                    'username': user.get('Name', 'Unknown'),
                    'email': user.get('Email'),
                    'thumb': None,  # Jellyfin doesn't provide avatar URLs in user list
                    'is_home_user': False,  # Jellyfin doesn't have this concept
                    'library_ids': library_ids,
                    'is_admin': user.get('Policy', {}).get('IsAdministrator', False)
                })
            
            return result
        except Exception as e:
            self.log_error(f"Error fetching users: {e}")
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