"""
Authentication routes: login, register, logout
"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, current_user
from models import db, User
from auth_utils import (
    validate_username, validate_password, validate_email, log_audit
)

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


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
                full_name=full_name
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            log_audit('user_registered', user_id=user.id, status='success')
            
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        
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
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(username=username).first()
        
        if not user:
            logger.warning(f"Login attempt with non-existent username: {username}")
            log_audit('login_failed', status='failure', details=f'User not found: {username}')
            flash('Invalid username or password', 'error')
            return render_template('auth/login.html'), 401
        
        if not user.is_active:
            log_audit('login_failed', user_id=user.id, status='failure', details='Account inactive')
            flash('Your account has been deactivated', 'error')
            return render_template('auth/login.html'), 403
        
        if not user.check_password(password):
            logger.warning(f"Failed login attempt for user: {username}")
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
    """Forgot password — shows a polished form; no email is sent (SMTP not configured)."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        # We always return the same response to prevent user enumeration.
        logger.info(f"Password reset requested for email: {email}")
        log_audit('password_reset_requested', status='info', details=f'Email: {email}')
        # Redirect with ?sent=1 so the template can show the success state
        return redirect(url_for('auth.forgot_password') + '?sent=1')

    return render_template('auth/forgot_password.html')


@auth_bp.route('/profile', methods=['GET'])
def profile():
    """View user profile"""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    return render_template('auth/profile.html', user=current_user)


@auth_bp.route('/change-password', methods=['POST'])
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
