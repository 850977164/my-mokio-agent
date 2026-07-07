"""GrepTool —— 正则搜索文件内容."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import StructuredTool

from mokioclaw.core.paths import safe_path


def _grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    head_limit: int = 250,
    ignore_case: bool = False,
    *,
    workspace: Path,
) -> str:
    """在 workspace 内递归搜索匹配 pattern 的文件行。

    Args:
        pattern: 正则表达式。
        path: 搜索起始路径（相对于 workspace），默认为 "."。
        glob: 文件名过滤 glob 模式，如 "*.py"。
        head_limit: 最大输出行数（截断）。
        ignore_case: 是否忽略大小写。
        workspace: 工作区根目录。

    Returns:
        匹配行列表，格式: ``file_path:line_num:content``。
    """
    search_root = safe_path(workspace, path)

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"正则表达式错误: {e}"

    # 收集匹配的文件
    if glob:
        files = list(search_root.rglob(glob))
    else:
        files = list(search_root.rglob("*"))

    results = []
    for fpath in files:
        if not fpath.is_file():
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                rel = fpath.relative_to(workspace)
                results.append(f"{rel}:{lineno}:{line}")
                if len(results) >= head_limit:
                    break
        if len(results) >= head_limit:
            break

    if not results:
        return f"未找到匹配 '{pattern}' 的内容"

    output = "\n".join(results)
    if len(results) >= head_limit:
        output += f"\n... (已截断，共 {head_limit} 条)"
    return output


def create_grep_tool(*, workspace: Path) -> StructuredTool:
    """创建 GrepTool 实例。"""
    return StructuredTool.from_function(
        func=lambda pattern, path=".", glob=None, head_limit=250,
                      ignore_case=False: _grep(
            pattern, path, glob, head_limit, ignore_case, workspace=workspace,
        ),
        name="Grep",
        description=(
            "在 workspace 内正则搜索文件内容。"
            "参数: pattern (正则), path (搜索路径, 默认'.'), "
            "glob (文件过滤如'*.py'), head_limit (最大条数, 默认250), "
            "ignore_case (忽略大小写, 默认False)。"
        ),
    )
