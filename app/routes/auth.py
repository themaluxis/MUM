# File: app/routes/auth.py
import uuid
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, g
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlsplit, urljoin, urlencode, quote as url_quote
import datetime 
from app.utils.helpers import log_event
from app.models import AdminAccount, Setting, EventType, SettingValueType 
from app.forms import LoginForm
from app.extensions import db, csrf # <<< IMPORT CSRF
from plexapi.myplex import MyPlexAccount 
from plexapi.exceptions import Unauthorized, NotFound, PlexApiException
from datetime import datetime, timezone, timedelta
from app.utils.plex_auth_helpers import create_plex_pin_login, check_plex_pin_status, get_plex_auth_url

bp = Blueprint('auth', __name__)

# Removed direct API URLs and headers function - now using plexapi helpers

def is_safe_url(target):
    host_url = urlsplit(request.host_url); redirect_url = urlsplit(urljoin(request.host_url, target))
    return redirect_url.scheme in ('http', 'https') and host_url.netloc == redirect_url.netloc

@bp.route('/login', methods=['GET', 'POST'])
def app_login():
    if current_user.is_authenticated and getattr(g, 'setup_complete', False):
        return redirect(url_for('dashboard.index'))
    
    # Check if any admin account exists at all for the setup redirect
    try:
        if not AdminAccount.query.first():
            flash('App setup not complete. Please set up an admin account.', 'warning')
            return redirect(url_for('setup.account_setup'))
    except Exception as e_db:
        current_app.logger.warning(f"Could not query AdminAccount in login: {e_db}")
        # Allow rendering the login page even if DB check fails, it will likely fail on submit anyway
    
    # The form is always prepared now.
    form = LoginForm()

    if form.validate_on_submit():
        # Find the admin by the submitted username
        admin = AdminAccount.query.filter_by(username=form.username.data).first()
        
        # Check if an admin with that username exists AND if they have a password that matches.
        # The check_password method will safely return False if password_hash is None.
        if admin and admin.check_password(form.password.data):
            login_user(admin, remember=True)
            admin.last_login_at = db.func.now()
            db.session.commit()
            log_event(EventType.ADMIN_LOGIN_SUCCESS, f"Admin '{admin.username}' logged in (password).")
            
            # Check if setup is complete before redirecting
            if not getattr(g, 'setup_complete', False):
                # Find the next required setup step
                from app.routes.setup import get_completed_steps
                completed_steps = get_completed_steps()
                if 'account' not in completed_steps:
                    return redirect(url_for('setup.account_setup'))
                if 'plugins' not in completed_steps:
                    return redirect(url_for('plugins.setup_plugins'))
                if 'app' not in completed_steps:
                    return redirect(url_for('setup.app_config'))
                return redirect(url_for('setup.discord_config'))

            next_page = request.args.get('next')
            if not next_page or not is_safe_url(next_page):
                next_page = url_for('dashboard.index')
            return redirect(next_page)
        else:
            log_event(EventType.ADMIN_LOGIN_FAIL, f"Failed login attempt for username '{form.username.data}'.")
            flash('Invalid username or password.', 'danger')
            
    # Always render the login page with both options enabled.
    return render_template('auth/login.html', title="Admin Login", form=form)

@bp.route('/plex_sso_admin', methods=['POST'])
def plex_sso_login_admin():
    # Only redirect to dashboard if already logged in AND already linked to Plex.
    # This allows a logged-in, non-linked user to proceed.
    if current_user.is_authenticated and current_user.plex_uuid and getattr(g, 'setup_complete', False):
        return redirect(url_for('dashboard.index'))

    try:
        # Use plexapi instead of direct HTTP requests
        pin_login, error_msg = create_plex_pin_login(client_identifier_suffix="AdminLogin")
        if not pin_login:
            current_app.logger.error(f"Failed to create Plex PIN: {error_msg}")
            raise Exception(f"Could not create Plex PIN: {error_msg}")

        pin_id = getattr(pin_login, 'id', getattr(pin_login, 'identifier', None))
        session['plex_pin_id_admin_login'] = pin_id
        session['plex_pin_code_admin_login'] = pin_login.pin
        # Store headers for callback recreation - only store serializable dict
        try:
            if hasattr(pin_login, '_headers'):
                headers = pin_login._headers() if callable(pin_login._headers) else pin_login._headers
                if isinstance(headers, dict):
                    session['plex_headers_admin_login'] = {k: str(v) for k, v in headers.items() if isinstance(v, (str, int, float, bool))}
                else:
                    session['plex_headers_admin_login'] = {}
            else:
                session['plex_headers_admin_login'] = {}
        except Exception as e:
            current_app.logger.warning(f"Could not store headers in session: {e}")
            session['plex_headers_admin_login'] = {}
        
        app_base_url = Setting.get('APP_BASE_URL', request.url_root.rstrip('/'))
        callback_path_segment = url_for('auth.plex_sso_callback_admin', _external=False)
        forward_url_to_our_app = f"{app_base_url.rstrip('/')}{callback_path_segment}"
        
        # Use plexapi helper to get auth URL
        auth_url_for_user_to_visit = get_plex_auth_url(pin_login, forward_url_to_our_app)
        
        # If user is already logged in, the "next page" should be their account settings.
        # Otherwise, it's a fresh login, so go to the dashboard.
        if current_user.is_authenticated:
            session['plex_admin_login_next_url'] = url_for('dashboard.settings_account')
        else:
            session['plex_admin_login_next_url'] = request.args.get('next') or url_for('dashboard.index')

        return redirect(auth_url_for_user_to_visit)
        
    except Exception as e:
        current_app.logger.error(f"Error initiating Plex PIN for admin login: {e}", exc_info=True)
        flash(f"Could not initiate Plex SSO. Error: {e}", "danger")

    # If an error occurs, send the user back to the most relevant page
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.settings_account'))
    else:
        return redirect(url_for('auth.app_login'))

@bp.route('/plex_sso_callback_admin') 
def plex_sso_callback_admin():
    pin_id_from_session = session.get('plex_pin_id_admin_login')
    pin_headers = session.get('plex_headers_admin_login', {})
    
    # Context-aware fallback URL
    fallback_url = url_for('dashboard.settings_account') if current_user.is_authenticated else url_for('auth.app_login')
    
    if not pin_id_from_session:
        flash('Plex login callback invalid or session expired.', 'danger')
        return redirect(fallback_url)
    
    try:
        # Recreate pin login object for checking status
        from plexapi.myplex import MyPlexPinLogin
        pin_login = MyPlexPinLogin(headers=pin_headers, oauth=False)
        # Restore PIN ID using safe attribute setting
        if hasattr(pin_login, 'id'):
            pin_login.id = pin_id_from_session
        elif hasattr(pin_login, 'identifier'):
            pin_login.identifier = pin_id_from_session
        pin_login.pin = session.get('plex_pin_code_admin_login')
        
        # Use plexapi helper to check PIN status
        plex_auth_token, error_msg = check_plex_pin_status(pin_login)
        
        if not plex_auth_token: 
            flash('Plex PIN not yet linked or has expired.', 'warning')
            return redirect(fallback_url)
        
        plex_account = MyPlexAccount(token=plex_auth_token)
        
        admin_to_update = None
        log_message = ""
        
        # Determine if we're linking an existing account or logging in a new one
        if current_user.is_authenticated:
            admin_to_update = AdminAccount.query.get(current_user.id)
            log_message = f"Admin '{admin_to_update.username}' linked their Plex account '{plex_account.username}'."
        else:
            admin_to_update = AdminAccount.query.filter_by(plex_uuid=plex_account.uuid).first()
            log_message = f"Admin '{plex_account.username}' logged in via Plex SSO."
        
        if not admin_to_update:
            flash(f"Plex account '{plex_account.username}' is not a configured admin.", "danger")
            return redirect(fallback_url)
        
        # Check if the returning Plex account is already assigned to a different MUM admin
        if admin_to_update.plex_uuid and admin_to_update.plex_uuid != plex_account.uuid:
             flash("This Plex account is already linked to a different admin.", "danger")
             return redirect(fallback_url)

        # Update the admin record with the latest details from Plex
        admin_to_update.plex_uuid = plex_account.uuid
        admin_to_update.plex_username = plex_account.username
        admin_to_update.plex_thumb = plex_account.thumb
        admin_to_update.email = plex_account.email
        admin_to_update.last_login_at = db.func.now()
        db.session.commit()
        
        login_user(admin_to_update, remember=True)
        log_event(EventType.ADMIN_LOGIN_SUCCESS, log_message, admin_id=admin_to_update.id)
        
        next_url = session.pop('plex_admin_login_next_url', url_for('dashboard.index'))
        if not is_safe_url(next_url):
            next_url = fallback_url
        
        # Clean up session
        session.pop('plex_pin_id_admin_login', None)
        session.pop('plex_pin_code_admin_login', None)
        session.pop('plex_headers_admin_login', None)

        return redirect(next_url)

    except PlexApiException as e_plex:
        flash(f'Plex API error: {str(e_plex)}', 'danger')
        current_app.logger.error(f"Plex admin callback PlexApiException: {e_plex}", exc_info=True)
    except Exception as e:
        current_app.logger.error(f"Error during Plex admin callback: {e}", exc_info=True)
        flash(f'An unexpected error occurred: {e}', 'danger')
    
    # Cleanup session and redirect on error
    session.pop('plex_pin_id_admin_login', None)
    session.pop('plex_pin_code_admin_login', None)
    session.pop('plex_headers_admin_login', None)
    return redirect(fallback_url)

@bp.route('/logout')
@login_required
def logout():
    admin_name = current_user.username or current_user.plex_username
    log_event(EventType.ADMIN_LOGOUT, f"Admin '{admin_name}' logged out.", admin_id=current_user.id)
    logout_user(); flash('You have been logged out.', 'success'); return redirect(url_for('auth.app_login'))

@bp.route('/logout_setup')
def logout_setup():
    # ... (same)
    if current_user.is_authenticated:
        admin_name = current_user.username or current_user.plex_username
        log_event(EventType.ADMIN_LOGOUT, f"Admin '{admin_name}' logged out during setup.", admin_id=current_user.id)
        logout_user()
    session.clear(); flash('Logged out of setup.', 'info'); return redirect(url_for('setup.account_setup'))

DISCORD_API_BASE_URL = 'https://discord.com/api/v10'

@bp.route('/discord/link_admin', methods=['POST'])
@login_required
def discord_link_admin():
    current_app.logger.info("--- discord_link_admin CALLED (CSRF Exempted for Test) ---") # New log
    enabled_setting_val = Setting.get('DISCORD_OAUTH_ENABLED', False)
    client_id_val = Setting.get('DISCORD_CLIENT_ID')
    client_secret_val = Setting.get('DISCORD_CLIENT_SECRET') 
    app_base_url_val = Setting.get('APP_BASE_URL')

    current_app.logger.info(f"Retrieved DISCORD_OAUTH_ENABLED: {enabled_setting_val} (Type: {type(enabled_setting_val)})")
    current_app.logger.info(f"Retrieved DISCORD_CLIENT_ID: '{client_id_val}'")
    current_app.logger.info(f"Retrieved DISCORD_CLIENT_SECRET: '{client_secret_val}'")
    current_app.logger.info(f"Retrieved APP_BASE_URL: '{app_base_url_val}'")

    discord_enabled_for_invitees = False
    if isinstance(enabled_setting_val, bool):
        discord_enabled_for_invitees = enabled_setting_val
    else:
        discord_enabled_for_invitees = str(enabled_setting_val).lower() == 'true'

    if not discord_enabled_for_invitees:
        flash('Discord OAuth for Invitees must be enabled and configured before linking your admin account.', 'warning')
        return redirect(url_for('dashboard.settings_discord'))

    # The flash message you saw "Discord Client ID and Secret are required if enabled."
    # does not come from this route. It comes from dashboard.settings_discord on *saving* that form.
    # This route checks client_id and app_base_url for initiating the link.
    # The client_secret is only needed in the callback.
    if not client_id_val or not app_base_url_val: 
        flash_msg = "Discord configuration is incomplete for linking. Required: "
        missing = []
        if not client_id_val: missing.append("Client ID (Save in Discord Settings first)")
        if not app_base_url_val: missing.append("Application Base URL (Save in App URL Settings first)")
        flash(flash_msg + ", ".join(missing) + ".", "danger")
        return redirect(url_for('dashboard.settings_discord'))
    
    # Client secret is NOT needed to initiate the OAuth flow with Discord, only in the callback.
    # So, the check for client_secret_val here was likely causing the redirect if it was failing.
    # The original flash message "Discord Client ID and Secret are required if enabled." is from the other route.

    redirect_uri = url_for('auth.discord_callback_admin', _external=True)
    Setting.set('DISCORD_REDIRECT_URI_ADMIN_LINK', redirect_uri, SettingValueType.STRING, "Discord OAuth Admin Link Redirect URI (auto-set)") # This is fine

    session['discord_oauth_state_admin_link'] = str(uuid.uuid4())
    
    params = {
        'client_id': client_id_val, # Use the value retrieved from settings
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'identify email guilds.join', 
        'state': session['discord_oauth_state_admin_link'],
        'prompt': 'consent' 
    }
    discord_auth_url = f"{DISCORD_API_BASE_URL}/oauth2/authorize?{urlencode(params)}"
    return redirect(discord_auth_url)

@bp.route('/discord/callback_admin')
@login_required
def discord_callback_admin():
    returned_state = request.args.get('state')
    if not returned_state or returned_state != session.pop('discord_oauth_state_admin_link', None):
        flash('Discord linking failed: Invalid state.', 'danger')
        return redirect(url_for('dashboard.settings_discord'))
    
    code = request.args.get('code')
    if not code:
        flash(f'Discord linking failed: {request.args.get("error_description", "No code.")}', 'danger')
        return redirect(url_for('dashboard.settings_discord'))

    client_id = Setting.get('DISCORD_CLIENT_ID')
    client_secret = Setting.get('DISCORD_CLIENT_SECRET')
    redirect_uri = Setting.get('DISCORD_REDIRECT_URI_ADMIN_LINK')

    if not client_id or not client_secret or not redirect_uri:
        flash('Discord app details not fully configured in MUM settings.', 'danger')
        return redirect(url_for('dashboard.settings_discord'))

    token_url = f"{DISCORD_API_BASE_URL}/oauth2/token"
    payload = {
        'client_id': client_id, 'client_secret': client_secret, 
        'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_response = requests.post(token_url, data=payload, headers=headers)
        token_response.raise_for_status()
        token_data = token_response.json()
        
        access_token = token_data['access_token']
        refresh_token = token_data.get('refresh_token') # May not always be present
        expires_in = token_data['expires_in']
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        user_info_url = f"{DISCORD_API_BASE_URL}/users/@me"
        auth_headers = {'Authorization': f'Bearer {access_token}'}
        user_response = requests.get(user_info_url, headers=auth_headers)
        user_response.raise_for_status()
        discord_user = user_response.json()

        # Ensure we are working with the correct admin account instance from the DB
        admin_to_update = AdminAccount.query.get(current_user.id)
        if not admin_to_update:
            # Should not happen if @login_required is working
            flash('Admin account not found. Please log in again.', 'danger')
            return redirect(url_for('auth.app_login'))

        # Check if this Discord ID is already linked to a *different* admin account
        existing_link = AdminAccount.query.filter(
            AdminAccount.id != admin_to_update.id, 
            AdminAccount.discord_user_id == discord_user['id']
        ).first()

        if existing_link:
            flash(f"Discord account '{discord_user['username']}' is already linked to another admin account ({existing_link.username or existing_link.plex_username}).", 'danger')
            return redirect(url_for('dashboard.settings_discord'))

        admin_to_update.discord_user_id = discord_user['id']
        admin_to_update.discord_username = discord_user['username']
        if discord_user.get('discriminator') and discord_user.get('discriminator') != '0':
            admin_to_update.discord_username = f"{discord_user['username']}#{discord_user['discriminator']}"
        admin_to_update.discord_avatar_hash = discord_user.get('avatar')
        admin_to_update.discord_email = discord_user.get('email') # NEW
        admin_to_update.discord_email_verified = discord_user.get('verified') # NEW
        admin_to_update.discord_access_token = access_token
        admin_to_update.discord_refresh_token = refresh_token
        admin_to_update.discord_token_expires_at = token_expires_at
        
        db.session.commit()
        current_app.logger.info(f"ADMIN DISCORD LINK: User {admin_to_update.id} Discord ID {admin_to_update.discord_user_id} committed to DB.")
        
        # --- KEY CHANGE: Re-login the user to refresh the session's user object ---
        # Fetch the updated user from the database to ensure all fields are current
        fresh_admin_user = AdminAccount.query.get(admin_to_update.id)
        if fresh_admin_user:
            # Flask-Login's login_user function will update the user in the session
            login_user(fresh_admin_user, remember=current_user.is_remembered if hasattr(current_user, 'is_remembered') else True) 
            current_app.logger.info(f"ADMIN DISCORD LINK: User {fresh_admin_user.id} re-logged in to refresh session data.")
        else:
            # This would be very unusual if the commit succeeded
            current_app.logger.error(f"ADMIN DISCORD LINK: Could not re-fetch admin user {admin_to_update.id} after commit for re-login.")
        # --- END KEY CHANGE ---

        log_event(EventType.DISCORD_ADMIN_LINK_SUCCESS, f"Admin '{admin_to_update.username or admin_to_update.plex_username}' linked Discord '{admin_to_update.discord_username}'.", admin_id=admin_to_update.id)
        flash('Discord account linked successfully!', 'success')

    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response is not None:
            try:
                error_detail = e.response.json().get('error_description', str(e.response.text))
            except: # Fallback if response is not JSON
                error_detail = str(e.response.content) 
        current_app.logger.error(f"Discord OAuth admin error: {error_detail}", exc_info=True if not isinstance(e, requests.exceptions.HTTPError) else False)
        flash(f'Failed to link Discord: {error_detail}', 'danger')
    except Exception as e_gen: # Catch any other unexpected errors
        current_app.logger.error(f"Unexpected error during Discord admin link callback: {e_gen}", exc_info=True)
        flash('An unexpected error occurred while linking Discord.', 'danger')
        
    return redirect(url_for('dashboard.settings_discord'))

@bp.route('/discord/unlink_admin', methods=['POST'])
@login_required
def discord_unlink_admin():
    discord_username_log = current_user.discord_username
    current_user.discord_user_id = None; current_user.discord_username = None; current_user.discord_avatar_hash = None
    current_user.discord_access_token = None; current_user.discord_refresh_token = None; current_user.discord_token_expires_at = None
    db.session.commit()
    log_event(EventType.DISCORD_ADMIN_UNLINK, f"Admin '{current_user.username or current_user.plex_username}' unlinked Discord '{discord_username_log}'.", admin_id=current_user.id)
    flash('Discord account unlinked.', 'success'); return redirect(url_for('dashboard.settings_discord'))