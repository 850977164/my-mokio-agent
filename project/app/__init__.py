"""Flask 应用工厂

使用 create_app() 模式创建和配置 Flask 应用。
"""

from flask import Flask
from dotenv import load_dotenv

# 加载 .env 文件（必须在读取配置之前）
load_dotenv()

from app.config import Config
from app.extensions import db


def create_app(config_class=Config) -> Flask:
    """创建并配置 Flask 应用

    Args:
        config_class: 配置类，默认使用 Config。

    Returns:
        配置完成的 Flask 应用实例。
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 初始化扩展
    db.init_app(app)

    # 注册蓝图
    from app.auth import auth_bp
    from app.admin import admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # 根路由重定向到仪表盘
    from flask import redirect, url_for
    @app.route('/')
    def index():
        from flask import session
        if 'user_id' in session:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('auth.login'))

    # 创建数据库表
    with app.app_context():
        db.create_all()

    return app
