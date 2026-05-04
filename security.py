"""
Security utilities: headers, CSRF protection, input validation
"""
import os
import logging
from flask import request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def init_security(app):
    """Initialize security features for Flask app"""
    
    @app.before_request
    def set_security_headers():
        """Add security headers to all responses"""
        pass  # Headers are set in after_request
    
    @app.after_request
    def add_security_headers(response):
        """Add security headers to all responses"""
        
        # Prevent clickjacking attacks
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        # Enable XSS protection in older browsers
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Content Security Policy - restrictive but functional
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "  # Minimal unsafe-inline for compatibility
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://res.cloudinary.com; "
            "connect-src 'self'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers['Content-Security-Policy'] = csp
        
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Feature policy / Permissions policy
        response.headers['Permissions-Policy'] = (
            'geolocation=(), microphone=(), camera=(), usb=(), payment=()'
        )
        
        # HSTS (HTTP Strict-Transport-Security) - only on HTTPS
        if request.is_secure or os.environ.get('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        return response
    
    # Session security
    app.config.update(
        SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    
    logger.info("Security headers and features initialized")


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize filename to prevent directory traversal and other attacks.
    
    Args:
        filename: The filename to sanitize
        max_length: Maximum length for the sanitized filename
    
    Returns:
        Safe filename
    """
    import re
    
    # Remove any path components
    filename = os.path.basename(filename)
    
    # Remove null bytes
    filename = filename.replace('\0', '')
    
    # Allow only safe characters (alphanumeric, dash, underscore, dot)
    filename = re.sub(r'[^\w\-\.]', '_', filename)
    
    # Remove leading/trailing dots and spaces
    filename = filename.strip('. ')
    
    # Prevent empty filename
    if not filename:
        filename = 'file'
    
    # Limit length
    if len(filename) > max_length:
        # Preserve extension
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext
    
    return filename


def validate_file_extension(filename: str, allowed_extensions: list) -> bool:
    """
    Validate that file has an allowed extension.
    
    Args:
        filename: The filename to validate
        allowed_extensions: List of allowed extensions (without dots)
    
    Returns:
        True if extension is allowed, False otherwise
    """
    if not filename or '.' not in filename:
        return False
    
    ext = filename.rsplit('.', 1)[-1].lower()
    return ext in [e.lower() for e in allowed_extensions]


def mask_sensitive_data(data: dict, fields_to_mask: list) -> dict:
    """
    Mask sensitive fields in a dictionary before logging or sending to client.
    
    Args:
        data: Dictionary containing data to mask
        fields_to_mask: List of field names to mask
    
    Returns:
        Dictionary with masked fields
    """
    import copy
    
    masked = copy.deepcopy(data)
    for field in fields_to_mask:
        if field in masked:
            value = str(masked[field])
            if len(value) > 4:
                masked[field] = value[:2] + '*' * (len(value) - 4) + value[-2:]
            else:
                masked[field] = '*' * len(value)
    
    return masked


def get_client_info() -> dict:
    """Extract client information from request for logging"""
    return {
        'ip_address': request.remote_addr,
        'user_agent': request.headers.get('User-Agent', 'Unknown'),
        'endpoint': request.endpoint,
        'method': request.method,
        'timestamp': now_ist().isoformat()
    }


class RateLimiter:
    """Simple in-memory rate limiter for protecting against abuse"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}  # {key: [(timestamp, count), ...]}
    
    def is_rate_limited(self, key: str) -> bool:
        """Check if a key has exceeded rate limit"""
        now = now_ist()
        window_start = now - timedelta(seconds=self.window_seconds)
        
        # Clean old entries
        if key in self.requests:
            self.requests[key] = [
                (ts, count) for ts, count in self.requests[key]
                if ts > window_start
            ]
        
        # Count requests in window
        total_requests = sum(count for _, count in self.requests.get(key, []))
        
        return total_requests >= self.max_requests
    
    def record_request(self, key: str):
        """Record a request for rate limiting"""
        now = now_ist()
        
        if key not in self.requests:
            self.requests[key] = []
        
        # Add or increment the count for this second
        if self.requests[key] and self.requests[key][-1][0] == now:
            ts, count = self.requests[key][-1]
            self.requests[key][-1] = (ts, count + 1)
        else:
            self.requests[key].append((now, 1))


# Global rate limiter instances
login_rate_limiter = RateLimiter(max_requests=5, window_seconds=300)  # 5 attempts in 5 minutes
upload_rate_limiter = RateLimiter(max_requests=20, window_seconds=3600)  # 20 uploads per hour


def check_login_rate_limit(identifier: str) -> tuple[bool, str]:
    """
    Check if login attempt should be rate limited.
    Returns (is_limited, message)
    """
    if login_rate_limiter.is_rate_limited(identifier):
        return True, "Too many login attempts. Please try again later."
    
    login_rate_limiter.record_request(identifier)
    return False, ""


def check_upload_rate_limit(user_id: int) -> tuple[bool, str]:
    """
    Check if upload should be rate limited.
    Returns (is_limited, message)
    """
    key = f"upload_{user_id}"
    if upload_rate_limiter.is_rate_limited(key):
        return True, "Upload rate limit exceeded. Maximum 20 uploads per hour."
    
    upload_rate_limiter.record_request(key)
    return False, ""
