from flask import Blueprint, render_template
from flask_login import login_required, current_user

main = Blueprint('main', __name__)


@main.route('/')
def index():
    return render_template('index.html')


@main.route('/dashboard')
@login_required
def dashboard():
    # 模拟统计数据
    stats = {
        'total_users': 128,
        'active_sessions': 37,
        'daily_visits': 2560,
        'monthly_revenue': '¥89,420'
    }
    return render_template('dashboard.html', stats=stats)
