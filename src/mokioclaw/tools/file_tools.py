"""FileReadTool / FileWriteTool / FileEditTool —— 文件操作工具."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool

from mokioclaw.core.paths import safe_path


def _read_file(
    file_path: str,
    offset: int = 0,
    limit: int | None = None,
    *,
    workspace: Path,
) -> str:
    """读取文件内容。

    Args:
        file_path: 相对于 workspace 的文件路径。
        offset: 起始行号（0-based），默认第 0 行。
        limit: 最大读取行数，None 表示读取全部。
        workspace: 工作区根目录（通过 bind 注入）。

    Returns:
        文件内容字符串，带行号前缀。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 路径安全检查失败。
    """
    target = safe_path(workspace, file_path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {target}")

    with open(target, encoding="utf-8") as f:
        lines = f.readlines()

    if offset > 0:
        lines = lines[offset:]
    if limit is not None:
        lines = lines[:limit]

    # 带行号输出，方便后续编辑定位
    result = []
    start_num = offset + 1
    for i, line in enumerate(lines):
        result.append(f"{start_num + i}\t{line.rstrip()}")

    return "\n".join(result)


def create_file_read_tool(*, workspace: Path) -> StructuredTool:
    """创建 FileReadTool 实例。

    Args:
        workspace: 工作区根目录。

    Returns:
        绑定 workspace 的 StructuredTool。
    """
    return StructuredTool.from_function(
        func=lambda file_path, offset=0, limit=None: _read_file(
            file_path, offset, limit, workspace=workspace,
        ),
        name="FileRead",
        description=(
            "读取文件内容，返回带行号的文本。"
            "参数: file_path (相对路径), offset (起始行, 0-based, 默认0), "
            "limit (最大行数, 可选)。"
        ),
    )


def _write_file(
    file_path: str,
    content: str,
    *,
    workspace: Path,
) -> str:
    """创建或覆写文件。

    Args:
        file_path: 相对于 workspace 的文件路径。
        content: 要写入的内容。
        workspace: 工作区根目录。

    Returns:
        操作结果描述。
    """
    target = safe_path(workspace, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    action = "已覆写" if target.exists() else "已创建"
    target.write_text(content, encoding="utf-8")
    return f"{action}文件: {target.relative_to(workspace)}"


def create_file_write_tool(*, workspace: Path) -> StructuredTool:
    """创建 FileWriteTool 实例。"""
    return StructuredTool.from_function(
        func=lambda file_path, content: _write_file(
            file_path, content, workspace=workspace,
        ),
        name="FileWrite",
        description=(
            "创建或覆写文件。"
            "参数: file_path (相对路径), content (文件内容)。"
        ),
    )


def _edit_file(
    file_path: str,
    old_text: str,
    new_text: str,
    *,
    workspace: Path,
) -> str:
    """精确替换文件中的唯一文本片段。

    Args:
        file_path: 相对于 workspace 的文件路径。
        old_text: 待替换的原文本（必须在文件中唯一出现）。
        new_text: 替换后的新文本。
        workspace: 工作区根目录。

    Returns:
        操作结果描述。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: old_text 在文件中出现 0 次或多次。
    """
    target = safe_path(workspace, file_path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {target}")

    content = target.read_text(encoding="utf-8")
    occurrences = content.count(old_text)

    if occurrences == 0:
        raise ValueError(
            f"未找到匹配文本: old_text 在文件中不存在"
        )
    if occurrences > 1:
        raise ValueError(
            f"匹配到 {occurrences} 处文本: old_text 必须唯一匹配，"
            f"请提供更多上下文使匹配唯一"
        )

    new_content = content.replace(old_text, new_text, 1)
    target.write_text(new_content, encoding="utf-8")
    return f"已编辑文件: {target.relative_to(workspace)} (替换 1 处)"


def create_file_edit_tool(*, workspace: Path) -> StructuredTool:
    """创建 FileEditTool 实例。"""
    return StructuredTool.from_function(
        func=lambda file_path, old_text, new_text: _edit_file(
            file_path, old_text, new_text, workspace=workspace,
        ),
        name="FileEdit",
        description=(
            "精确替换文件中的唯一文本片段。"
            "参数: file_path (相对路径), old_text (原文本, 必须唯一匹配), "
            "new_text (替换文本)。"
        ),
    )
