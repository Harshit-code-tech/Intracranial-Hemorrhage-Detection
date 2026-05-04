"""
Authentication utilities and decorators for user management and security
"""
import logging
from functools import wraps
from flask import session, redirect, url_for, request, has_request_context
from flask_login import LoginManager, current_user
from models import db, User, AuditLog, now_ist
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

login_manager = LoginManager()


def init_auth(app):
    """Initialize authentication system"""
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    """Load user from database by ID"""
    try:
        return User.query.get(int(user_id))
    except SQLAlchemyError as e:
        logger.warning(f"User loader failed, clearing session context: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
    except Exception as e:
        logger.warning(f"Unexpected user loader failure: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def get_client_ip():
    """Extract client IP address from request"""
    if not has_request_context():
        return 'system'
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'


def log_audit(action, user_id=None, resource_type=None, resource_id=None, 
              details=None, status='success'):
    """Log action to audit trail"""
    try:
        audit_entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=get_client_ip(),
            timestamp=now_ist(),
            status=status
        )
        db.session.add(audit_entry)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to log audit entry: {e}")
        # Don't raise - audit failures shouldn't break the app


def login_required_with_audit(f):
    """Decorator that requires login and logs the access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            log_audit('access_denied', status='failure', details=f'Unauthorized access to {request.path}')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def require_json_content_type(f):
    """Decorator to ensure request has JSON content type"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'PATCH']:
            if not request.is_json:
                return {'error': 'Content-Type must be application/json'}, 400
        return f(*args, **kwargs)
    return decorated_function


def validate_username(username):
    """Validate username format"""
    if not username or len(username) < 3 or len(username) > 80:
        return False, "Username must be between 3 and 80 characters"
    if not all(c.isalnum() or c in '_-' for c in username):
        return False, "Username can only contain letters, numbers, underscores, and hyphens"
    return True, ""


def validate_password(password):
    """Validate password strength"""
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if len(password) > 128:
        return False, "Password must be less than 128 characters"
    # Check for at least one uppercase, one lowercase, one digit
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_upper and has_lower and has_digit):
        return False, "Password must contain uppercase, lowercase, and digits"
    return True, ""


def validate_email(email):
    """Basic email validation"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format"
    return True, ""
