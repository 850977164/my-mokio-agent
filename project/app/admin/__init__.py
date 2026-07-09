"""管理后台蓝图

提供仪表盘和用户管理功能。
"""

from flask import Blueprint

admin_bp = Blueprint(
    'admin',
    __name__,
    url_prefix='/admin',
    template_folder='../templates',
)

from app.admin import routes  # noqa: E402, F401
