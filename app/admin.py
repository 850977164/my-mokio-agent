from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app.models import User

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Custom decorator to restrict access to admin users only."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash('您没有管理员权限，无法访问此页面。', 'danger')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@login_required
def dashboard():
    """Main dashboard page — accessible to all authenticated users."""
    user_count = User.query.count()
    return render_template('dashboard.html',
                           title='仪表盘',
                           user=current_user,
                           user_count=user_count)


@admin_bp.route('/users')
@admin_required
def users():
    """Admin-only page: list all registered users."""
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html',
                           title='用户管理',
                           users=all_users)
