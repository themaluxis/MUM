# File: app/services/history_service.py
from flask import current_app
from app.models import HistoryLog, EventType
from app.extensions import db
from app.utils.helpers import log_event # For logging the clear action itself

def clear_history_logs(event_types_to_clear: list[str] = None, admin_id: int = None):
    """
    Clears history logs.
    If event_types_to_clear is provided and not empty, only logs of those types are cleared.
    Otherwise, all history logs are cleared.
    """
    query = HistoryLog.query
    cleared_count = 0
    action_details = {}

    if event_types_to_clear: # If a specific list of event type names is given
        # Convert string names to EventType enum members
        valid_event_enums_to_clear = []
        invalid_type_names = []
        for type_name in event_types_to_clear:
            try:
                valid_event_enums_to_clear.append(EventType[type_name])
            except KeyError:
                invalid_type_names.append(type_name)
        
        if invalid_type_names:
            current_app.logger.warning(f"History_Service.py - clear_history_logs(): Invalid event type names received: {invalid_type_names}")
        
        if valid_event_enums_to_clear:
            query = query.filter(HistoryLog.event_type.in_(valid_event_enums_to_clear))
            action_details['cleared_event_types'] = [e.name for e in valid_event_enums_to_clear]
            current_app.logger.info(f"History_Service.py - clear_history_logs(): Clearing specific event types: {[e.name for e in valid_event_enums_to_clear]}")
        else: # No valid event types provided, so don't clear anything if specific types were intended
            current_app.logger.warning("History_Service.py - clear_history_logs(): No valid event types provided to clear. No logs will be deleted.")
            return 0 # Or raise an error / return a specific message

    else: # Clear all logs
        action_details['cleared_event_types'] = 'ALL'
        current_app.logger.info("History_Service.py - clear_history_logs(): Clearing ALL event types.")

    try:
        # Perform the delete operation
        # For query.delete() to work efficiently, it should not have joins or complex conditions
        # that SQLAlchemy can't translate to a simple DELETE FROM ... WHERE.
        # Our current query should be fine.
        cleared_count = query.delete(synchronize_session=False) # False is usually safer for bulk deletes
        db.session.commit()
        
        log_message = f"Cleared {cleared_count} history log entries."
        if event_types_to_clear and valid_event_enums_to_clear:
            log_message += f" (Types: {[e.name for e in valid_event_enums_to_clear]})"
        elif not event_types_to_clear:
             log_message += " (All types)"

        log_event(EventType.SETTING_CHANGE, # Or a new EventType like HISTORY_CLEARED
                  log_message,
                  admin_id=admin_id,
                  details=action_details)
        current_app.logger.info(f"History_Service.py - clear_history_logs(): {log_message}")
        return cleared_count
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"History_Service.py - clear_history_logs(): Error clearing history logs: {e}", exc_info=True)
        # Optionally log this error to history as well, or raise it
        log_event(EventType.ERROR_GENERAL, f"Failed to clear history logs: {e}", admin_id=admin_id, details={"error": str(e)})
        raise # Re-raise so the route can catch it and flash an error