"""
Data isolation and file management for user-specific screening data
Ensures users can only access their own files and data
"""
import os
import logging
from pathlib import Path
from flask_login import current_user
from models import db, ScreeningUpload, ScreeningReport

logger = logging.getLogger(__name__)


class UserDataManager:
    """Manages user-specific data storage and access control"""
    
    def __init__(self, base_upload_dir: str = "uploads"):
        self.base_upload_dir = Path(base_upload_dir)
        self.base_upload_dir.mkdir(parents=True, exist_ok=True)
    
    def get_user_upload_dir(self, user_id: int) -> Path:
        """Get the uploads directory for a specific user"""
        user_dir = self.base_upload_dir / f"user_{user_id}" / "uploads"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
    
    def get_user_reports_dir(self, user_id: int) -> Path:
        """Get the reports directory for a specific user"""
        reports_dir = self.base_upload_dir / f"user_{user_id}" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        return reports_dir
    
    def get_user_data_dir(self, user_id: int) -> Path:
        """Get the root data directory for a specific user"""
        data_dir = self.base_upload_dir / f"user_{user_id}"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    
    def get_current_user_dir(self) -> Path:
        """Get upload directory for the current authenticated user"""
        if not current_user.is_authenticated:
            raise PermissionError("User not authenticated")
        return self.get_user_upload_dir(current_user.id)
    
    def get_current_user_reports_dir(self) -> Path:
        """Get reports directory for the current authenticated user"""
        if not current_user.is_authenticated:
            raise PermissionError("User not authenticated")
        return self.get_user_reports_dir(current_user.id)
    
    @staticmethod
    def verify_file_ownership(user_id: int, file_path: str) -> bool:
        """
        Verify that a file belongs to the specified user.
        Prevents directory traversal attacks.
        """
        user_data_dir = Path("uploads") / f"user_{user_id}"
        try:
            file_full_path = user_data_dir.resolve() / file_path
            # Ensure the resolved path is still within the user's directory
            return str(file_full_path).startswith(str(user_data_dir.resolve()))
        except Exception:
            return False
    
    @staticmethod
    def verify_upload_ownership(user_id: int, upload_id: int) -> bool:
        """Verify that an upload record belongs to the specified user"""
        upload = ScreeningUpload.query.filter_by(id=upload_id, user_id=user_id).first()
        return upload is not None
    
    @staticmethod
    def verify_report_ownership(user_id: int, report_id: int) -> bool:
        """Verify that a report record belongs to the specified user"""
        report = ScreeningReport.query.filter_by(id=report_id, user_id=user_id).first()
        return report is not None
    
    @staticmethod
    def get_user_uploads(user_id: int, limit: int = None):
        """Get all uploads for a user with optional limit"""
        query = ScreeningUpload.query.filter_by(user_id=user_id).order_by(
            ScreeningUpload.upload_timestamp.desc()
        )
        if limit:
            query = query.limit(limit)
        return query.all()
    
    @staticmethod
    def get_user_reports(user_id: int, limit: int = None):
        """Get all reports for a user with optional limit"""
        query = ScreeningReport.query.filter_by(user_id=user_id).order_by(
            ScreeningReport.generated_at.desc()
        )
        if limit:
            query = query.limit(limit)
        return query.all()
    
    @staticmethod
    def get_report_statistics(user_id: int) -> dict:
        """Get statistics about a user's reports"""
        reports = ScreeningReport.query.filter_by(user_id=user_id).all()
        
        total = len(reports)
        positive = len([r for r in reports if r.urgency and 'urgent' in r.urgency.lower()])
        negative = total - positive
        
        avg_cal_prob = 0
        if total > 0:
            avg_cal_prob = sum(r.calibrated_probability or 0 for r in reports) / total
        
        return {
            'total': total,
            'positive': positive,
            'negative': negative,
            'avg_cal_prob': avg_cal_prob,
            'pos_rate': (positive / total * 100) if total > 0 else 0
        }


class SecureFileAccess:
    """Handles secure file access with permission checks"""
    
    @staticmethod
    def is_path_safe(base_dir: Path, requested_path: Path) -> bool:
        """
        Verify that requested_path is within base_dir.
        Prevents directory traversal attacks.
        """
        try:
            # Resolve both paths to absolute to prevent symlink tricks
            base_resolved = base_dir.resolve()
            path_resolved = (base_dir / requested_path).resolve()
            
            # Check if the resolved path is within the base directory
            path_resolved.relative_to(base_resolved)
            return True
        except ValueError:
            return False
    
    @staticmethod
    def get_user_file(user_id: int, file_path: str):
        """
        Safely retrieve a file that belongs to the user.
        Returns None if file doesn't exist or user doesn't own it.
        """
        if not UserDataManager.verify_file_ownership(user_id, file_path):
            logger.warning(f"Unauthorized file access attempt by user {user_id}: {file_path}")
            return None
        
        user_data_dir = Path("uploads") / f"user_{user_id}"
        full_path = (user_data_dir / file_path).resolve()
        
        if not full_path.exists() or not full_path.is_file():
            return None
        
        return full_path
    
    @staticmethod
    def delete_user_file(user_id: int, file_path: str) -> bool:
        """
        Safely delete a file that belongs to the user.
        Returns True if successful, False otherwise.
        """
        file_to_delete = SecureFileAccess.get_user_file(user_id, file_path)
        if not file_to_delete:
            return False
        
        try:
            file_to_delete.unlink()
            logger.info(f"Deleted file for user {user_id}: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file for user {user_id}: {e}")
            return False


def require_user_ownership(resource_type: str):
    """
    Decorator to verify user ownership of resources before processing.
    
    Args:
        resource_type: 'upload' or 'report'
    """
    from functools import wraps
    from flask import request, abort
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            resource_id = request.view_args.get('id')
            if not resource_id:
                abort(400)
            
            try:
                resource_id = int(resource_id)
            except (ValueError, TypeError):
                abort(400)
            
            if resource_type == 'upload':
                if not UserDataManager.verify_upload_ownership(current_user.id, resource_id):
                    abort(403)
            elif resource_type == 'report':
                if not UserDataManager.verify_report_ownership(current_user.id, resource_id):
                    abort(403)
            else:
                abort(400)
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator
