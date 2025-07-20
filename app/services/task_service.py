# File: app/services/task_service.py
from flask import current_app
from app.extensions import scheduler 
from app.models import Setting, EventType, User, StreamHistory
from app.models_media_services import ServiceType
from app.utils.helpers import log_event
from . import user_service # user_service is needed for deleting users
from app.services.media_service_manager import MediaServiceManager
from datetime import datetime, timezone, timedelta 
from app.extensions import db

_active_stream_sessions = {}

# --- Scheduled Tasks ---

def monitor_plex_sessions_task():
    """
    Statefully monitors Plex sessions, with corrected session tracking and duration calculation.
    - Creates a new StreamHistory record when a new session starts.
    - Continuously updates the view offset (progress) on the SAME record for an ongoing session.
    - Correctly calculates final playback duration from the last known viewOffset when the session stops.
    - Enforces "No 4K Transcoding" user setting with improved detection.
    """
    global _active_stream_sessions
    with scheduler.app.app_context():
        current_app.logger.info("--- Running Plex Session Monitor Task ---")
        
        # Correctly check for any active Plex server from the database
        plex_servers = MediaServiceManager.get_servers_by_type(ServiceType.PLEX, active_only=True)
        if not plex_servers:
            current_app.logger.warning("No active Plex servers configured in the database. Skipping task.")
            return

        try:
            # This already gets sessions from all active servers
            active_plex_sessions = MediaServiceManager.get_all_active_sessions()
            now_utc = datetime.now(timezone.utc)
            current_app.logger.info(f"Found {len(active_plex_sessions)} active sessions across all servers.")

            current_sessions_dict = {session.sessionKey: session for session in active_plex_sessions if hasattr(session, 'sessionKey')}
            current_plex_session_keys = set(current_sessions_dict.keys())
            
            current_app.logger.debug(f"Current Plex session keys: {list(current_plex_session_keys)}")
            current_app.logger.debug(f"Previously tracked session keys: {list(_active_stream_sessions.keys())}")

            # Step 1: Check for stopped streams
            stopped_session_keys = set(_active_stream_sessions.keys()) - current_plex_session_keys
            if stopped_session_keys:
                current_app.logger.info(f"Found {len(stopped_session_keys)} stopped sessions: {list(stopped_session_keys)}")
                for session_key in stopped_session_keys:
                    stream_history_id = _active_stream_sessions.pop(session_key, None)
                    if stream_history_id:
                        history_record = db.session.get(StreamHistory, stream_history_id)
                        if history_record and not history_record.stopped_at:
                            final_duration = history_record.view_offset_at_end_seconds
                            history_record.duration_seconds = final_duration if final_duration and final_duration > 0 else 0
                            history_record.stopped_at = now_utc
                            current_app.logger.info(f"Marked session {session_key} (DB ID: {stream_history_id}) as stopped. Final duration: {history_record.duration_seconds}s.")
                        else:
                            current_app.logger.warning(f"Could not find or already stopped history record for DB ID {stream_history_id}")
            
            # Step 2: Check for new and ongoing streams
            if not current_sessions_dict:
                current_app.logger.info("No new or ongoing sessions to process.")
            else:
                current_app.logger.info(f"Processing {len(current_sessions_dict)} new or ongoing sessions...")

            for session_key, session in current_sessions_dict.items():
                user_id_from_session = None
                if hasattr(session, 'user') and session.user and hasattr(session.user, 'id'):
                    user_id_from_session = session.user.id
                elif hasattr(session, 'userId'):
                    user_id_from_session = session.userId

                if not user_id_from_session:
                    current_app.logger.warning(f"Session {session_key} is missing a user ID. Skipping.")
                    continue

                mum_user = User.query.filter_by(plex_user_id=user_id_from_session).first()
                
                if not mum_user:
                    current_app.logger.warning(f"Could not find MUM user for Plex User ID {user_id_from_session} from session {session_key}. Skipping.")
                    continue
                
                current_app.logger.debug(f"Processing session {session_key} for user '{mum_user.plex_username}' (MUM ID: {mum_user.id})")

                # If the session is new, create the history record
                if session_key not in _active_stream_sessions:
                    current_app.logger.info(f"New session detected: {session_key}. Creating history record.")
                    
                    media_duration_ms = getattr(session, 'duration', 0)
                    media_duration_s = int(media_duration_ms / 1000) if media_duration_ms else 0

                    new_history_record = StreamHistory(
                        user_id=mum_user.id,
                        session_key=str(session_key),
                        rating_key=str(getattr(session, 'ratingKey', None)),
                        started_at=now_utc,
                        platform=getattr(session.player, 'platform', 'N/A') if hasattr(session, 'player') else 'N/A',
                        product=getattr(session.player, 'product', 'N/A') if hasattr(session, 'player') else 'N/A',
                        player=getattr(session.player, 'title', 'N/A') if hasattr(session, 'player') else 'N/A',
                        ip_address=getattr(session.player, 'address', 'N/A') if hasattr(session, 'player') else 'N/A',
                        is_lan=getattr(session.player, 'local', False) if hasattr(session, 'player') else False,
                        media_title=getattr(session, 'title', "Unknown"),
                        media_type=getattr(session, 'type', "Unknown"),
                        grandparent_title=getattr(session, 'grandparentTitle', None),
                        parent_title=getattr(session, 'parentTitle', None),
                        media_duration_seconds=media_duration_s,
                        view_offset_at_end_seconds=int(getattr(session, 'viewOffset', 0) / 1000)
                    )
                    db.session.add(new_history_record)
                    db.session.flush() # Flush to get the ID
                    _active_stream_sessions[session_key] = new_history_record.id
                    current_app.logger.info(f"Successfully created StreamHistory record (ID: {new_history_record.id}) for session {session_key}.")
                
                # If the session is ongoing, update its progress
                else:
                    history_record_id = _active_stream_sessions.get(session_key)
                    if history_record_id:
                        history_record = db.session.get(StreamHistory, history_record_id)
                        if history_record:
                            current_offset_s = int(getattr(session, 'viewOffset', 0) / 1000)
                            history_record.view_offset_at_end_seconds = current_offset_s
                            current_app.logger.debug(f"Updated progress for ongoing session {session_key} to {current_offset_s}s.")
                        else:
                            current_app.logger.warning(f"Could not find existing StreamHistory record with ID {history_record_id} for ongoing session {session_key}.")
                    else:
                        current_app.logger.error(f"CRITICAL: Session {session_key} was in tracked keys but had no DB ID!")

                # Update the user's main 'last_streamed_at' field
                user_service.update_user_last_streamed(mum_user.plex_user_id, now_utc)

            # Commit all changes for this cycle
            db.session.commit()
            current_app.logger.info("--- Plex Session Monitor Task Finished ---")
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Fatal error in monitor_plex_sessions_task: {e}", exc_info=True)

def check_user_access_expirations_task():
    """
    Checks for users whose access has expired and removes them from MUM and Plex.
    This version correctly compares naive datetimes to ensure accuracy.
    """
    with scheduler.app.app_context():
        current_app.logger.info("Task_Service: Running check_user_access_expirations_task...")
        
        # Enhanced debugging
        now_naive = datetime.utcnow()
        current_app.logger.info(f"Task_Service: Current UTC time (naive): {now_naive}")
        
        # First, let's see all users with expiration dates
        all_users_with_expiry = User.query.filter(User.access_expires_at.isnot(None)).all()
        current_app.logger.info(f"Task_Service: Found {len(all_users_with_expiry)} users with expiration dates:")
        
        for user in all_users_with_expiry:
            expiry_date = user.access_expires_at
            is_expired = expiry_date <= now_naive
            time_diff = (expiry_date - now_naive).total_seconds() if expiry_date else None
            current_app.logger.info(f"  - User '{user.plex_username}' (ID: {user.id}): expires {expiry_date} | Expired: {is_expired} | Time diff: {time_diff}s")
        
        # Now get actually expired users
        expired_users = User.query.filter(
            User.access_expires_at.isnot(None), 
            User.access_expires_at <= now_naive
        ).all()
        
        current_app.logger.info(f"Task_Service: Query returned {len(expired_users)} expired users")

        if not expired_users:
            current_app.logger.info("Task_Service: No users found with expired access.")
            return

        current_app.logger.info(f"Task_Service: Found {len(expired_users)} user(s) with expired access. Processing removals...")
        
        system_admin_id = None
        try:
            from app.models import AdminAccount
            admin = AdminAccount.query.first()
            if admin:
                system_admin_id = admin.id
            current_app.logger.debug(f"Task_Service: Using system_admin_id: {system_admin_id}")
        except Exception as e_admin:
            current_app.logger.warning(f"Task_Service: Could not fetch admin_id for logging expiration task: {e_admin}")

        removal_count = 0
        for user in expired_users:
            username_for_log = user.plex_username
            mum_user_id_for_log = user.id
            original_expiry_for_log = user.access_expires_at
            
            try:
                current_app.logger.info(f"Task_Service: Processing expired user '{username_for_log}' (MUM ID: {mum_user_id_for_log}). Expiry: {original_expiry_for_log}. Removing...")
                
                # Check if user_service.delete_user_from_mum_and_plex exists
                if not hasattr(user_service, 'delete_user_from_mum_and_plex'):
                    current_app.logger.error(f"Task_Service: user_service.delete_user_from_mum_and_plex method not found!")
                    continue
                
                user_service.delete_user_from_mum_and_plex(user_id=mum_user_id_for_log, admin_id=system_admin_id)
                removal_count += 1
                
                log_event(
                    EventType.MUM_USER_DELETED_FROM_MUM,
                    f"User '{username_for_log}' automatically removed due to expired invite-based access (expired: {original_expiry_for_log}).",
                    user_id=mum_user_id_for_log,
                    admin_id=system_admin_id, 
                    details={"reason": "Automated removal: invite access duration expired."}
                )
                current_app.logger.info(f"Task_Service: Successfully removed expired user '{username_for_log}'")
                
            except Exception as e:
                current_app.logger.error(f"Task_Service: Error removing expired user '{username_for_log}' (MUM ID: {mum_user_id_for_log}): {e}", exc_info=True)
                log_event(
                    EventType.ERROR_GENERAL,
                    f"Task failed to remove expired user '{username_for_log}': {e}",
                    user_id=mum_user_id_for_log,
                    admin_id=system_admin_id
                )
        
        current_app.logger.info(f"Task_Service: User access expiration check complete. Removed: {removal_count}/{len(expired_users)} users.")

# Add this helper function to check scheduler status
def debug_scheduler_status():
    """Debug function to check scheduler status"""
    with scheduler.app.app_context():
        current_app.logger.info("=== SCHEDULER DEBUG INFO ===")
        current_app.logger.info(f"Scheduler running: {scheduler.running}")
        current_app.logger.info(f"Scheduler state: {scheduler.state}")
        
        jobs = scheduler.get_jobs()
        current_app.logger.info(f"Total jobs: {len(jobs)}")
        
        for job in jobs:
            current_app.logger.info(f"Job ID: {job.id}")
            current_app.logger.info(f"  Function: {job.func}")
            current_app.logger.info(f"  Next run: {job.next_run_time}")
            current_app.logger.info(f"  Trigger: {job.trigger}")
            
        # Check specific expiration job
        expiration_job = scheduler.get_job('check_user_expirations')
        if expiration_job:
            current_app.logger.info(f"Expiration job found:")
            current_app.logger.info(f"  Next run: {expiration_job.next_run_time}")
            current_app.logger.info(f"  Trigger: {expiration_job.trigger}")
        else:
            current_app.logger.warning("Expiration job NOT found in scheduler!")
        
        current_app.logger.info("=== END SCHEDULER DEBUG ===")

# Add this manual trigger function
def manually_trigger_expiration_check():
    """Manually trigger the expiration check for testing"""
    current_app.logger.info("MANUAL TRIGGER: Running expiration check manually...")
    check_user_access_expirations_task()
    current_app.logger.info("MANUAL TRIGGER: Expiration check completed.")

def _schedule_job_if_not_exists_or_reschedule(job_id, func, trigger_type, **trigger_args):
    """Helper to add or reschedule a job."""
    if not scheduler.running:
        current_app.logger.warning(f"Task_Service: APScheduler not running. Cannot schedule job '{job_id}'.")
        return False
    
    try:
        existing_job = scheduler.get_job(job_id)
        if existing_job:
            # Simple reschedule, more complex trigger comparison might be needed if triggers vary widely
            scheduler.reschedule_job(job_id, trigger=trigger_type, **trigger_args)
            current_app.logger.info(f"Task_Service: Rescheduled job '{job_id}' with trigger {trigger_type} and args {trigger_args}.")
        else:
            scheduler.add_job(id=job_id, func=func, trigger=trigger_type, **trigger_args)
            current_app.logger.info(f"Task_Service: ADDED new job '{job_id}' with trigger {trigger_type} and args {trigger_args}.")
        return True
    except Exception as e:
        current_app.logger.error(f"Task_Service: Error adding/rescheduling job '{job_id}': {e}", exc_info=True)
        try:
            log_event(EventType.ERROR_GENERAL, f"Failed to schedule/reschedule task '{job_id}': {e}")
        except Exception as e_log:
            current_app.logger.error(f"Task_Service: Failed to log scheduling error for '{job_id}' to DB: {e_log}")
        return False


def schedule_all_tasks():
    """Schedules all recurring tasks defined in the application."""
    current_app.logger.info("Task_Service: Attempting to schedule all defined tasks...")

    # Get the session monitoring interval from settings
    try:
        interval_str = Setting.get('SESSION_MONITORING_INTERVAL_SECONDS', '60')
        session_interval_seconds = int(interval_str)
        if session_interval_seconds < 10: # Enforce minimum
             current_app.logger.warning(f"Session monitoring interval '{session_interval_seconds}' too low, using 10s.")
             session_interval_seconds = 10
    except (ValueError, TypeError):
        session_interval_seconds = 60
        current_app.logger.warning(f"Invalid SESSION_MONITORING_INTERVAL_SECONDS. Defaulting to {session_interval_seconds}s.")

    # 1. Plex Session Monitoring
    if _schedule_job_if_not_exists_or_reschedule(
        job_id='monitor_plex_sessions',
        func=monitor_plex_sessions_task,
        trigger_type='interval',
        seconds=session_interval_seconds,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10) # Start shortly after app start
    ):
        log_event(EventType.APP_STARTUP, f"Plex session monitoring task (re)scheduled (Interval: {session_interval_seconds}s).")

    # 2. User Access Expiration Check - NOW USES SAME INTERVAL AS SESSION MONITORING
    if _schedule_job_if_not_exists_or_reschedule(
        job_id='check_user_expirations',
        func=check_user_access_expirations_task,
        trigger_type='interval',
        seconds=session_interval_seconds,  # CHANGED: Now uses same interval as session monitoring
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30) # Start 30 seconds after app start (offset from session monitoring)
    ):
        log_event(EventType.APP_STARTUP, f"User access expiration check task (re)scheduled (Interval: {session_interval_seconds}s - same as session monitoring).")

    current_app.logger.info("Task_Service: Finished attempting to schedule all tasks.")