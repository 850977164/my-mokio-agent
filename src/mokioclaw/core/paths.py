"""工作区路径工具 —— 解析、创建、安全检查."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace(workspace: str | Path | None) -> Path:
    """解析命令行传入的 workspace，未提供则自动生成默认路径。

    Args:
        workspace: 用户指定的工作区路径，可为 None。

    Returns:
        解析后的绝对路径。
    """
    if workspace is None:
        # 默认在项目根目录下创建 .mokioclaw/workspaces/<timestamp>
        cwd = Path.cwd()
        workspace = cwd / ".mokioclaw" / "workspaces" / "default"
    return Path(workspace).resolve()


def ensure_workspace(workspace: Path) -> Path:
    """确保工作区目录存在，不存在则创建。

    Args:
        workspace: 待确保的路径。

    Returns:
        已存在的 workspace 路径。
    """
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def safe_path(workspace: Path, file_path: str | Path) -> Path:
    """路径安全检查 —— 确保目标路径在 workspace 内。

    将相对路径拼接到 workspace 后解析为绝对路径，
    再验证结果是否以 workspace 为前缀。

    Args:
        workspace: 工作区根目录（已 resolve 的绝对路径）。
        file_path: 用户请求的文件路径。

    Returns:
        安全解析后的绝对路径。

    Raises:
        ValueError: 路径逃逸出 workspace 范围。
    """
    workspace = workspace.resolve()
    target = (workspace / file_path).resolve()

    # 确保 target 以 workspace 为前缀（防止 ../ 逃逸）
    try:
        target.relative_to(workspace)
    except ValueError:
        raise ValueError(
            f"路径逃逸: '{file_path}' 解析后 '{target}' 不在 workspace "
            f"'{workspace}' 内"
        )
    return target
