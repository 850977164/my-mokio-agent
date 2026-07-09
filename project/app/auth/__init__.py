"""认证蓝图

提供用户注册、登录、登出功能。
"""

from flask import Blueprint

auth_bp = Blueprint(
    'auth',
    __name__,
    url_prefix='/auth',
    template_folder='../templates',
)

from app.auth import routes  # noqa: E402, F401
