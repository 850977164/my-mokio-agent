"""命令风险分类与审批机制."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import uuid4

# ═══════════════════════════════════════════════════════════════════
# 风险命令正则分类
# ═══════════════════════════════════════════════════════════════════

RISK_PATTERNS: list[tuple[str, str]] = [
    (r"(?:^|&&|\|\||;)\s*(?:python\s+-m\s+)?pip\s+install\b", "Python package installation"),
    (r"(?:^|&&|\|\||;)\s*uv\s+add\b", "Project dependency change with uv add"),
    (r"(?:^|&&|\|\||;)\s*uv\s+sync\b", "Dependency synchronization with uv sync"),
    (r"(?:^|&&|\|\||;)\s*uv\s+pip\s+install\b", "Python package installation with uv pip"),
    (r"(?:^|&&|\|\||;)\s*npm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*pnpm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*yarn\s+(?:install\b|add\b)", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*(?:curl|wget)\b", "Network download command"),
    (r"(?:^|&&|\|\||;)\s*uvicorn\b", "Long-running development server"),
    (r"(?:^|&&|\|\||;)\s*python\s+-m\s+http\.server\b", "Long-running development server"),
]


def classify_command_risk(command: str) -> str | None:
    """匹配则返回风险原因字符串，否则返回 None（安全命令）."""
    for pattern, reason in RISK_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


# ═══════════════════════════════════════════════════════════════════
# 审批请求和决策数据类
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ApprovalRequest:
    """审批请求 —— 包含命令及其风险信息."""
    id: str
    command: str
    risk_reason: str
    tool_name: str = "BashTool"

    @classmethod
    def create(cls, command: str, risk_reason: str) -> ApprovalRequest:
        """工厂方法：生成带唯一 ID 的审批请求."""
        return cls(
            id=f"approval-{uuid4().hex[:8]}",
            command=command,
            risk_reason=risk_reason,
        )


@dataclass(frozen=True)
class ApprovalDecision:
    """审批决策结果."""
    approved: bool
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════
# 审批模式
# ═══════════════════════════════════════════════════════════════════

VALID_APPROVAL_MODES: set[str] = {"inline", "auto", "deny"}


def normalize_approval_mode(mode: str | None) -> str:
    """默认 "inline"，无效值也 fallback 到 "inline"。"""
    if mode is None or mode not in VALID_APPROVAL_MODES:
        return "inline"
    return mode
