from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'
login_manager.login_message_category = 'warning'


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    db.init_app(app)
    login_manager.init_app(app)

    from app.auth import auth_bp
    from app.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    with app.app_context():
        from app import models  # noqa: F401
        db.create_all()

    return app
