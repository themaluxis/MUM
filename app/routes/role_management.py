# File: app/routes/role_management.py
from flask import (
    Blueprint, render_template, redirect, url_for, 
    flash, request, current_app, make_response
)
from flask_login import login_required, current_user
from app.models import Owner, UserAppAccess, Role
from app.forms import RoleEditForm, RoleCreateForm, RoleMemberForm
from app.extensions import db
from app.utils.helpers import setup_required, permission_required, any_permission_required
import json

bp = Blueprint('role_management', __name__)

@bp.route('/')
@login_required
@any_permission_required(['create_role', 'edit_role', 'delete_role'])
def index():
    roles = Role.query.order_by(Role.name).all()
    return render_template(
        'settings/index.html',
        title="Manage Roles",
        roles=roles,
        active_tab='roles'
    )

@bp.route('/create', methods=['GET', 'POST'])
@login_required
@permission_required('create_role')
def create():
    form = RoleCreateForm()
    if form.validate_on_submit():
        new_role = Role(
            name=form.name.data,
            description=form.description.data,
            color=form.color.data,
            icon=form.icon.data.strip()
        )
        db.session.add(new_role)
        db.session.flush()  # Flush to get the ID before commit
        role_id = new_role.id  # Store the ID
        db.session.commit()
        
        flash(f"Role '{new_role.name}' created successfully. You can now set its permissions.", "success")
        # Redirect to the 'edit' page for the newly created role
        return redirect(url_for('role_management.edit', role_id=role_id))

    # The GET request rendering remains the same, but the template it renders will be changed.
    return render_template(
        'roles/create.html',
        title="Create New Role",
        form=form,
        active_tab='roles' # Keep 'roles' highlighted in the main settings sidebar
    )

@bp.route('/edit/<int:role_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_role')
def edit(role_id):
    tab = request.args.get('tab', 'display')
    role = Role.query.get_or_404(role_id)
    form = RoleEditForm(original_name=role.name, obj=role)
    member_form = RoleMemberForm()

    if current_user.id != 1 and current_user in role.admins:
        flash("You cannot edit a role you are currently assigned to.", "danger")
        return redirect(url_for('role_management.index'))

    # --- Define the hierarchical permission structure ---
    permissions_structure = {
        'Users': {
            'label': 'Users',
            'children': {
                'view_user': {'label': 'View User', 'description': 'Can view user profile.'},
                'edit_user': {'label': 'Edit User', 'description': 'Can edit user details, notes, whitelists, and library access.'},
                'delete_user': {'label': 'Delete User', 'description': 'Can permanently remove users from MUM and the Plex server.'},
                'purge_users': {'label': 'Purge Users', 'description': 'Can use the inactivity purge feature.'},
                'mass_edit_users': {'label': 'Mass Edit Users', 'description': 'Can perform bulk actions like assigning libraries or whitelisting.'},
            }
        },
        'Invites': {
            'label': 'Invites',
            'children': {
                'create_invites': {'label': 'Create Invites', 'description': 'Can create new invite links.'},
                'delete_invites': {'label': 'Delete Invites', 'description': 'Can delete existing invite links.'},
                'edit_invites': {'label': 'Edit Invites', 'description': 'Can modify settings for existing invites.'},
            }
        },
        'Admins': { 
            'label': 'Admins & Roles', 
            'children': {
                'view_admins_tab': {'label': 'View Admin Management Section', 'description': 'Allows user to see the "Admins" and "Roles" tabs in settings.'},
                'create_admin':    {'label': 'Create Admin', 'description': 'Can create new administrator accounts.'},
                'edit_admin':      {'label': 'Edit Admin', 'description': 'Can edit other administrators. (roles, reset password etc.)'},
                'delete_admin':    {'label': 'Delete Admin', 'description': 'Can delete other non-primary administrators.'},
                'create_role':     {'label': 'Create Role', 'description': 'Can create new administrator roles.'},
                'edit_role':       {'label': 'Edit Role Permissions', 'description': 'Can edit a role\'s name, color, and permissions.'},
                'delete_role':     {'label': 'Delete Roles', 'description': 'Can delete roles that are not in use.'},
            }
        },
        'Streams': {
            'label': 'Streams',
            'children': {
                'view_streaming': {'label': 'View Streams', 'description': 'Can access the "Active Streams" page.'},
                'kill_stream': {'label': 'Terminate Stream', 'description': 'Can stop a user\'s active stream.'},
            }
        },
        'EventLogs': {
            'label': 'Application Logs',
            'children': {
                 'view_logs': {'label': 'View Application Logs', 'description': 'Can access the full "Application Logs" page in settings.'},
                 'clear_logs': {'label': 'Clear Application Logs', 'description': 'Can erase the full "Application Logs".'},
            }
        },
        'Libraries': {
            'label': 'Libraries',
            'children': {
                'view_libraries': {'label': 'View Libraries', 'description': 'Can access the libraries page and view library information.'},
                'sync_libraries': {'label': 'Sync Libraries', 'description': 'Can synchronize libraries from media servers.'},
            }
        },
        'AppSettings': {
            'label': 'App Settings',
            'children': {
                'manage_general_settings': {'label': 'Manage General', 'description': 'Can change the application name and base URL.'},
                'manage_plex_settings': {'label': 'Manage Plex', 'description': 'Can change the Plex server connection details.'},
                'manage_discord_settings': {'label': 'Manage Discord', 'description': 'Can change Discord OAuth, Bot, and feature settings.'},
                'manage_plugins': {'label': 'Manage Plugins', 'description': 'Can enable/disable plugins.'},
                'manage_advanced_settings' : {'label': 'Manage Advanced', 'description': 'Can access and manage advanced settings page.'},
            }
        }
    }

    # Flatten the structure to populate the form's choices
    all_permission_choices = []
    for category_data in permissions_structure.values():
        for p_key, p_label in category_data.get('children', {}).items():
            all_permission_choices.append((p_key, p_label))
    form.permissions.choices = all_permission_choices
    
    # Populate choices for the 'Add Members' modal form
    users_not_in_role = UserAppAccess.query.filter(
        ~UserAppAccess.roles.any(id=role.id)
    ).order_by(UserAppAccess.username).all()
    member_form.admins_to_add.choices = [(u.id, u.username) for u in users_not_in_role]

    # Handle form submissions from different tabs
    if request.method == 'POST':
        if 'submit_display' in request.form and form.validate():
            role.name = form.name.data
            role.description = form.description.data
            role.color = form.color.data
            role.icon = form.icon.data.strip()
            db.session.commit()
            flash(f"Display settings for role '{role.name}' updated.", "success")
            return redirect(url_for('role_management.edit', role_id=role_id, tab='display'))
        
        elif 'submit_permissions' in request.form and form.validate():
            # The form.permissions.data will correctly contain all checked permissions
            role.permissions = form.permissions.data
            db.session.commit()
            flash(f"Permissions for role '{role.name}' updated.", "success")
            return redirect(url_for('role_management.edit', role_id=role_id, tab='permissions'))
            
        elif 'submit_add_members' in request.form and member_form.validate_on_submit():
            users_to_add = UserAppAccess.query.filter(UserAppAccess.id.in_(member_form.admins_to_add.data)).all()
            if users_to_add:
                for user in users_to_add:
                    if user not in role.user_app_access:
                        role.user_app_access.append(user)
                db.session.commit()
                
                # On SUCCESS, send back a trigger for a toast and a list refresh
                toast = {"showToastEvent": {"message": f"Added {len(users_to_add)} member(s) to role '{role.name}'.", "category": "success"}}
                # Create an empty 204 response because we don't need to swap any content
                response = make_response("", 204)
                # Set the header that HTMX and our JS will listen for
                response.headers['HX-Trigger'] = json.dumps({"refreshMembersList": True, **toast})
                return response

            else:
                # User submitted the form without selecting anyone
                toast = {"showToastEvent": {"message": "No members were selected to be added.", "category": "info"}}
                response = make_response("", 204)
                response.headers['HX-Trigger'] = json.dumps(toast)
                return response

    # Populate form for GET request
    if request.method == 'GET' and tab == 'permissions':
        form.permissions.data = role.permissions

    return render_template(
        'settings/index.html',
        title=f"Edit Role: {role.name}",
        role=role,
        edit_form=form,
        form=form,
        member_form=member_form,
        current_members=role.user_app_access,
        permissions_structure=permissions_structure, # Pass the hierarchy
        active_tab='roles_edit', 
        active_role_tab=tab 
    )

@bp.route('/edit/<int:role_id>/remove_member/<int:admin_id>', methods=['POST'])
@login_required
@permission_required('edit_role')
def remove_member(role_id, admin_id):
    role = Role.query.get_or_404(role_id)
    user = UserAppAccess.query.get_or_404(admin_id)
    if user in role.user_app_access:
        role.user_app_access.remove(user)
        db.session.commit()
        flash(f"Removed '{user.username}' from role '{role.name}'.", "success")
    # Redirect back to the members tab
    return redirect(url_for('role_management.edit', role_id=role.id, tab='members'))

@bp.route('/delete/<int:role_id>', methods=['POST'])
@login_required
@permission_required('delete_role')
def delete(role_id):
    role = Role.query.get_or_404(role_id)

    # Prevent deletion if current user is assigned to this role (unless Owner)
    if isinstance(current_user, UserAppAccess) and current_user in role.user_app_access:
        flash("You cannot delete a role you are currently assigned to.", "danger")
        return redirect(url_for('role_management.index'))
    
    if role.user_app_access:
        flash(f"Cannot delete role '{role.name}' as it is currently assigned to one or more users.", "danger")
        return redirect(url_for('role_management.index'))
    
    db.session.delete(role)
    db.session.commit()
    flash(f"Role '{role.name}' deleted.", "success")
    return redirect(url_for('role_management.index'))