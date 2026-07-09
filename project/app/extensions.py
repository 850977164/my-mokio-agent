"""Flask 扩展

集中管理所有 Flask 扩展实例，避免循环引用。
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
