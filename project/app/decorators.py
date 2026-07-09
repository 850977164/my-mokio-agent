"""路由装饰器

提供 login_required 和 admin_required 装饰器，基于 Flask session。
"""

from functools import wraps
from flask import session, redirect, url_for, flash


def login_required(f):
    """要求用户已登录，否则重定向到登录页面"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录后再访问该页面。', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    """要求用户为管理员，否则重定向到仪表盘"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录后再访问该页面。', 'warning')
            return redirect(url_for('auth.login'))
        if session.get('role') != 'admin':
            flash('您没有管理员权限。', 'danger')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)

    return decorated_function
