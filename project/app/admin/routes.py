"""管理后台路由

仪表盘和用户管理（CRUD）。
"""

from flask import render_template, request, redirect, url_for, session, flash
from app.extensions import db
from app.models import User
from app.decorators import login_required, admin_required
from app.admin import admin_bp


@admin_bp.route('/dashboard')
@login_required
def dashboard():
    """仪表盘 — 显示基本统计信息"""
    total_users = User.query.count()
    admin_count = User.query.filter_by(role='admin').count()
    active_count = User.query.filter_by(is_active=True).count()
    inactive_count = total_users - active_count

    stats = {
        'total_users': total_users,
        'admin_count': admin_count,
        'active_count': active_count,
        'inactive_count': inactive_count,
    }

    return render_template('dashboard.html', stats=stats)


@admin_bp.route('/users')
@admin_required
def user_list():
    """用户列表（支持简单分页）"""
    page = request.args.get('page', 1, type=int)
    per_page = 10

    pagination = User.query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    users = pagination.items

    return render_template(
        'user_list.html',
        users=users,
        pagination=pagination,
    )


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@admin_required
def user_create():
    """创建用户"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')

        # 校验
        errors = []
        if not username or len(username) < 3:
            errors.append('用户名至少需要3个字符。')
        if not email or '@' not in email:
            errors.append('请输入有效的邮箱地址。')
        if not password or len(password) < 6:
            errors.append('密码至少需要6个字符。')
        if role not in ('user', 'admin'):
            errors.append('无效的角色。')
        if User.query.filter_by(username=username).first():
            errors.append('该用户名已被使用。')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已被注册。')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('user_form.html', user=None)

        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash(f'用户 {username} 创建成功！', 'success')
        return redirect(url_for('admin.user_list'))

    return render_template('user_form.html', user=None)


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def user_edit(user_id: int):
    """编辑用户"""
    user = db.session.get(User, user_id)
    if user is None:
        flash('用户不存在。', 'danger')
        return redirect(url_for('admin.user_list'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        is_active = request.form.get('is_active') == '1'

        errors = []
        if not username or len(username) < 3:
            errors.append('用户名至少需要3个字符。')
        if not email or '@' not in email:
            errors.append('请输入有效的邮箱地址。')
        if role not in ('user', 'admin'):
            errors.append('无效的角色。')

        # 检查唯一性（排除自身）
        existing = User.query.filter_by(username=username).first()
        if existing and existing.id != user.id:
            errors.append('该用户名已被使用。')
        existing = User.query.filter_by(email=email).first()
        if existing and existing.id != user.id:
            errors.append('该邮箱已被注册。')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('user_form.html', user=user)

        user.username = username
        user.email = email
        user.role = role
        user.is_active = is_active
        if password:
            user.set_password(password)

        db.session.commit()
        flash(f'用户 {username} 已更新。', 'success')
        return redirect(url_for('admin.user_list'))

    return render_template('user_form.html', user=user)


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def user_delete(user_id: int):
    """删除用户（不能删除自己）"""
    if user_id == session.get('user_id'):
        flash('不能删除当前登录的管理员账户。', 'danger')
        return redirect(url_for('admin.user_list'))

    user = db.session.get(User, user_id)
    if user is None:
        flash('用户不存在。', 'danger')
        return redirect(url_for('admin.user_list'))

    username = user.username
    db.session.delete(user)
    db.session.commit()

    flash(f'用户 {username} 已被删除。', 'info')
    return redirect(url_for('admin.user_list'))
