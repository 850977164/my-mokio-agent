"""RuntimeState —— 持有本次会话的运行状态."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RuntimeState:
    """MokioClaw 运行时状态。

    所有工具通过 state 访问 workspace，操作限制在此目录内。
    """

    workspace: Path = field(default_factory=Path.cwd)
    """工作区根目录，所有文件/命令操作均限制在此范围内。"""

    model: str = field(default_factory=lambda: os.getenv("MODEL", "gpt-4o"))
    """当前使用的模型名称，默认从环境变量 MODEL 读取。"""
