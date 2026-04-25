"""
Database models for ICH Screening Application with user authentication and privacy
"""
import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User account model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Relationships
    screening_uploads = db.relationship('ScreeningUpload', backref='user', lazy=True, cascade='all, delete-orphan')
    screening_reports = db.relationship('ScreeningReport', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set the user's password"""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        """Verify password against stored hash"""
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'


class ScreeningUpload(db.Model):
    """Track uploaded DICOM files with user ownership"""
    __tablename__ = 'screening_uploads'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer)  # bytes
    file_path = db.Column(db.String(500), nullable=False)  # Relative to user's upload dir
    upload_timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    processing_status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed
    processing_error = db.Column(db.Text)  # Error message if failed
    
    # Relationships
    reports = db.relationship('ScreeningReport', backref='upload', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<ScreeningUpload {self.id} - user {self.user_id}>'


class ScreeningReport(db.Model):
    """Store screening results with full user isolation"""
    __tablename__ = 'screening_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('screening_uploads.id'), nullable=False, index=True)
    image_id = db.Column(db.String(100), nullable=False)
    
    # Prediction results
    screening_outcome = db.Column(db.String(100))
    raw_probability = db.Column(db.Float)
    calibrated_probability = db.Column(db.Float)
    confidence_band = db.Column(db.String(50))
    decision_threshold = db.Column(db.Float)
    
    # Triage information
    triage_action = db.Column(db.String(100))
    urgency = db.Column(db.String(50))
    
    # Ground truth (for validation only)
    true_label = db.Column(db.String(100))
    
    # File paths (relative to user's data dir)
    report_json_path = db.Column(db.String(500))
    gradcam_image_path = db.Column(db.String(500))
    
    # Generated timestamp
    generated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<ScreeningReport {self.id} - user {self.user_id} - {self.image_id}>'


class AuditLog(db.Model):
    """Audit trail for security and compliance"""
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)  # login, logout, upload, delete, download, etc.
    resource_type = db.Column(db.String(50))  # upload, report, etc.
    resource_id = db.Column(db.Integer)
    details = db.Column(db.Text)  # JSON or plain text with additional info
    ip_address = db.Column(db.String(45))  # IPv4 or IPv6
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    status = db.Column(db.String(20), default='success')  # success, failure
    
    def __repr__(self):
        return f'<AuditLog {self.action} - user {self.user_id} - {self.timestamp}>'
