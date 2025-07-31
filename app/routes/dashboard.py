# File: app/routes/dashboard.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, g, make_response, session
)
from flask_login import login_required, current_user, logout_user 
import secrets
from app.models import User, Invite, HistoryLog, Setting, EventType, SettingValueType, AdminAccount, Role, UserPreferences 
# Role forms moved to role_management.py
from app.extensions import db, scheduler # For db.func.now() if used, or db specific types
from app.utils.helpers import log_event, setup_required, permission_required, any_permission_required
# No direct plexapi imports here, plex_service should handle that.
from app.services.media_service_factory import MediaServiceFactory
from app.models_media_services import ServiceType
from app.services.plugin_manager import plugin_manager
from app.services.media_service_manager import MediaServiceManager
from app.services.unified_user_service import UnifiedUserService
from app.services import history_service
import json
from urllib.parse import urlparse
from datetime import datetime 
from functools import wraps
import xml.etree.ElementTree as ET
import xmltodict
import re

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.id == 1:
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
@setup_required 
def index():
    total_users = User.query.count()
    active_invites_count = Invite.query.filter(
        Invite.is_active == True,
        (Invite.expires_at == None) | (Invite.expires_at > db.func.now()), # Use db.func.now() for DB comparison
        (Invite.max_uses == None) | (Invite.current_uses < Invite.max_uses)
    ).count()

    # --- NEW: Get active streams count ---
    active_streams_count = 0
    try:
        active_sessions_list = MediaServiceManager.get_all_active_sessions() # This returns a list
        if active_sessions_list:
            active_streams_count = len(active_sessions_list)
    except Exception as e:
        current_app.logger.error(f"Dashboard: Failed to get active streams count: {e}")
    # --- END NEW ---

    # --- Server Status Card Logic ---
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    server_count = len(all_servers)
    server_status_data = {}

    if server_count == 1:
        server = all_servers[0]
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            server_status_data = service.get_server_info()
            server_status_data['server_id'] = server.id
            server_status_data['name'] = server.name
            server_status_data['service_type'] = server.service_type.value
    elif server_count > 1:
        online_count = 0
        offline_count = 0
        all_server_statuses = []
        servers_by_service = {}
        
        for server in all_servers:
            service = MediaServiceFactory.create_service_from_db(server)
            if service:
                status = service.get_server_info()
                # Extract the actual server name BEFORE overriding the 'name' field
                actual_server_name = status.get('name', server.name)
                
                # DEBUG: Log what we're getting from each service (remove these lines after testing)
                # current_app.logger.info(f"DEBUG SERVER INFO - Server: {server.name} ({server.service_type.value})")
                # current_app.logger.info(f"DEBUG SERVER INFO - Raw status: {status}")
                # current_app.logger.info(f"DEBUG SERVER INFO - Extracted actual_server_name: '{actual_server_name}'")
                
                status['server_id'] = server.id
                status['custom_name'] = server.name  # Custom nickname from app
                status['actual_server_name'] = actual_server_name  # Actual server name from service
                status['name'] = server.name  # Override with custom name for backward compatibility
                status['service_type'] = server.service_type.value
                all_server_statuses.append(status)
                
                # Group by service type for categorized display
                service_type = server.service_type.value
                if service_type not in servers_by_service:
                    servers_by_service[service_type] = {
                        'service_name': service_type.title(),
                        'servers': [],
                        'online_count': 0,
                        'offline_count': 0,
                        'total_count': 0
                    }
                
                servers_by_service[service_type]['servers'].append(status)
                servers_by_service[service_type]['total_count'] += 1
                
                if status.get('online'):
                    online_count += 1
                    servers_by_service[service_type]['online_count'] += 1
                else:
                    offline_count += 1
                    servers_by_service[service_type]['offline_count'] += 1
                    
        server_status_data = {
            'multi_server': True,
            'online_count': online_count,
            'offline_count': offline_count,
            'all_statuses': all_server_statuses,
            'servers_by_service': servers_by_service
        }
    # If server_count is 0, server_status_data will be an empty dict, which the template handles.
    current_app.logger.debug(f"Dashboard.py - index(): Server status from service: {server_status_data}")

    recent_activities = HistoryLog.query.order_by(HistoryLog.timestamp.desc()).limit(10).all()
    recent_activities_count = HistoryLog.query.count()

    return render_template('dashboard/index.html',
                           title="Dashboard",
                           total_users=total_users,
                           active_invites_count=active_invites_count,
                           active_streams_count=active_streams_count,
                           server_status=server_status_data,
                           recent_activities=recent_activities,
                           recent_activities_count=recent_activities_count)

# Settings routes have been moved to app/routes/settings.py

@bp.route('/settings')
@login_required
@setup_required
def settings_index():
    # Redirect to the new settings blueprint
    return redirect(url_for('settings.index'))

# Plugin management routes moved to app/routes/plugin_management.py



# Settings routes moved to app/routes/settings.py

@bp.route('/streaming')
@login_required
@setup_required
@permission_required('view_streaming')
def streaming_sessions():
    # Fetch the session monitoring interval from settings
    default_interval = current_app.config.get('SESSION_MONITORING_INTERVAL_SECONDS', 30) # Default fallback
    try:
        interval_seconds_str = Setting.get('SESSION_MONITORING_INTERVAL_SECONDS', str(default_interval))
        # Ensure it's a valid integer, otherwise use a sensible default for the template
        streaming_refresh_interval_seconds = int(interval_seconds_str)
        if streaming_refresh_interval_seconds < 5: # Enforce a minimum reasonable refresh interval for UI
            current_app.logger.warning(f"Streaming page refresh interval ({streaming_refresh_interval_seconds}s) is too low, defaulting to 5s for UI.")
            streaming_refresh_interval_seconds = 5 
    except ValueError:
        current_app.logger.warning(f"Invalid SESSION_MONITORING_INTERVAL_SECONDS ('{interval_seconds_str}') in settings. Using default {default_interval}s for streaming page refresh.")
        streaming_refresh_interval_seconds = default_interval
    except Exception as e_setting:
        current_app.logger.error(f"Error fetching SESSION_MONITORING_INTERVAL_SECONDS: {e_setting}. Using default {default_interval}s.")
        streaming_refresh_interval_seconds = default_interval


    current_app.logger.debug(f"Streaming page will use refresh interval: {streaming_refresh_interval_seconds} seconds.")
    
    return render_template('streaming/index.html', 
                           title="Active Streams", 
                           streaming_refresh_interval=streaming_refresh_interval_seconds)

@bp.route('/streaming/partial')
@login_required
@setup_required
@permission_required('view_streaming')
def streaming_sessions_partial():
    view_mode = request.args.get('view', 'merged')
    
    active_sessions_data = []
    sessions_by_server = {}  # For categorized view
    sessions_by_service = {}  # For service view
    summary_stats = {
        "total_streams": 0,
        "direct_play_count": 0,
        "transcode_count": 0,
        "total_bandwidth_mbps": 0.0,
        "lan_bandwidth_mbps": 0.0,
        "wan_bandwidth_mbps": 0.0
    }

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

    try:
        raw_sessions_from_all_services = MediaServiceManager.get_all_active_sessions()
        
        if raw_sessions_from_all_services:
            summary_stats["total_streams"] = len(raw_sessions_from_all_services)
            
            # Collect user IDs from both Plex and Jellyfin sessions
            user_ids_in_session_for_query = set()
            for rs in raw_sessions_from_all_services:
                # Plex sessions have rs.user.id, Jellyfin sessions have UserId
                if hasattr(rs, 'user') and rs.user and hasattr(rs.user, 'id'):
                    user_ids_in_session_for_query.add(int(rs.user.id))
                elif isinstance(rs, dict) and rs.get('UserId'):
                    # For Jellyfin, we need to find the user by jellyfin user ID
                    pass  # We'll handle this differently below
            
            mum_users_map_by_plex_id = {u.plex_user_id: u for u in User.query.filter(User.plex_user_id.in_(list(user_ids_in_session_for_query)))} if user_ids_in_session_for_query else {}
            
            # Also get users by primary_username for Jellyfin sessions
            jellyfin_usernames = set()
            for rs in raw_sessions_from_all_services:
                if isinstance(rs, dict) and rs.get('UserName'):
                    jellyfin_usernames.add(rs.get('UserName'))
            
            mum_users_map_by_username = {u.primary_username: u for u in User.query.filter(User.primary_username.in_(list(jellyfin_usernames)))} if jellyfin_usernames else {}

            for raw_session in raw_sessions_from_all_services:
                # Determine if this is a Plex or Jellyfin session
                is_plex_session = hasattr(raw_session, 'user') and hasattr(raw_session, 'sessionKey')
                is_jellyfin_session = isinstance(raw_session, dict) and 'UserId' in raw_session
                
                if is_plex_session:
                    # Handle Plex session format
                    user_name = getattr(raw_session.user, 'title', 'Unknown User')
                    player = raw_session.player
                    player_title = getattr(player, 'title', 'Unknown Player')
                    player_platform = getattr(player, 'platform', '')
                    product = getattr(player, 'product', 'N/A')
                    media_title = getattr(raw_session, 'title', "Unknown Title")
                    media_type = getattr(raw_session, 'type', 'unknown').capitalize()
                    year = getattr(raw_session, 'year', None)
                    library_name = getattr(raw_session, 'librarySectionTitle', "N/A")
                    progress = (raw_session.viewOffset / raw_session.duration) * 100 if raw_session.duration else 0
                    thumb_path = raw_session.thumb
                    if media_type == 'Episode' and hasattr(raw_session, 'grandparentThumb'):
                        thumb_path = raw_session.grandparentThumb
                    thumb_url = url_for('api.plex_image_proxy', path=thumb_path.lstrip('/')) if thumb_path else None
                    transcode_session = raw_session.transcodeSession
                    is_transcoding = transcode_session is not None
                    
                    location_ip = getattr(player, 'address', 'N/A')
                    is_lan = getattr(player, 'local', False)
                    location_lan_wan = "LAN" if is_lan else "WAN"
                    mum_user = mum_users_map_by_plex_id.get(int(raw_session.user.id))
                    mum_user_id = mum_user.id if mum_user else None
                    session_key = raw_session.sessionKey
                    
                    # Generate Plex user avatar URL if available
                    user_avatar_url = None
                    if hasattr(raw_session.user, 'thumb') and raw_session.user.thumb:
                        user_thumb_url = raw_session.user.thumb  # Use different variable name to avoid collision
                        # Check if this is a Plex.tv hosted avatar (external URL)
                        if user_thumb_url.startswith('https://plex.tv/') or user_thumb_url.startswith('http://plex.tv/'):
                            # Use the Plex.tv URL directly, no need to proxy
                            user_avatar_url = user_thumb_url
                            current_app.logger.debug(f"Using direct Plex.tv avatar URL for user {user_name}: {user_avatar_url}")
                        else:
                            # This is a local Plex server avatar, proxy it through our API
                            try:
                                user_avatar_url = url_for('api.plex_image_proxy', path=user_thumb_url.lstrip('/'))
                                current_app.logger.debug(f"Generated proxied Plex avatar URL for user {user_name}: {user_avatar_url}")
                            except Exception as e:
                                current_app.logger.error(f"Could not generate Plex avatar URL for user {user_name} with thumb '{user_thumb_url}': {e}")
                                user_avatar_url = None
                    else:
                        current_app.logger.debug(f"Plex user {user_name} has no thumb attribute or thumb is empty")
                    
                elif is_jellyfin_session:
                    # Handle Jellyfin session format
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
                            current_app.logger.info(f"Generated Jellyfin episode thumbnail URL: {thumb_url}")
                        else:
                            thumb_url = url_for('api.jellyfin_image_proxy', item_id=item_id, image_type='Primary')
                            current_app.logger.info(f"Generated Jellyfin movie thumbnail URL: {thumb_url} for item_id: {item_id}")
                    
                    is_transcoding = play_state.get('PlayMethod') == 'Transcode'
                    
                    location_ip = raw_session.get('RemoteEndPoint', 'N/A')
                    is_lan = not raw_session.get('IsLocal', True)  # Jellyfin logic might be inverted
                    location_lan_wan = "LAN" if is_lan else "WAN"
                    
                    # Find MUM user by username for Jellyfin
                    mum_user = mum_users_map_by_username.get(user_name)
                    mum_user_id = mum_user.id if mum_user else None
                    session_key = raw_session.get('Id', '')
                    
                    # Generate Jellyfin user avatar URL if available
                    user_avatar_url = None
                    jellyfin_user_id = raw_session.get('UserId')
                    if jellyfin_user_id:
                        try:
                            # Generate Jellyfin user avatar URL - the API route will handle checking for PrimaryImageTag
                            user_avatar_url = url_for('api.jellyfin_user_avatar_proxy', user_id=jellyfin_user_id)
                        except Exception as e:
                            current_app.logger.debug(f"Could not generate Jellyfin avatar URL for user {jellyfin_user_id}: {e}")
                            user_avatar_url = None
                    
                else:
                    # Skip unknown session formats
                    current_app.logger.warning(f"Unknown session format: {type(raw_session)}")
                    continue

                # Initialize details
                quality_detail = ""
                stream_details = ""
                video_detail = ""
                audio_detail = ""
                subtitle_detail = "None"
                container_detail = ""
                
                # Handle session details based on service type
                if is_plex_session:
                    # Find original and transcoded media parts and streams for Plex
                    original_media = next((m for m in raw_session.media if not m.selected), raw_session.media[0])
                    original_media_part = original_media.parts[0]
                    original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                    original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)

                    # Determine stream type for Plex
                    if is_transcoding:
                        # For transcodes, the session data is for the *output* stream.
                        # We need to fetch the original item to get the source quality.
                        try:
                            full_media_item = raw_session._server.fetchItem(raw_session.ratingKey)
                            original_media_part = full_media_item.media[0].parts[0]
                            original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                            original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)
                        except Exception as e:
                            current_app.logger.error(f"Could not fetch full media item for transcode session: {e}")
                            # Fallback to the potentially inaccurate session data
                            original_media_part = next((p for m in raw_session.media for p in m.parts if not p.selected), raw_session.media[0].parts[0])
                            original_video_stream = next((s for s in original_media_part.streams if s.streamType == 1), None)
                            original_audio_stream = next((s for s in original_media_part.streams if s.streamType == 2), None)

                    speed = f"(Speed: {transcode_session.speed:.1f})" if transcode_session.speed is not None else ""
                    status = "Throttled" if transcode_session.throttled else ""
                    stream_details = f"Transcode {status} {speed}".strip()
                    
                    # Container
                    original_container = original_media_part.container.upper() if original_media_part else 'N/A'
                    transcoded_container = transcode_session.container.upper()
                    container_detail = f"Converting ({original_container} \u2192 {transcoded_container})"

                    # Video
                    original_res = get_standard_resolution(original_video_stream.height) if original_video_stream else "Unknown"
                    transcoded_res = get_standard_resolution(transcode_session.height)
                    if transcode_session.videoDecision == "copy":
                        video_detail = f"Direct Stream ({original_video_stream.codec.upper()} {original_res})"
                    else:
                        video_detail = f"Transcode ({original_video_stream.codec.upper()} {original_res} \u2192 {transcode_session.videoCodec.upper()} {transcoded_res})"

                    # Audio
                    if transcode_session.audioDecision == "copy":
                        audio_detail = f"Direct Stream ({original_audio_stream.displayTitle})"
                    else:
                        original_audio_display = original_audio_stream.displayTitle if original_audio_stream else "Unknown"
                        audio_channel_layout_map = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
                        transcoded_channel_layout = audio_channel_layout_map.get(transcode_session.audioChannels, f"{transcode_session.audioChannels}ch")
                        transcoded_audio_display = f"{transcode_session.audioCodec.upper()} {transcoded_channel_layout}"
                        audio_detail = f"Transcode ({original_audio_display} \u2192 {transcoded_audio_display})"

                    # Subtitle
                    selected_subtitle_stream = next((s for m in raw_session.media for p in m.parts for s in p.streams if s.streamType == 3 and s.selected), None)
                    if transcode_session.subtitleDecision == "transcode":
                        if selected_subtitle_stream:
                            lang = selected_subtitle_stream.language or "Unknown"
                            # The 'format' attribute seems to reliably hold the destination container (e.g., 'ass', 'srt')
                            dest_format = (getattr(selected_subtitle_stream, 'format', '???') or '???').upper()
                            display_title = selected_subtitle_stream.displayTitle
                            match = re.search(r'\((.*?)\)', display_title)
                            if match:
                                # Extracts "SRT" from "English (SRT)"
                                original_format = match.group(1).upper()
                            else:
                                original_format = '???'
                            
                            if original_format != dest_format and dest_format != '???':
                                subtitle_detail = f"Transcode ({lang} - {original_format} → {dest_format})"
                            else:
                                # Fallback to a simpler display if formats match or dest is unknown
                                subtitle_detail = f"Transcode ({display_title})"
                        else:
                            subtitle_detail = "Transcode (Unknown)"
                    elif transcode_session.subtitleDecision == "copy":
                        if selected_subtitle_stream:
                            subtitle_detail = f"Direct Stream ({selected_subtitle_stream.displayTitle})"
                        else:
                            subtitle_detail = "Direct Stream (Unknown)"

                    # Quality
                    transcoded_media = next((m for m in raw_session.media if m.selected), None)
                    quality_res = get_standard_resolution(getattr(transcoded_media, 'height', transcode_session.height))
                    if transcoded_media:
                        quality_detail = f"{quality_res} ({transcoded_media.bitrate / 1000:.1f} Mbps)"
                    else:
                        quality_detail = f"{quality_res} (Bitrate N/A)"

                elif is_plex_session and not is_transcoding:
                    # Plex Direct Play
                    stream_details = "Direct Play"
                    if any(p.decision == 'transcode' for m in raw_session.media for p in m.parts):
                        stream_details = "Direct Stream"

                    original_res = get_standard_resolution(original_video_stream.height) if original_video_stream else "Unknown"
                    container_detail = original_media_part.container.upper()
                    video_detail = f"Direct Play ({original_video_stream.codec.upper()} {original_res})" if original_video_stream else "Direct Play (Unknown Video)"
                    audio_detail = f"Direct Play ({original_audio_stream.displayTitle})" if original_audio_stream else "Direct Play (Unknown Audio)"
                    
                    selected_subtitle_stream = next((s for m in raw_session.media for p in m.parts for s in p.streams if s.streamType == 3 and s.selected), None)
                    if selected_subtitle_stream:
                        subtitle_detail = f"Direct Play ({selected_subtitle_stream.displayTitle})"

                    quality_detail = f"Original ({original_media.bitrate / 1000:.1f} Mbps)"

                else:
                    # Jellyfin session handling (enhanced)
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

                # Prepare raw data for modal
                raw_session_dict = {}
                if is_plex_session:
                    if hasattr(raw_session, '_data') and raw_session._data is not None:
                        raw_xml_string = ET.tostring(raw_session._data, encoding='unicode')
                        raw_session_dict = xmltodict.parse(raw_xml_string)
                elif is_jellyfin_session:
                    raw_session_dict = raw_session  # Jellyfin sessions are already dict format
                
                raw_json_string = json.dumps(raw_session_dict, indent=2)

                # Get additional details based on session type
                if is_plex_session:
                    grandparent_title = getattr(raw_session, 'grandparentTitle', None)
                    parent_title = getattr(raw_session, 'parentTitle', None)
                    player_state = getattr(raw_session.player, 'state', 'N/A').capitalize()
                    bitrate_calc = raw_session.media[0].bitrate if raw_session.media else 0
                else:
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
                    'user': user_name, 'mum_user_id': mum_user_id, 'player_title': player_title,
                    'player_platform': player_platform, 'product': product, 'media_title': media_title,
                    'grandparent_title': grandparent_title,
                    'parent_title': parent_title, 'media_type': media_type,
                    'library_name': library_name, 'year': year, 'state': player_state,
                    'progress': round(progress, 1), 'thumb_url': thumb_url, 'session_key': session_key,
                    'user_avatar_url': user_avatar_url,  # Add user avatar URL
                    'quality_detail': quality_detail, 'stream_detail': stream_details,
                    'container_detail': container_detail,
                    'video_detail': video_detail, 'audio_detail': audio_detail, 'subtitle_detail': subtitle_detail,
                    'location_detail': f"{location_lan_wan}: {location_ip}", 'is_public_ip': not is_lan,
                    'location_ip': location_ip, 'bandwidth_detail': f"Streaming via {location_lan_wan}",
                    'bitrate_calc': bitrate_calc, 'location_type_calc': location_lan_wan,
                    'is_transcode_calc': is_transcoding,
                    'raw_data_json': raw_json_string,
                    'raw_data_json_lines': raw_json_string.splitlines()
                }
                active_sessions_data.append(session_details)

                # For categorized and service views, group sessions
                if view_mode == 'categorized':
                    # Get the actual server name from the session data
                    server_name = getattr(raw_session, 'server_name', None)
                    
                    # Get the actual server name from the server itself (not the custom name)
                    actual_server_name = None
                    if is_plex_session:
                        # For Plex, try to get the server name from the session
                        actual_server_name = getattr(raw_session, 'machineIdentifier', None)
                        if hasattr(raw_session, '_server') and hasattr(raw_session._server, 'friendlyName'):
                            actual_server_name = raw_session._server.friendlyName
                    else:
                        # For Jellyfin, get the server name from the session data
                        actual_server_name = raw_session.get('ServerName', None)
                    
                    if not server_name:
                        # Fallback to service type if server_name not available
                        if is_plex_session:
                            server_name = "Plex Server"
                        else:
                            server_name = "Jellyfin Server"
                    if server_name not in sessions_by_server:
                        sessions_by_server[server_name] = {
                            'sessions': [],
                            'actual_server_name': actual_server_name,
                            'stats': {
                                "total_streams": 0,
                                "direct_play_count": 0,
                                "transcode_count": 0,
                                "total_bandwidth_mbps": 0.0,
                                "lan_bandwidth_mbps": 0.0,
                                "wan_bandwidth_mbps": 0.0
                            }
                        }
                    
                    sessions_by_server[server_name]['sessions'].append(session_details)
                    sessions_by_server[server_name]['stats']['total_streams'] += 1

                # For service view, group by service type
                elif view_mode == 'service':
                    # Determine service type from session
                    if is_plex_session:
                        service_name = "Plex"
                        service_type = "plex"
                    elif is_jellyfin_session:
                        service_name = "Jellyfin"
                        service_type = "jellyfin"
                    else:
                        service_name = "Unknown Service"
                        service_type = "unknown"
                    
                    if service_name not in sessions_by_service:
                        sessions_by_service[service_name] = {
                            'sessions': [],
                            'service_type': service_type,
                            'stats': {
                                "total_streams": 0,
                                "direct_play_count": 0,
                                "transcode_count": 0,
                                "total_bandwidth_mbps": 0.0,
                                "lan_bandwidth_mbps": 0.0,
                                "wan_bandwidth_mbps": 0.0
                            }
                        }
                    
                    sessions_by_service[service_name]['sessions'].append(session_details)
                    sessions_by_service[service_name]['stats']['total_streams'] += 1

                if is_transcoding:
                    summary_stats["transcode_count"] += 1
                    if view_mode == 'categorized' and server_name in sessions_by_server:
                        sessions_by_server[server_name]['stats']['transcode_count'] += 1
                    elif view_mode == 'service' and service_name in sessions_by_service:
                        sessions_by_service[service_name]['stats']['transcode_count'] += 1
                else:
                    summary_stats["direct_play_count"] += 1
                    if view_mode == 'categorized' and server_name in sessions_by_server:
                        sessions_by_server[server_name]['stats']['direct_play_count'] += 1
                    elif view_mode == 'service' and service_name in sessions_by_service:
                        sessions_by_service[service_name]['stats']['direct_play_count'] += 1
                
                # Bandwidth Calculation (moved to be unconditional)
                if is_plex_session:
                    bitrate_kbps = getattr(raw_session.session, 'bandwidth', 0)
                else:
                    # Enhanced Jellyfin bandwidth calculation
                    if transcoding_info and transcoding_info.get('Bitrate'):
                        bitrate_kbps = transcoding_info.get('Bitrate', 0) / 1000  # Convert from bps to kbps
                    elif original_video_stream and original_video_stream.get('BitRate'):
                        bitrate_kbps = original_video_stream.get('BitRate', 0) / 1000  # Convert from bps to kbps
                    else:
                        bitrate_kbps = 0
                bitrate_mbps = (bitrate_kbps or 0) / 1000
                summary_stats["total_bandwidth_mbps"] += bitrate_mbps
                if is_lan:
                    summary_stats["lan_bandwidth_mbps"] += bitrate_mbps
                else:
                    summary_stats["wan_bandwidth_mbps"] += bitrate_mbps

                # Update server-specific stats for categorized view
                if view_mode == 'categorized' and server_name in sessions_by_server:
                    sessions_by_server[server_name]['stats']['total_bandwidth_mbps'] += bitrate_mbps
                    if is_lan:
                        sessions_by_server[server_name]['stats']['lan_bandwidth_mbps'] += bitrate_mbps
                    else:
                        sessions_by_server[server_name]['stats']['wan_bandwidth_mbps'] += bitrate_mbps
                
                # Update service-specific stats for service view
                elif view_mode == 'service' and service_name in sessions_by_service:
                    sessions_by_service[service_name]['stats']['total_bandwidth_mbps'] += bitrate_mbps
                    if is_lan:
                        sessions_by_service[service_name]['stats']['lan_bandwidth_mbps'] += bitrate_mbps
                    else:
                        sessions_by_service[service_name]['stats']['wan_bandwidth_mbps'] += bitrate_mbps

        # Round bandwidth values
        summary_stats["total_bandwidth_mbps"] = round(summary_stats["total_bandwidth_mbps"], 1)
        summary_stats["lan_bandwidth_mbps"] = round(summary_stats["lan_bandwidth_mbps"], 1)
        summary_stats["wan_bandwidth_mbps"] = round(summary_stats["wan_bandwidth_mbps"], 1)

        # Round server-specific bandwidth values
        if view_mode == 'categorized':
            for server_data in sessions_by_server.values():
                server_data['stats']['total_bandwidth_mbps'] = round(server_data['stats']['total_bandwidth_mbps'], 1)
                server_data['stats']['lan_bandwidth_mbps'] = round(server_data['stats']['lan_bandwidth_mbps'], 1)
                server_data['stats']['wan_bandwidth_mbps'] = round(server_data['stats']['wan_bandwidth_mbps'], 1)
        
        # Round service-specific bandwidth values
        elif view_mode == 'service':
            for service_data in sessions_by_service.values():
                service_data['stats']['total_bandwidth_mbps'] = round(service_data['stats']['total_bandwidth_mbps'], 1)
                service_data['stats']['lan_bandwidth_mbps'] = round(service_data['stats']['lan_bandwidth_mbps'], 1)
                service_data['stats']['wan_bandwidth_mbps'] = round(service_data['stats']['wan_bandwidth_mbps'], 1)

    except Exception as e:
        current_app.logger.error(f"STREAMING_DEBUG: Error during streaming_sessions_partial: {e}", exc_info=True)
    
    if view_mode == 'categorized':
        return render_template('streaming/partials/sessions_categorized.html', 
                               sessions_by_server=sessions_by_server, 
                               summary_stats=summary_stats)
    elif view_mode == 'service':
        return render_template('streaming/partials/sessions_categorized_by_service.html', 
                               sessions_by_service=sessions_by_service, 
                               summary_stats=summary_stats)
    else:
        return render_template('streaming/partials/sessions.html', 
                               sessions=active_sessions_data, 
                               summary_stats=summary_stats)

# Admin management routes moved to app/routes/admin_management.py

# Role management routes moved to app/routes/role_management.py

# Admin delete route moved to app/routes/admin_management.py

# All role management routes moved to app/routes/role_management.py

# Admin edit and reset password routes moved to app/routes/admin_management.py

@bp.route('/libraries')
@login_required
@setup_required
# Optional: Add a new permission check here if desired
# @permission_required('view_libraries')
def libraries():
    all_libraries = []
    all_servers = MediaServiceManager.get_all_servers(active_only=True)
    for server in all_servers:
        service = MediaServiceFactory.create_service_from_db(server)
        if service:
            try:
                libs = service.get_libraries()
                for lib in libs:
                    lib['server_name'] = server.name
                    lib['service_type'] = server.service_type.value
                all_libraries.extend(libs)
            except Exception as e:
                current_app.logger.error(f"Error getting libraries from {server.name}: {e}")

    return render_template(
        'libraries/index.html',
        title="Libraries",
        libraries=all_libraries
    )

# Logs routes moved to app/routes/settings.py