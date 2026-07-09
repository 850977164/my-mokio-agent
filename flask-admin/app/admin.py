from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app.models import User

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/dashboard')
@login_required
def dashboard():
    user_count = User.query.count()
    return render_template(
        'admin/dashboard.html',
        user_count=user_count
    )


@admin_bp.route('/users')
@login_required
def users():
    all_users = User.query.order_by(User.id).all()
    return render_template(
        'admin/users.html',
        users=all_users
    )
