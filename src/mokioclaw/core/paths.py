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
        cwd = Path.cwd().resolve()

        # 如果 cwd 已经是某个 workspace 目录（包含 .mokioclaw 标记），
        # 直接使用 cwd，避免路径嵌套。
        if (cwd / ".mokioclaw").is_dir():
            return cwd

        # 查找项目根目录（往上找包含 pyproject.toml 或 .git 的目录）
        project_root = _find_project_root(cwd)
        workspace = project_root / ".mokioclaw" / "workspaces" / "default"
    return Path(workspace).resolve()


def _find_project_root(start: Path) -> Path:
    """从 start 开始向上查找项目根目录。

    搜索标记: pyproject.toml 或 .git 目录。
    """
    current = start
    for _ in range(10):  # 最多向上找 10 层
        if (current / "pyproject.toml").is_file() or (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:  # 已到文件系统根
            break
        current = parent
    # 回退到 start 本身
    return start


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
