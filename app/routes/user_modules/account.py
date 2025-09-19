# File: app/routes/user_modules/account.py
"""User account management and settings functionality"""

from flask import render_template, redirect, url_for, flash, request, current_app, make_response, abort
from flask_login import login_required, current_user
from datetime import datetime
from app.models import UserAppAccess, Owner, EventType
from app.models_media_services import UserMediaAccess
from app.extensions import db
from app.utils.helpers import permission_required, log_event
from . import user_bp
import urllib.parse
import json


@user_bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    """User account management page - similar to admin account page"""
    # Ensure this is a regular user, not an owner
    if isinstance(current_user, Owner):
        return redirect(url_for('dashboard.account'))
    
    # Ensure this is a UserAppAccess (user account)
    if not isinstance(current_user, UserAppAccess):
        flash('Access denied. Please log in with a valid user account.', 'danger')
        return redirect(url_for('auth.app_login'))
    
    from app.forms import ChangePasswordForm, TimezonePreferenceForm
    from app.models import UserPreferences
    
    # Initialize forms
    change_password_form = ChangePasswordForm()
    timezone_form = TimezonePreferenceForm()
    
    # Get user preferences (create if doesn't exist)
    # Note: UserPreferences is designed for admin_id, but we'll adapt it
    user_prefs = UserPreferences.get_timezone_preference(current_user.id)
    timezone_form.timezone_preference.data = user_prefs.get('preference', 'local')
    timezone_form.time_format.data = user_prefs.get('time_format', '12')
    
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'change_password' and change_password_form.validate_on_submit():
            # Verify current password
            if not current_user.check_password(change_password_form.current_password.data):
                flash('Current password is incorrect.', 'danger')
            else:
                # Update password
                current_user.set_password(change_password_form.new_password.data)
                current_user.updated_at = datetime.utcnow()
                db.session.commit()
                
                log_event(EventType.SETTING_CHANGE, f"Password was changed for user '{current_user.get_display_name()}'.", admin_id=current_user.id)
                flash('Password has been updated successfully.', 'success')
                return redirect(url_for('user.account'))
        
        elif form_type == 'timezone' and timezone_form.validate_on_submit():
            # Update timezone preferences
            UserPreferences.set_timezone_preference(
                current_user.id,
                timezone_form.timezone_preference.data,
                timezone_form.time_format.data
            )
            
            log_event(EventType.SETTING_CHANGE, f"Timezone preferences updated for user '{current_user.get_display_name()}'.", admin_id=current_user.id)
            flash('Timezone preferences have been updated successfully.', 'success')
            return redirect(url_for('user.account'))
    
    return render_template('account/user/index.html',
                         title="Account Settings",
                         change_password_form=change_password_form,
                         timezone_form=timezone_form)


@user_bp.route('/app_user/<username>/reset_password', methods=['GET', 'POST'])
@login_required
@permission_required('edit_user')
def reset_app_user_password(username):
    """Reset password for an app user (admin only)"""
    # URL decode the username to handle special characters
    try:
        username = urllib.parse.unquote(username)
    except Exception as e:
        current_app.logger.warning(f"Error decoding username parameter: {e}")
        abort(400)
    
    # Validate username
    if not username:
        abort(400)
    
    user_app_access = UserAppAccess.query.filter_by(username=username).first_or_404()
    
    from app.forms import UserResetPasswordForm
    form = UserResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            user_app_access.set_password(form.new_password.data)
            user_app_access.updated_at = datetime.utcnow()
            db.session.commit()
            
            log_event(EventType.SETTING_CHANGE, f"Password was reset for app user '{user_app_access.get_display_name()}'.", admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset successfully.", "category": "success"}}
            
            return make_response("<!-- success -->", 200, {'HX-Trigger': json.dumps(toast)})
        else:
            # Return form with errors
            return render_template('user/partials/modals/reset_password_modal.html', 
                                 form=form, 
                                 user=user_app_access)
    
    # GET request - render the form
    return render_template('user/partials/modals/reset_password_modal.html', 
                         form=form, 
                         user=user_app_access)


@user_bp.route('/<username>/reset_password', methods=['GET', 'POST'])
@user_bp.route('/<server_nickname>/<server_username>/reset_password', methods=['GET', 'POST'])
@login_required
@permission_required('edit_user')
def reset_password(username=None, server_nickname=None, server_username=None):
    """Reset password for users - handles both local and service users"""
    # URL decode parameters to handle special characters
    if username:
        try:
            username = urllib.parse.unquote(username)
        except Exception as e:
            current_app.logger.warning(f"Error decoding username parameter: {e}")
            abort(400)
    
    if server_nickname and server_username:
        try:
            server_nickname = urllib.parse.unquote(server_nickname)
            server_username = urllib.parse.unquote(server_username)
        except Exception as e:
            current_app.logger.warning(f"Error decoding URL parameters: {e}")
            abort(400)
    
    # Check for potential username conflicts with server nicknames
    if username:
        from app.models_media_services import MediaServer
        server_conflict = MediaServer.query.filter_by(server_nickname=username).first()
        if server_conflict:
            current_app.logger.warning(f"Potential conflict: app username '{username}' matches server nickname")
        
        user_app_access = UserAppAccess.query.filter_by(username=username).first_or_404()
    
    elif server_nickname and server_username:
        # This is for service users linked to local accounts
        from app.models_media_services import MediaServer
        
        # Check for potential username conflicts with app users
        user_conflict = UserAppAccess.query.filter_by(username=server_nickname).first()
        if user_conflict:
            current_app.logger.warning(f"Potential conflict: server nickname '{server_nickname}' matches app user username")
        
        server = MediaServer.query.filter_by(server_nickname=server_nickname).first_or_404()
        
        # Find the service account by server and username
        access = UserMediaAccess.query.filter_by(
            server_id=server.id,
            external_username=server_username
        ).first_or_404()
        
        # Get the linked local user if exists
        if access.user_app_access_id:
            user_app_access = UserAppAccess.query.get_or_404(access.user_app_access_id)
        else:
            flash('This service account is not linked to a local user account and cannot have its password reset.', 'warning')
            return redirect(request.referrer or url_for('users.list_users'))
    
    else:
        current_app.logger.error("Invalid parameters for reset_password route")
        abort(400)
    
    from app.forms import UserResetPasswordForm
    form = UserResetPasswordForm()
    
    if request.method == 'POST':
        if form.validate_on_submit():
            user_app_access.set_password(form.new_password.data)
            user_app_access.updated_at = datetime.utcnow()
            db.session.commit()
            
            log_event(EventType.SETTING_CHANGE, f"Password was reset for user '{user_app_access.get_display_name()}'.", admin_id=current_user.id)
            
            toast = {"showToastEvent": {"message": "Password has been reset successfully.", "category": "success"}}
            
            return make_response("<!-- success -->", 200, {'HX-Trigger': json.dumps(toast)})
        else:
            # Return form with errors for HTMX
            return render_template('user/partials/modals/reset_password_modal.html', 
                                 form=form, 
                                 user=user_app_access)
    
    # GET request - render the modal form
    return render_template('user/partials/modals/reset_password_modal.html', 
                         form=form, 
                         user=user_app_access)