"""BashTool —— 在 workspace 内执行 shell 命令，带超时控制."""

from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import StructuredTool


def _run_bash(
    command: str,
    timeout_seconds: int = 120,
    *,
    workspace: Path,
) -> str:
    """在 workspace 目录中执行 shell 命令。

    Args:
        command: 要执行的 shell 命令。
        timeout_seconds: 超时秒数，默认 120。
        workspace: 工作区根目录。

    Returns:
        命令的 stdout + stderr 组合输出。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,        # 捕获输出：等同于 stdout=PIPE, stderr=PIPE，把标准输出和标准错误都捕获到内存中
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired:
        return f"命令超时 ({timeout_seconds}s): {command}"

    output = result.stdout    # stdout（标准输出）作为主体内容
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"

    output += f"\n[exit code: {result.returncode}]"
    return output


def create_bash_tool(*, workspace: Path) -> StructuredTool:
    """创建 BashTool 实例。"""
    return StructuredTool.from_function(
        func=lambda command, timeout_seconds=120: _run_bash(
            command, timeout_seconds, workspace=workspace,
        ),
        name="Bash",
        description=(
            "在 workspace 目录内执行 shell 命令。"
            "参数: command (命令字符串), timeout_seconds (超时秒数, 默认120)。"
        ),
    )
