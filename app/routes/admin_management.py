# File: app/routes/admin_management.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, make_response
)
from flask_login import login_required, current_user
from app.models import AdminAccount, Role, EventType
from app.forms import AdminCreateForm, AdminEditForm, AdminResetPasswordForm
from app.extensions import db
from app.utils.helpers import log_event, setup_required, permission_required, any_permission_required
import json

bp = Blueprint('admin_management', __name__)

@bp.route('/')
@login_required
@any_permission_required(['create_admin', 'edit_admin', 'delete_admin'])
def index():
    admins = AdminAccount.query.order_by(AdminAccount.id).all()
    return render_template(
        'settings/index.html',
        title="Manage Admins",
        admins=admins,
        active_tab='admins'
    )

@bp.route('/create', methods=['POST'])
@login_required
@permission_required('create_admin')
def create():
    form = AdminCreateForm()
    if form.validate_on_submit():
        new_admin = AdminAccount(
            username=form.username.data,
            force_password_change=True,
            roles=[] # New admins start with no explicit permissions/roles
        )
        new_admin.set_password(form.password.data)
        db.session.add(new_admin)
        db.session.commit()
        
        toast = {"showToastEvent": {"message": f"Admin '{new_admin.username}' created.", "category": "success"}}
        response = make_response("", 204) # No Content
        response.headers['HX-Trigger'] = json.dumps({"refreshAdminList": True, **toast})
        return response
    
    # If validation fails, re-render the form partial with errors
    return render_template('admin/partials/create_admin_modal.html', form=form), 422

@bp.route('/create_form')
@login_required
@permission_required('create_admin')
def create_form():
    form = AdminCreateForm()
    return render_template('admin/partials/create_admin_modal.html', form=form)

@bp.route('/delete/<int:admin_id>', methods=['POST'])
@login_required
@permission_required('delete_admin')
def delete(admin_id):
    if admin_id == 1 or admin_id == current_user.id:
        flash("The primary admin or your own account cannot be deleted.", "danger")
        return redirect(url_for('admin_management.index'))
    
    admin_to_delete = AdminAccount.query.get_or_404(admin_id)
    db.session.delete(admin_to_delete)
    db.session.commit()
    flash(f"Admin '{admin_to_delete.username}' has been deleted.", "success")
    return redirect(url_for('admin_management.index'))

@bp.route('/edit/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def edit(admin_id):
    admin = AdminAccount.query.get_or_404(admin_id)

    if admin.id == 1:
        flash("The primary admin's roles and permissions cannot be edited.", "warning")
        return redirect(url_for('admin_management.index'))
    
    if admin_id == current_user.id:
        flash("To manage your own account, please use the 'My Account' page.", "info")
        return redirect(url_for('settings.account'))
        
    form = AdminEditForm(obj=admin)
    form.roles.choices = [(r.id, r.name) for r in Role.query.order_by('name')]

    if form.validate_on_submit():
        admin.roles = Role.query.filter(Role.id.in_(form.roles.data)).all()
        db.session.commit()
        flash(f"Roles for '{admin.username or admin.plex_username}' updated.", "success")
        return redirect(url_for('admin_management.index'))
        
    if request.method == 'GET':
        form.roles.data = [r.id for r in admin.roles]

    return render_template(
        'admin/edit.html',
        title="Edit Admin",
        admin=admin,
        form=form,
        active_tab='admins'
    )

@bp.route('/reset_password/<int:admin_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_admin')
def reset_password(admin_id):
    admin = AdminAccount.query.get_or_404(admin_id)
    if admin.id == 1 or admin.id == current_user.id:
        flash("You cannot reset the password for the primary admin or yourself.", "danger")
        return redirect(url_for('admin_management.edit', admin_id=admin_id))
    
    form = AdminResetPasswordForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            admin.set_password(form.new_password.data)
            admin.force_password_change = True # Force change on next login
            db.session.commit()
            
            log_event(EventType.ADMIN_PASSWORD_CHANGE, f"Password was reset for admin '{admin.username}'.", admin_id=current_user.id)
            toast = {"showToastEvent": {"message": "Password has been reset.", "category": "success"}}
            response = make_response("", 204)
            response.headers['HX-Trigger'] = json.dumps(toast)
            return response
        else:
            # Re-render form with validation errors for HTMX
            return render_template('admin/partials/reset_password_modal.html', form=form, admin=admin), 422
    
    # For GET request, just render the form
    return render_template('admin/partials/reset_password_modal.html', form=form, admin=admin)