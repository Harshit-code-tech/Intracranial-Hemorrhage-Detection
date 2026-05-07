"""Authentication routes: login, register, logout, password reset and OTP verification."""
import hashlib
import logging
import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urljoin

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_

from auth_utils import log_audit, validate_email, validate_password, validate_username
from models import PendingOtp, User, db, now_ist

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _auth_email_debug_enabled() -> bool:
    return _parse_bool(os.environ.get("ICH_DEBUG_AUTH_EMAILS"), False)


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# ── DB-based OTP helpers (session-cookie-free) ────────────────────────────────

def _store_otp(email: str, purpose: str, user_id: int | None = None,
               pending_value: str | None = None) -> tuple[str, str]:
    """Generate a new OTP, persist it in the DB, return (code, token)."""
    # Delete any existing pending OTPs for this email+purpose to keep the table clean
    PendingOtp.query.filter_by(email=email, purpose=purpose).delete()
    code  = _generate_otp()
    token = secrets.token_urlsafe(32)
    row   = PendingOtp(
        token         = token,
        email         = email,
        purpose       = purpose,
        otp_hash      = _hash_otp(code),
        expires_at    = now_ist() + timedelta(minutes=10),
        attempts      = 0,
        user_id       = user_id,
        pending_value = pending_value,
    )
    db.session.add(row)
    db.session.commit()
    return code, token


def _otp_row_from_token(token: str | None) -> PendingOtp | None:
    """Look up a PendingOtp row by its opaque token."""
    if not token:
        return None
    return PendingOtp.query.filter_by(token=token).first()


def _validate_otp(submitted_code: str, expected_purpose: str,
                  token: str | None) -> tuple[bool, str, PendingOtp | None]:
    """Validate a submitted OTP code. Returns (ok, message, row)."""
    row = _otp_row_from_token(token)
    if not row:
        return False, "OTP session is missing or expired. Please request a new code.", None
    if row.purpose != expected_purpose:
        return False, "OTP purpose mismatch. Please request a new code.", None
    if row.is_expired():
        db.session.delete(row)
        db.session.commit()
        return False, "OTP expired. Please request a new code.", None
    if row.attempts >= 5:
        db.session.delete(row)
        db.session.commit()
        return False, "Too many failed attempts. Please request a new code.", None
    if _hash_otp(submitted_code) != row.otp_hash:
        row.attempts += 1
        db.session.commit()
        remaining = 5 - row.attempts
        return False, f"Invalid OTP code. {remaining} attempt(s) remaining.", None
    return True, "", row


def _clear_otp_row(row: PendingOtp) -> None:
    db.session.delete(row)
    db.session.commit()


def _otp_body(code: str, purpose: str) -> str:
    if purpose == "verify_email":
        title = "Verify your ICH Screening account"
    else:
        title = "Your ICH Screening verification code"
    return (
        f"{title}\n\n"
        f"Your one-time password (OTP) is: {code}\n"
        "This code expires in 10 minutes.\n\n"
        "If you did not request this, you can ignore this email."
    )


def _password_reset_body(reset_link: str) -> str:
    return (
        "Reset your ICH Screening password\n\n"
        f"Click the link below to set a new password:\n{reset_link}\n\n"
        "This link expires in 30 minutes.\n"
        "If you did not request this, you can ignore this email."
    )


def _send_email(to_email: str, subject: str, body: str) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", os.environ.get("EMAIL_HOST", "")).strip()
    smtp_user = os.environ.get("SMTP_USER", os.environ.get("EMAIL_HOST_USER", "")).strip()
    smtp_pass = os.environ.get("SMTP_PASSWORD", os.environ.get("EMAIL_HOST_PASSWORD", "")).strip()
    smtp_from = os.environ.get("SMTP_FROM", os.environ.get("EMAIL_FROM", smtp_user)).strip()
    port_raw = os.environ.get("SMTP_PORT", os.environ.get("EMAIL_PORT", "587"))
    smtp_port = int(port_raw)
    use_tls = _parse_bool(os.environ.get("SMTP_USE_TLS", os.environ.get("EMAIL_USE_TLS")), True)

    if not smtp_host or not smtp_from:
        logger.error(
            "SMTP not configured: set SMTP_HOST/SMTP_FROM or EMAIL_HOST/EMAIL_FROM (and credentials if required)."
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False


def _token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="ich-password-reset")


def _build_external_link(endpoint: str, **values: object) -> str:
    """Build externally reachable link, preferring explicit public base URL when configured."""
    public_base_url = os.environ.get("ICH_PUBLIC_BASE_URL", "").strip()
    if public_base_url:
        relative_path = url_for(endpoint, _external=False, **values)
        return urljoin(public_base_url.rstrip("/") + "/", relative_path.lstrip("/"))
    return url_for(endpoint, _external=True, **values)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        full_name = request.form.get('full_name', '').strip()
        
        # Validate inputs
        valid, msg = validate_username(username)
        if not valid:
            flash(msg, 'error')
            return render_template('auth/register.html'), 400
        
        valid, msg = validate_email(email)
        if not valid:
            flash(msg, 'error')
            return render_template('auth/register.html'), 400
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('auth/register.html'), 400
        
        valid, msg = validate_password(password)
        if not valid:
            flash(msg, 'error')
            return render_template('auth/register.html'), 400
        
        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('auth/register.html'), 400
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('auth/register.html'), 400
        
        try:
            # Create new user
            user = User(
                username=username,
                email=email,
                full_name=full_name,
                is_active=False,
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            otp_code, otp_token = _store_otp(email=user.email, purpose="verify_email", user_id=user.id)
            sent = _send_email(
                user.email,
                "Your ICH Screening verification code",
                _otp_body(otp_code, "verify_email"),
            )
            if _auth_email_debug_enabled():
                logger.info("DEV OTP for %s: %s", user.email, otp_code)

            log_audit('user_registered', user_id=user.id, status='success')
            notice = 'otp_sent' if sent else 'otp_email_failed'
            return redirect(url_for('auth.verify_otp', purpose='verify_email',
                                    email=user.email, otp_token=otp_token, notice=notice))
        
        except Exception as e:
            db.session.rollback()
            logger.error(f"Registration error: {e}")
            log_audit('user_registration_failed', status='failure', details=str(e))
            flash('Registration failed. Please try again.', 'error')
            return render_template('auth/register.html'), 500
    
    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        identifier = request.form.get('identifier', request.form.get('username', '')).strip()
        normalized_identifier = identifier.lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember', False))

        user = User.query.filter(
            or_(
                func.lower(User.username) == normalized_identifier,
                func.lower(User.email) == normalized_identifier,
            )
        ).first()
        
        if not user:
            logger.warning("Login attempt with non-existent identifier: %s", identifier)
            log_audit('login_failed', status='failure', details=f'User not found: {identifier}')
            flash('Invalid username or password', 'error')
            return render_template('auth/login.html'), 401
        
        if not user.is_active:
            otp_code, otp_token = _store_otp(email=user.email, purpose="verify_email", user_id=user.id)
            sent = _send_email(
                user.email,
                "Your ICH Screening verification code",
                _otp_body(otp_code, "verify_email"),
            )
            if _auth_email_debug_enabled():
                logger.info("DEV OTP resend/login for %s: %s", user.email, otp_code)
            log_audit('login_failed', user_id=user.id, status='failure', details='Email not verified')
            notice = 'otp_resent' if sent else 'otp_email_failed'
            return redirect(url_for('auth.verify_otp', purpose='verify_email',
                                    email=user.email, otp_token=otp_token, notice=notice))
        
        if not user.check_password(password):
            logger.warning("Failed login attempt for identifier: %s", identifier)
            log_audit('login_failed', user_id=user.id, status='failure', details='Invalid password')
            flash('Invalid username or password', 'error')
            return render_template('auth/login.html'), 401
        
        try:
            login_user(user, remember=remember)
            log_audit('login_success', user_id=user.id, status='success')
            
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('home'))
        
        except Exception as e:
            logger.error(f"Login error: {e}")
            log_audit('login_error', user_id=user.id, status='failure', details=str(e))
            flash('Login failed. Please try again.', 'error')
            return render_template('auth/login.html'), 500
    
    return render_template('auth/login.html')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """User logout"""
    if current_user.is_authenticated:
        log_audit('logout', user_id=current_user.id, status='success')
        logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Forgot password route: issue signed reset link and send email."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            token = _token_serializer().dumps({"email": user.email, "purpose": "reset_password"})
            reset_link = _build_external_link('auth.reset_password', token=token)
            sent = _send_email(
                user.email,
                'Reset your ICH Screening password',
                _password_reset_body(reset_link),
            )
            if _auth_email_debug_enabled():
                logger.info("DEV reset link for %s: %s", user.email, reset_link)
            log_audit(
                'password_reset_requested',
                user_id=user.id,
                status='success' if sent else 'failure',
                details='Reset email sent' if sent else 'SMTP failed',
            )
        else:
            log_audit('password_reset_requested', status='info', details=f'Unknown email: {email}')
            flash('No account exists with this email address.', 'error')
            return render_template('auth/forgot_password.html'), 404

        return redirect(url_for('auth.forgot_password') + '?sent=1')

    return render_template('auth/forgot_password.html')


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    """Verify one-time password — uses DB row via otp_token URL param (cookie-free)."""
    purpose    = request.args.get('purpose', 'verify_email')
    otp_token  = request.args.get('otp_token') or request.form.get('otp_token', '')
    email      = request.args.get('email', '')
    notice     = request.args.get('notice', '')

    if request.method == 'POST':
        # Reconstruct digits → 6-char string
        direct = request.form.get('otp', '').strip()
        if not direct:
            direct = ''.join(request.form.get(f'd{i}', '').strip() for i in range(1, 7))
        submitted = direct

        if len(submitted) != 6 or not submitted.isdigit():
            return redirect(url_for('auth.verify_otp', purpose=purpose, email=email,
                                    otp_token=otp_token, notice='invalid_digits'))

        ok, msg, row = _validate_otp(submitted, purpose, otp_token)
        if not ok:
            logger.warning("OTP validation failed for %s: %s", email, msg)
            return redirect(url_for('auth.verify_otp', purpose=purpose, email=email,
                                    otp_token=otp_token, notice='invalid_code'))

        user = User.query.get(int(row.user_id)) if row.user_id else None
        if not user:
            _clear_otp_row(row)
            return redirect(url_for('auth.register') + '?notice=session_invalid')

        user.is_active = True
        _clear_otp_row(row)
        log_audit('email_verified', user_id=user.id, status='success')
        flash('Email verified. You can now sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/verify_otp.html', email=email, purpose=purpose,
                           otp_token=otp_token, notice=notice)


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend OTP — recovers context from the otp_token in the form, not the session."""
    old_token = request.form.get('otp_token', '')
    email     = request.form.get('email', '')
    purpose   = request.form.get('purpose', 'verify_email')

    # Look up the old row to get user_id
    old_row = _otp_row_from_token(old_token)
    user_id = old_row.user_id if old_row else None

    # If we have no row and no email, we can't recover
    if not email:
        return redirect(url_for('auth.register') + '?notice=session_invalid')

    new_code, new_token = _store_otp(email=email, purpose=purpose, user_id=user_id)
    sent = _send_email(email, "Your ICH Screening verification code", _otp_body(new_code, purpose))
    if _auth_email_debug_enabled():
        logger.info("DEV OTP resend for %s: %s", email, new_code)

    notice = 'otp_resent' if sent else 'otp_email_failed'
    return redirect(url_for('auth.verify_otp', purpose=purpose, email=email,
                            otp_token=new_token, notice=notice))


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token: str):
    """Reset password using signed token sent by email."""
    try:
        payload = _token_serializer().loads(token, max_age=1800)
    except SignatureExpired:
        flash('Password reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))
    except BadSignature:
        flash('Invalid password reset link.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if payload.get('purpose') != 'reset_password':
        flash('Invalid password reset link.', 'error')
        return redirect(url_for('auth.forgot_password'))

    email = payload.get('email', '').strip().lower()
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Invalid password reset link.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth/reset_password.html', token=token), 400

        valid, msg = validate_password(password)
        if not valid:
            flash(msg, 'error')
            return render_template('auth/reset_password.html', token=token), 400

        user.set_password(password)
        db.session.commit()
        log_audit('password_reset_completed', user_id=user.id, status='success')
        flash('Password updated successfully. Please sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)


@auth_bp.route('/profile', methods=['GET'])
@login_required
def profile():
    """View user profile"""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    return render_template('auth/profile.html', user=current_user)


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password"""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400
    
    data = request.get_json()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')
    
    if not current_user.check_password(current_password):
        log_audit('password_change_failed', user_id=current_user.id, 
                 status='failure', details='Invalid current password')
        return jsonify({'error': 'Current password is incorrect'}), 401
    
    if new_password != confirm_password:
        return jsonify({'error': 'New passwords do not match'}), 400
    
    valid, msg = validate_password(new_password)
    if not valid:
        return jsonify({'error': msg}), 400
    
    try:
        current_user.set_password(new_password)
        db.session.commit()
        log_audit('password_changed', user_id=current_user.id, status='success')
        return jsonify({'message': 'Password changed successfully'}), 200
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"Password change error: {e}")
        log_audit('password_change_error', user_id=current_user.id, 
                 status='failure', details=str(e))
        return jsonify({'error': 'Password change failed'}), 500

# ── Profile Management Routes ───────────────────────────────────────────────

@auth_bp.route('/profile/update-name', methods=['POST'])
@login_required
def update_name():
    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400
    full_name = request.json.get('full_name', '').strip()
    if not full_name:
        return jsonify({'error': 'Name cannot be empty'}), 400
    
    current_user.full_name = full_name
    db.session.commit()
    return jsonify({'message': 'Name updated successfully', 'full_name': full_name})


@auth_bp.route('/profile/request-username-change', methods=['POST'])
@login_required
def request_username_change():
    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400
    new_username = request.json.get('new_username', '').strip()
    valid, msg = validate_username(new_username)
    if not valid:
        return jsonify({'error': msg}), 400
    if User.query.filter_by(username=new_username).first():
        return jsonify({'error': 'Username already taken'}), 400

    code, token = _store_otp(email=current_user.email, purpose="change_username", 
                             user_id=current_user.id, pending_value=new_username)
    sent = _send_email(current_user.email, "Verify username change", _otp_body(code, "change_username"))
    if not sent:
        return jsonify({'error': 'Failed to send OTP email'}), 500
    return jsonify({'message': 'OTP sent to your current email', 'otp_token': token})


@auth_bp.route('/profile/request-email-change', methods=['POST'])
@login_required
def request_email_change():
    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400
    new_email = request.json.get('new_email', '').strip().lower()
    valid, msg = validate_email(new_email)
    if not valid:
        return jsonify({'error': msg}), 400
    if User.query.filter_by(email=new_email).first():
        return jsonify({'error': 'Email already registered'}), 400

    code, token = _store_otp(email=new_email, purpose="change_email", 
                             user_id=current_user.id, pending_value=new_email)
    sent = _send_email(new_email, "Verify your new email address", _otp_body(code, "change_email"))
    if not sent:
        return jsonify({'error': 'Failed to send OTP to new email'}), 500
    return jsonify({'message': 'OTP sent to your NEW email address', 'otp_token': token})


@auth_bp.route('/profile/confirm-change', methods=['POST'])
@login_required
def confirm_profile_change():
    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400
    
    otp = request.json.get('otp', '').strip()
    otp_token = request.json.get('otp_token', '').strip()
    purpose = request.json.get('purpose', '').strip()

    if purpose not in ("change_username", "change_email"):
        return jsonify({'error': 'Invalid purpose'}), 400

    ok, msg, row = _validate_otp(otp, purpose, otp_token)
    if not ok:
        return jsonify({'error': msg}), 400
    
    if not row.pending_value:
        _clear_otp_row(row)
        return jsonify({'error': 'No pending value found in OTP session'}), 400

    if purpose == "change_username":
        if User.query.filter_by(username=row.pending_value).first():
            _clear_otp_row(row)
            return jsonify({'error': 'Username was taken in the meantime'}), 400
        current_user.username = row.pending_value
    
    elif purpose == "change_email":
        if User.query.filter_by(email=row.pending_value).first():
            _clear_otp_row(row)
            return jsonify({'error': 'Email was taken in the meantime'}), 400
        current_user.email = row.pending_value

    db.session.commit()
    _clear_otp_row(row)
    log_audit(purpose, user_id=current_user.id, status='success')
    return jsonify({'message': 'Profile updated successfully'})


@auth_bp.route('/profile/upload-avatar', methods=['POST'])
@login_required
def upload_avatar():
    if 'avatar' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['avatar']
    if not file.filename:
        return jsonify({'error': 'No selected file'}), 400
    
    try:
        import cloudinary
        import cloudinary.uploader
        res = cloudinary.uploader.upload(file, folder="ich_avatars", 
                                         transformation=[{'width': 256, 'height': 256, 'crop': 'fill', 'gravity': 'face'}])
        
        # Delete old avatar if exists
        if current_user.avatar_public_id:
            try:
                cloudinary.uploader.destroy(current_user.avatar_public_id)
            except Exception as exc:
                logger.warning("Failed to destroy old avatar: %s", exc)

        current_user.avatar_url = res.get('secure_url')
        current_user.avatar_public_id = res.get('public_id')
        db.session.commit()
        return jsonify({'message': 'Avatar updated', 'avatar_url': current_user.avatar_url})
    except ImportError:
        return jsonify({'error': 'Cloudinary package not installed'}), 500
    except Exception as exc:
        logger.error("Avatar upload failed: %s", exc)
        return jsonify({'error': 'Failed to upload image'}), 500


@auth_bp.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    password = request.form.get('password', '')
    if not current_user.check_password(password):
        flash('Incorrect password. Account deletion cancelled.', 'error')
        return redirect(url_for('auth.profile'))
    
    try:
        user_id = current_user.id
        
        # Cloudinary cleanup: delete avatar if exists
        if current_user.avatar_public_id:
            try:
                import cloudinary.uploader
                cloudinary.uploader.destroy(current_user.avatar_public_id)
            except Exception as exc:
                logger.warning("Failed to delete avatar during account deletion: %s", exc)

        db.session.delete(current_user)
        db.session.commit()
        log_audit('account_deleted', user_id=user_id, status='success')
        logout_user()
        flash('Your account has been successfully deleted.', 'success')
        return redirect(url_for('home'))
    except Exception as exc:
        db.session.rollback()
        logger.error("Error deleting account: %s", exc)
        flash('An error occurred while deleting your account.', 'error')
        return redirect(url_for('auth.profile'))
