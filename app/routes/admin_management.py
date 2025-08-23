# File: app/routes/admin_management.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, make_response
)
from flask_login import login_required, current_user
from app.models import Owner, UserAppAccess, Role, EventType
from app.forms import UserAppAccessCreateForm, UserAppAccessEditForm, UserAppAccessResetPasswordForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required, any_permission_required
import json

bp = Blueprint('admin_management', __name__)

@bp.route('/')
@login_required
@any_permission_required(['create_admin', 'edit_admin', 'delete_admin'])
def index():
    # Get Owner and UserAppAccess with admin roles
    owner = Owner.query.first()
    user_app_accesses = UserAppAccess.query.order_by(UserAppAccess.id).all()
    return render_template(
        'settings/index.html',
        title="Manage Users",
        owner=owner,
        app_users=user_app_accesses,
        active_tab='admins'
    )

@bp.route('/create', methods=['POST'])
@login_required
@permission_required('create_admin')
def create():
    form = UserAppAccessCreateForm()
    if form.validate_on_submit():
        new_user = UserAppAccess(
            username=form.username.data,
            force_password_change=True,
            roles=[] # New users start with no explicit permissions/roles
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.commit()
        
        toast = {"showToastEvent": {"message": f"User '{new_user.username}' created.", "category": "success"}}
        response = make_response("", 204) # No Content
        response.headers['HX-Trigger'] = json.dumps({"refreshAdminList": True, **toast})
        return response
    
    # If validation fails, re-render the form partial with errors
    return render_template('admin/partials/create_admin_modal.html', form=form), 422

@bp.route('/create_form')
@login_required
@permission_required('create_admin')
def create_form():
    form = UserAppAccessCreateForm()
    return render_template('admin/partials/create_admin_modal.html', form=form)

@bp.route('/delete/<int:admin_id>', methods=['POST'])
@login_required
@permission_required('delete_admin')
def delete(admin_id):
    # Prevent deletion of Owner or current user
    if admin_id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for('admin_management.index'))
    
    # Check if this is the Owner (cannot be deleted)
    if isinstance(current_user, Owner) and admin_id == current_user.id:
        flash("The Owner account cannot be deleted.", "danger")
        return redirect(url_for('admin_management.index'))
    
    user_to_delete = UserAppAccess.query.get_or_404(admin_id)
    db.session.delete(user_to_delete)
    db.session.commit()
    flash(f"User '{user_to_delete.username}' has been deleted.", "success")
    return redirect(url_for('admin_management.index'))

@bp.route('/edit/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def edit(admin_id):
    user = UserAppAccess.query.get_or_404(admin_id)

    # Prevent editing Owner account through this interface
    if isinstance(current_user, Owner) and admin_id == current_user.id:
        flash("The Owner account should be managed through the 'My Account' page.", "warning")
        return redirect(url_for('admin_management.index'))
    
    if admin_id == current_user.id:
        flash("To manage your own account, please use the 'My Account' page.", "info")
        return redirect(url_for('settings.account'))
        
    form = UserAppAccessEditForm(obj=user)
    form.roles.choices = [(r.id, r.name) for r in Role.query.order_by('name')]

    if form.validate_on_submit():
        user.roles = Role.query.filter(Role.id.in_(form.roles.data)).all()
        db.session.commit()
        flash(f"Roles for '{user.username}' updated.", "success")
        return redirect(url_for('admin_management.index'))
        
    if request.method == 'GET':
        form.roles.data = [r.id for r in user.roles]

    return render_template(
        'admin/edit.html',
        title="Edit User",
        admin=user,
        form=form,
        active_tab='admins'
    )

@bp.route('/reset_password/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def reset_password(admin_id):
    user = UserAppAccess.query.get_or_404(admin_id)
    if admin_id == current_user.id:
        flash("You cannot reset your own password through this interface.", "danger")
        return redirect(url_for('admin_management.edit', admin_id=admin_id))
    
    # Prevent resetting Owner password through this interface
    if isinstance(current_user, Owner) and admin_id == current_user.id:
        flash("Owner password should be reset through the 'My Account' page.", "danger")
        return redirect(url_for('admin_management.edit', admin_id=admin_id))
    
    form = UserAppAccessResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            user.set_password(form.new_password.data)
            user.force_password_change = True # Force change on next login
            db.session.commit()
            
            log_event(EventType.ADMIN_PASSWORD_CHANGE, f"Password was reset for user '{user.username}'.", admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('admin/partials/reset_password_modal.html', form=form, admin=user), 422
    
    # For GET request, just render the form
    return render_template('admin/partials/reset_password_modal.html', form=form, admin=user)