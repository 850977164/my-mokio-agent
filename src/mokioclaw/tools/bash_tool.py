"""BashTool —— 在 workspace 内执行 shell 命令，带超时控制与命令风险审批."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import StructuredTool

from mokioclaw.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    classify_command_risk,
    normalize_approval_mode,
)


# ═══════════════════════════════════════════════════════════════════
# 结果数据类
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BashResult:
    """_run_bash 的执行结果."""
    output: str
    ok: bool = True
    requires_approval: bool = False
    risk_reason: str | None = None


def _format_result_for_llm(result: BashResult) -> str:
    """将 BashResult 格式化为 LLM 可读的字符串."""
    if not result.ok:
        header = f"[审批拒绝] 命令被拒绝执行: {result.risk_reason or '未知风险'}\n"
        return header + f"命令: {result.output}"
    if result.requires_approval:
        header = f"[自动批准] 命令已自动放行 (风险: {result.risk_reason})\n"
        return header + result.output
    return result.output


# ═══════════════════════════════════════════════════════════════════
# 核心执行函数
# ═══════════════════════════════════════════════════════════════════

def _run_bash(
    command: str,
    timeout_seconds: int = 120,
    *,
    workspace: Path,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
) -> BashResult:
    """在 workspace 目录中执行 shell 命令，含风险审批。

    Args:
        command: 要执行的 shell 命令。
        timeout_seconds: 超时秒数，默认 120。
        workspace: 工作区根目录。
        approval_mode: 审批模式 "inline" | "auto" | "deny"。
        approval_handler: inline 模式下的审批回调，接收 ApprovalRequest 返回 ApprovalDecision。

    Returns:
        BashResult 包含输出、是否成功、是否需要审批等元信息。
    """
    risk_reason = classify_command_risk(command)

    # 无风险命令直接执行
    if risk_reason is None:
        output = _execute_command(command, timeout_seconds, workspace)
        return BashResult(output=output, ok=True)

    # 有风险命令按模式处理
    mode = normalize_approval_mode(approval_mode)

    if mode == "deny":
        return BashResult(
            output=command,
            ok=False,
            risk_reason=risk_reason,
        )

    if mode == "auto":
        output = _execute_command(command, timeout_seconds, workspace)
        return BashResult(
            output=output,
            ok=True,
            requires_approval=True,
            risk_reason=risk_reason,
        )

    # mode == "inline"
    if approval_handler is None:
        # 没有审批处理器时降级为 deny，避免无人值守时执行风险命令
        return BashResult(
            output=command,
            ok=False,
            risk_reason=f"{risk_reason}（inline 模式下缺少 approval_handler，已拒绝）",
        )

    request = ApprovalRequest.create(command=command, risk_reason=risk_reason)
    decision = approval_handler(request)

    if not decision.approved:
        return BashResult(
            output=command,
            ok=False,
            risk_reason=f"{risk_reason}（用户拒绝: {decision.reason}）",
        )

    output = _execute_command(command, timeout_seconds, workspace)
    return BashResult(
        output=output,
        ok=True,
        risk_reason=risk_reason,
    )


def _execute_command(
    command: str,
    timeout_seconds: int,
    workspace: Path,
) -> str:
    """执行 shell 命令并返回 stdout + stderr 组合输出."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired:
        return f"命令超时 ({timeout_seconds}s): {command}"

    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"

    output += f"\n[exit code: {result.returncode}]"
    return output


# ═══════════════════════════════════════════════════════════════════
# 工具工厂
# ═══════════════════════════════════════════════════════════════════

def create_bash_tool(
    *,
    workspace: Path,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
) -> StructuredTool:
    """创建 BashTool 实例。

    Args:
        workspace: 工作区根目录。
        approval_mode: 审批模式 "inline" | "auto" | "deny"（默认 inline）。
        approval_handler: inline 模式下的审批回调。
    """
    def _tool_func(command: str, timeout_seconds: int = 120) -> str:
        result = _run_bash(
            command,
            timeout_seconds,
            workspace=workspace,
            approval_mode=approval_mode,
            approval_handler=approval_handler,
        )
        return _format_result_for_llm(result)

    return StructuredTool.from_function(
        func=_tool_func,
        name="Bash",
        description=(
            "在 workspace 目录内执行 shell 命令。"
            "参数: command (命令字符串), timeout_seconds (超时秒数, 默认120)。"
        ),
    )
