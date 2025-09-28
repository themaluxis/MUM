# File: app/routes/users_modules/history.py
"""General history viewing functionality"""

from flask import render_template, request
from flask_login import login_required
from sqlalchemy.orm import joinedload
from app.models import User
from app.models_media_services import MediaStreamHistory
from app.utils.helpers import permission_required, setup_required
from . import users_bp


@users_bp.route('/history')
@login_required
@setup_required
@permission_required('view_users')
def general_history():
    """Display a general history of all streams from all users."""
    page = request.args.get('page', 1, type=int)

    # Query all media stream history, ordered by most recent.
    # Eagerly load related user and server info to prevent N+1 queries in the template.
    history_query = MediaStreamHistory.query.options(
        joinedload(MediaStreamHistory.user),
        joinedload(MediaStreamHistory.server)
    ).order_by(MediaStreamHistory.started_at.desc())

    history_pagination = history_query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'users/history.html',
        title="General History",
        history_logs=history_pagination,
        active_tab='history'
    )