import os
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_required, current_user

db = SQLAlchemy()
login_manager = LoginManager()


def create_app(config_class=None):
    """Application factory for the Flask admin system."""
    app = Flask(__name__)

    # Default configuration
    if config_class is None:
        from app.config import Config
        config_class = Config

    app.config.from_object(config_class)

    # Ensure instance directory exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '请先登录以访问此页面。'
    login_manager.login_message_category = 'warning'

    # User loader callback
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Register blueprints
    from app.auth import auth_bp
    from app.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # Root and /dashboard routes
    @app.route('/')
    @app.route('/dashboard')
    @login_required
    def dashboard():
        from app.models import User
        user_count = User.query.count()
        return render_template('dashboard.html',
                               title='仪表盘',
                               user=current_user,
                               user_count=user_count)

    # Create database tables
    with app.app_context():
        db.create_all()

    return app


app = create_app()
