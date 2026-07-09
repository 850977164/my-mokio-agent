"""RuntimeState —— 持有本次会话的运行状态."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest


@dataclass
class RuntimeState:
    """MokioClaw 运行时状态。

    所有工具通过 state 访问 workspace，操作限制在此目录内。
    """

    workspace: Path = field(default_factory=Path.cwd)
    """工作区根目录，所有文件/命令操作均限制在此范围内。"""

    model: str = field(default_factory=lambda: os.getenv("MODEL", "gpt-4o"))
    """当前使用的模型名称，默认从环境变量 MODEL 读取。"""

    checkpoint_mode: str = "light"
    """检查点模式: "light" | "strict" | "off"。"light" 只在节点切换时保存轻量检查点。"strict" 在每个事件后都追加保存完整 state。"""

    trace_mode: str = "on"
    """追踪模式: "on" | "off"。on 时记录所有事件到 .mokioclaw/traces/{trace_id}/ 目录。"""

    trace_id: str = ""
    """追踪 ID，为空时自动生成。用于在 traces 目录下创建子目录隔离多次运行。"""

    approval_mode: str = "inline"
    """审批模式: "inline" | "auto" | "deny"。inline 需要回调，auto 自动放行，deny 直接拒绝。"""

    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = field(
        default=None, compare=False, repr=False,
    )
    """inline 模式下的审批回调，接收 ApprovalRequest 返回 ApprovalDecision。"""

    resume_from: Path | None = field(default=None, compare=False, repr=False)
    """恢复工作区路径，非 None 时从该路径的检查点恢复运行。"""
