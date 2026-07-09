"""Flask 配置模块

从 .env 文件读取环境变量，提供配置类。
"""

import os
from pathlib import Path

basedir = Path(__file__).resolve().parent.parent


class Config:
    """基础配置"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-dev-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL',
        f'sqlite:///{basedir / "instance" / "app.db"}'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
