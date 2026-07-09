"""认证路由

处理用户注册、登录、登出。
"""

from flask import render_template, request, redirect, url_for, session, flash
from app.extensions import db
from app.models import User
from app.auth import auth_bp


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('请输入用户名和密码。', 'danger')
            return render_template('login.html')

        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash('用户名或密码错误。', 'danger')
            return render_template('login.html')

        if not user.is_active:
            flash('该账户已被禁用，请联系管理员。', 'warning')
            return render_template('login.html')

        # 设置 session
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        session.permanent = True

        flash(f'欢迎回来，{user.username}！', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """用户注册"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        # 输入校验
        errors = []
        if not username or len(username) < 3:
            errors.append('用户名至少需要3个字符。')
        if not email or '@' not in email:
            errors.append('请输入有效的邮箱地址。')
        if not password or len(password) < 6:
            errors.append('密码至少需要6个字符。')
        if password != password_confirm:
            errors.append('两次输入的密码不一致。')

        # 检查唯一性
        if User.query.filter_by(username=username).first():
            errors.append('该用户名已被使用。')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已被注册。')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('register.html')

        # 第一个注册的用户自动成为管理员
        is_first_user = User.query.count() == 0

        user = User(username=username, email=email)
        user.set_password(password)
        if is_first_user:
            user.role = 'admin'

        db.session.add(user)
        db.session.commit()

        flash('注册成功！请登录。', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth_bp.route('/logout')
def logout():
    """用户登出"""
    session.clear()
    flash('您已安全登出。', 'info')
    return redirect(url_for('auth.login'))
